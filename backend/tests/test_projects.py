from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from io import BytesIO
import json
import sqlite3
import unittest
from unittest.mock import patch
import zipfile
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from pydantic import ValidationError
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from backend.app.core.config import Settings, PROJECT_ROOT
from backend.app.core.database import connection, HubError, SCHEMA_REVISION, revision
from backend.app.core.note_snapshot import SnapshotFiles, verified_snapshot
from backend.app.modules.projects.service import ProjectService
from backend.app.modules.projects.schemas import (ProjectWrite, Version, MilestoneWrite,
    MilestoneVersion, ProjectTaskCreate, TaskLink, EntryCreate, EntryLink)
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskCreate, TaskPatch
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate
from backend.app.modules.knowledge.service import KnowledgeService
from backend.app.modules.knowledge.router import Relation
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.knowledge.export import portable_export
from backend.app.modules.tags.service import TagService
from backend.app.modules.tags.schemas import TagAssignment
from backend.app.modules.dashboard.service import DashboardService
from backend.app.modules.dashboard.schemas import default_preferences
from backend.app.main import create_app
from scripts.data_safety import audit_database
from backend.tests.async_support import AsyncAPITestCase


def key():
    return str(uuid4())


class Projects(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'unused',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.s=ProjectService(self.settings.database)
        self.p=self.s.save(ProjectWrite(title='Malware Analysis Lab',status='active',objective='产出可复核的分析报告'),key())

    def tearDown(self):
        self.tmp.cleanup()

    def version(self):
        return self.s.get(self.p['id'])['version']

    def milestone(self):
        return self.s.milestone_save(self.p['id'],MilestoneWrite(title='PE实验',expected_version=self.version()),key())

    def task(self, **fields):
        return self.s.task_create(self.p['id'],ProjectTaskCreate(title='独立分析导入表',expected_version=self.version(),**fields),key())

    def test_parallel_assignment_preserves_single_owner_without_partial_parent_updates(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        other=self.s.save(ProjectWrite(title='第二项目'),key())
        task=TaskService(self.settings.database).create(TaskCreate(title='共同候选任务'),key())[0]
        barrier=Barrier(2)
        def assign(pid):
            barrier.wait(timeout=5)
            try:
                self.s.task_attach(pid,TaskLink(task_id=task['id'],expected_version=1),key())
                return pid,'saved'
            except HubError as e:
                return pid,e.code
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(assign,[self.p['id'],other['id']]))
        self.assertEqual(sorted(code for _,code in results),['TASK_ALREADY_ASSIGNED','saved'])
        for pid,code in results:
            self.assertEqual(self.s.get(pid)['version'],2 if code=='saved' else 1)
            self.assertEqual(self.s.task_list(pid)['total'],1 if code=='saved' else 0)

    def test_full_project_workflow_progress_notes_archive_restore_and_original_directory_untouched(self):
        directory=self.root/'source';directory.mkdir();original=directory/'main.py';original.write_text('print("fixture")')
        self.s.save(ProjectWrite(title='Malware Analysis Lab',status='active',local_directory=str(directory),repository_url='https://github.com/example/lab',expected_version=1),key(),self.p['id'])
        m=self.milestone();task=self.task(milestone_id=m['id'],planned_date='2026-11-12')
        self.assertEqual(TaskService(self.settings.database).list(scope='today',on_date='2026-11-12')['total'],1)
        self.assertEqual(self.s.get(self.p['id'])['progress']['done'],0)
        TaskService(self.settings.database).change(task['id'],TaskPatch(expected_version=1,status='done'))
        self.assertEqual(self.s.get(self.p['id'])['progress']['percent'],100)
        self.assertEqual(self.s.milestones(self.p['id'])['items'][0]['status'],'todo')
        entry=self.s.entry_create(self.p['id'],EntryCreate(title='实验分析',body='## 复核步骤\n只分析虚构样例。',entry_kind='experiment',expected_version=self.version()),key())
        self.assertEqual(NoteService(self.settings.database).get(entry['note_id'])['note_kind'],'experiment')
        self.assertEqual(self.s.directory_check(self.p['id'])['status'],'available')
        self.s.save(ProjectWrite(title='Malware Analysis Lab',status='archived',local_directory=str(directory),expected_version=self.version()),key(),self.p['id'])
        with self.assertRaises(HubError) as error:self.task()
        self.assertEqual(error.exception.code,'PROJECT_ARCHIVED')
        self.s.trash(self.p['id'],Version(expected_version=self.version()),key())
        self.assertEqual(self.s.list()['total'],0)
        TaskService(self.settings.database).get(task['id']);NoteService(self.settings.database).get(entry['note_id'])
        self.s.trash(self.p['id'],Version(expected_version=7),key(),True)
        self.assertEqual(self.s.get(self.p['id'])['status'],'archived')
        self.assertEqual(original.read_text(),'print("fixture")')

    def test_existing_task_single_owner_milestone_composite_fk_and_unlink_preserves_task(self):
        task=TaskService(self.settings.database).create(TaskCreate(title='旧任务'),key())[0]
        other=self.s.save(ProjectWrite(title='另一个项目'),key());m=self.milestone()
        payload=TaskLink(task_id=task['id'],milestone_id=m['id'],expected_version=self.version());request=key()
        self.s.task_attach(self.p['id'],payload,request);self.s.task_attach(self.p['id'],payload,request)
        with self.assertRaises(HubError) as e:self.s.task_attach(other['id'],TaskLink(task_id=task['id'],expected_version=1),key())
        self.assertEqual(e.exception.code,'TASK_ALREADY_ASSIGNED')
        foreign_task=TaskService(self.settings.database).create(TaskCreate(title='独立任务'),key())[0]
        with self.assertRaises(HubError):self.s.task_attach(other['id'],TaskLink(task_id=foreign_task['id'],milestone_id=m['id'],expected_version=1),key())
        with self.assertRaises(Exception):
            with connection(self.settings.database,write=True) as c:c.exec_driver_sql('INSERT INTO project_tasks VALUES(?,?,?,?,?)',(key(),other['id'],foreign_task['id'],m['id'],'2026-11-12'))
        k=KnowledgeService(self.settings.database);edge=next(e for e in k.context(task['id'])['relations'] if e['relation_type']=='project_task')
        self.assertEqual(edge['resource_id'],self.p['id'])
        k.unlink(self.p['id'],edge['id'],self.version())
        self.assertEqual(self.s.task_list(self.p['id'])['total'],0);TaskService(self.settings.database).get(task['id'])
        self.s.task_attach(other['id'],TaskLink(task_id=task['id'],expected_version=1),key())
        self.assertEqual(self.s.task_list(other['id'])['total'],1)

    def test_progress_excludes_cancelled_deleted_private_and_no_tasks_is_unknown(self):
        self.assertIsNone(self.s.get(self.p['id'])['progress']['percent'])
        done=self.task();cancelled=self.task();deleted=self.task();private=self.task()
        tasks=TaskService(self.settings.database)
        tasks.change(done['id'],TaskPatch(expected_version=1,status='done'))
        tasks.change(cancelled['id'],TaskPatch(expected_version=1,status='cancelled'))
        tasks.trash(deleted['id'],1)
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(private['id'],))
        self.assertEqual(self.s.get(self.p['id'])['progress']['total'],1)
        self.assertEqual(self.s.get(self.p['id'])['progress']['percent'],100)
        self.assertEqual(self.s.task_list(self.p['id'])['total'],2)

    def test_milestone_versions_soft_delete_restore_and_status_are_independent(self):
        m=self.milestone();task=self.task(milestone_id=m['id'])
        with self.assertRaises(HubError):self.s.milestone_save(self.p['id'],MilestoneWrite(title='旧版本',expected_version=self.version(),expected_milestone_version=9),key(),m['id'])
        self.s.milestone_trash(self.p['id'],m['id'],MilestoneVersion(expected_version=self.version(),expected_milestone_version=1),key())
        self.assertIsNone(self.s.task_list(self.p['id'])['items'][0]['milestone_id'])
        self.assertEqual(self.s.get(self.p['id'])['progress']['total'],1)
        self.s.milestone_trash(self.p['id'],m['id'],MilestoneVersion(expected_version=self.version(),expected_milestone_version=2),key(),True)
        self.assertEqual(self.s.task_list(self.p['id'])['items'][0]['milestone_id'],m['id'])
        self.s.milestone_save(self.p['id'],MilestoneWrite(title='已验收',status='done',expected_version=self.version(),expected_milestone_version=3),key(),m['id'])
        self.assertEqual(TaskService(self.settings.database).get(task['id'])['status'],'todo')

    def test_entry_idempotency_roles_projection_unlink_and_recoverable_conflict(self):
        payload=EntryCreate(title='项目复盘',body='输出与下一步',entry_kind='summary',expected_version=1);request=key()
        first=self.s.entry_create(self.p['id'],payload,request);self.assertEqual(self.s.entry_create(self.p['id'],payload,request),first)
        self.assertEqual(self.s.entries(self.p['id'])['total'],1)
        self.s.entry_attach(self.p['id'],EntryLink(note_id=first['note_id'],entry_kind='log',expected_version=self.version()),key())
        edge=next(e for e in KnowledgeService(self.settings.database).context(first['note_id'])['relations'] if e['relation_type']=='log')
        KnowledgeService(self.settings.database).unlink(self.p['id'],edge['id'],self.version())
        self.assertEqual(self.s.entries(self.p['id'])['total'],0);NoteService(self.settings.database).get(first['note_id'])
        note_create=NoteService.create
        def concurrent_save(notes,payload,child_key):
            result=note_create(notes,payload,child_key)
            self.s.save(ProjectWrite(title='已被另一页修改',expected_version=self.version()),key(),self.p['id'])
            return result
        v=self.version();before=len(SnapshotFiles(self.settings.data_root).inventory())
        with patch.object(NoteService,'create',concurrent_save):
            with self.assertRaises(HubError):self.s.entry_create(self.p['id'],EntryCreate(title='保留普通笔记',body='关联发生冲突',expected_version=v),key())
        self.assertEqual(len(SnapshotFiles(self.settings.data_root).inventory()),before+1)
        self.assertEqual(self.s.entries(self.p['id'])['total'],0)

    def test_hidden_project_and_targets_never_leak_context_search_tags_export_or_recent(self):
        task=self.task();entry=self.s.entry_create(self.p['id'],EntryCreate(title='公开实验',body='正文',expected_version=self.version()),key())
        self.s.opened(self.p['id'])
        tags=TagService(self.settings.database);tags.assign(self.p['id'],TagAssignment(names=['仅项目标签'],expected_version=self.version(),expected_tag_versions={}),key())
        search=SearchService(self.settings.database);search.rebuild();self.assertEqual(search.query('Malware Analysis Lab')['total'],1)
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(self.p['id'],))
        self.assertEqual(self.s.list(recent=True)['total'],0);self.assertEqual(tags.list()['total'],0);self.assertEqual(search.query('Malware Analysis Lab')['total'],0)
        self.assertEqual(KnowledgeService(self.settings.database).context(task['id'])['relations'],[])
        with self.assertRaises(HubError):self.s.entries(self.p['id'])
        exported=zipfile.ZipFile(BytesIO(portable_export(self.settings.database))).read('data.json').decode()
        self.assertNotIn(self.p['id'],exported);self.assertNotIn('仅项目标签',exported)
        self.assertIn(task['id'],exported);self.assertIn(entry['note_id'],exported)

    def test_hidden_task_note_not_exposed_through_project_views_or_exports(self):
        task=self.task();entry=self.s.entry_create(self.p['id'],EntryCreate(title='不泄漏的实验',body='普通正文',expected_version=self.version()),key())
        with connection(self.settings.database,write=True) as c:
            for rid in (task['id'],entry['note_id']):c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(rid,))
        self.assertEqual(self.s.task_list(self.p['id'])['items'],[]);self.assertEqual(self.s.entries(self.p['id'])['items'],[])
        self.assertEqual(KnowledgeService(self.settings.database).context(self.p['id'])['relations'],[])
        exported=zipfile.ZipFile(BytesIO(portable_export(self.settings.database))).read('data.json').decode()
        self.assertNotIn(task['id'],exported);self.assertNotIn(entry['note_id'],exported)
        with self.assertRaises(HubError):self.s.entry_attach(self.p['id'],EntryLink(note_id=entry['note_id'],expected_version=self.version()),key())

    def test_validation_directory_metadata_no_follow_or_execution_and_missing_path(self):
        for path in ('relative/path','/','/tmp/../etc','/tmp//a','/tmp/x/','/tmp/a\x00b','/tmp/a\\b'):
            with self.assertRaises(ValidationError):ProjectWrite(title='测试',local_directory=path)
        for url in ('javascript:alert(1)','https://user:pass@example.invalid','file:///tmp'):
            with self.assertRaises(ValidationError):ProjectWrite(title='测试',repository_url=url)
        with self.assertRaises(ValidationError):ProjectWrite(title='测试',start_date='2026-11-13',target_date='2026-11-12')
        target=self.root/'target';target.mkdir();link=self.root/'link';link.symlink_to(target,target_is_directory=True)
        for path,state in [(str(link),'unavailable_or_unsafe'),(str(target/'missing'),'missing'),(str(target),'available')]:
            self.s.save(ProjectWrite(title='测试',local_directory=path,expected_version=self.version()),key(),self.p['id'])
            with patch('subprocess.Popen',side_effect=AssertionError('must not execute')):
                self.assertEqual(self.s.directory_check(self.p['id'])['status'],state)

    def test_project_cas_same_key_current_facts_transaction_rollback_and_unknown_revision(self):
        payload=ProjectWrite(title='保存修改',expected_version=1);request=key()
        saved=self.s.save(payload,request,self.p['id']);self.assertEqual(self.s.save(payload,request,self.p['id'])['version'],saved['version'])
        with self.assertRaises(HubError):self.s.save(ProjectWrite(title='覆盖',expected_version=1),key(),self.p['id'])
        before=audit_database(self.settings.database)
        with patch('backend.app.modules.projects.service.record',side_effect=RuntimeError('fixture')):
            with self.assertRaises(RuntimeError):self.task()
        self.assertEqual(audit_database(self.settings.database),before)
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE alembic_version SET version_num='9999_unknown'")
        with self.assertRaises(HubError):self.s.list()

    def test_complete_restore_and_portable_export_preserve_nonempty_project_graph(self):
        m=self.milestone();task=self.task(milestone_id=m['id'])
        entry=self.s.entry_create(self.p['id'],EntryCreate(title='复盘',body='## 实验结果',expected_version=self.version()),key())
        self.s.opened(self.p['id'])
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
        self.assertEqual(audit_database(self.root/'restored/db/hub.db'),before);self.assertEqual(SnapshotFiles(self.root/'restored').inventory(),files)
        restored=ProjectService(self.root/'restored/db/hub.db');self.assertEqual(restored.get(self.p['id']),self.s.get(self.p['id']))
        archive=zipfile.ZipFile(BytesIO(portable_export(self.settings.database)));data=json.loads(archive.read('data.json'))['tables']
        self.assertEqual(data['project_tasks'][0]['task_id'],task['id']);self.assertEqual(data['project_entries'][0]['note_id'],entry['note_id'])
        manifest=self.root/'backup/hub-files.json';meta=json.loads(manifest.read_text());meta['format_version']=2;manifest.write_text(json.dumps(meta))
        with self.assertRaises(HubError):verified_snapshot(self.root/'backup')

    def test_recent_is_explicit_and_old_dashboard_preferences_project_without_writing(self):
        before=audit_database(self.settings.database);self.s.get(self.p['id']);self.s.list();self.assertEqual(audit_database(self.settings.database),before)
        self.assertEqual(self.s.list(recent=True)['total'],0);v=self.version();self.s.opened(self.p['id']);self.assertEqual(self.version(),v)
        self.assertEqual(self.s.list(recent=True)['total'],1)
        prefs=default_preferences();old=prefs['widgets'][:8]
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("INSERT INTO dashboard_preferences VALUES('dashboard',1,?,?,1,?,?)",(json.dumps(old),json.dumps(prefs['shortcuts']),'2026-11-12','2026-11-12'))
        before=audit_database(self.settings.database);projected=DashboardService(self.settings.database).get()
        self.assertEqual(projected['widgets'][:8],old);self.assertEqual(projected['widgets'][8],{'id':'projects','enabled':False})
        self.assertEqual(audit_database(self.settings.database),before)


class ProjectsAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'unused',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings);self.app=create_app(self.settings)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1:8765')
        r=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':'http://127.0.0.1:8765'})
        self.headers={'Origin':'http://127.0.0.1:8765','X-CSRF-Token':r.json()['csrf_token']}

    async def asyncTearDown(self):
        await self.client.aclose();self.tmp.cleanup()

    async def test_api_project_task_entry_and_lifecycle_uuid_validation(self):
        headers={**self.headers,'Idempotency-Key':key()}
        r=await self.client.post('/api/v1/projects',json={'title':'API 项目'},headers=headers);self.assertEqual(r.status_code,200,r.text);p=r.json()
        again=await self.client.post('/api/v1/projects',json={'title':'API 项目'},headers=headers);self.assertEqual(again.json()['id'],p['id'])
        task=await self.client.post('/api/v1/projects/'+p['id']+'/tasks',json={'title':'API 任务','expected_version':1},headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(task.status_code,200,task.text)
        self.assertEqual((await self.client.get('/api/v1/projects/'+p['id']+'/tasks')).json()['total'],1)
        bad=await self.client.post('/api/v1/projects/'+p['id']+'/tasks/attach',json={'task_id':'not-a-uuid','expected_version':2},headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(bad.status_code,422)
        no_csrf=await self.client.post('/api/v1/projects',json={'title':'拒绝'},headers={'Origin':'http://127.0.0.1:8765','Idempotency-Key':key()});self.assertEqual(no_csrf.status_code,403)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/projects')).status_code,401)


class ProjectMigration(unittest.TestCase):
    def test_nonempty_0007_preserves_all_rows_custom_indexes_and_files(self):
        with TemporaryDirectory() as t:
            root=Path(t);s=Settings(data_root=root/'hub',legacy_database=root/'unused')
            initialize_empty(s.data_root)
            engine=create_engine('sqlite+pysqlite:///'+str(s.database),poolclass=NullPool)
            with engine.begin() as c:
                config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0007_phase2')
                c.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES('fixture','task','old','personal','2026-11-12','2026-11-12',1)")
                c.exec_driver_sql("INSERT INTO tasks(resource_id,status,priority,timezone,position) VALUES('fixture','todo',2,'Asia/Shanghai',0)")
                c.exec_driver_sql('CREATE INDEX ix_fixture_preserve ON resources(title,version)')
                c.exec_driver_sql("CREATE INDEX ix_fixture_partial ON resources(version) WHERE kind='task'")
                c.exec_driver_sql('CREATE INDEX ix_fixture_expression ON resources(lower(title))')
            engine.dispose();before=audit_database(s.database);result=upgrade(s)
            self.assertEqual(result['rehearsal'],'passed');after=audit_database(s.database)
            for name,fact in before['tables'].items():
                if name!='alembic_version':self.assertEqual(after['tables'][name],fact,name)
            self.assertEqual(revision(s.database),SCHEMA_REVISION)
            with connection(s.database) as c:self.assertTrue(c.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name='ix_fixture_preserve'").first())
            for name in ('projects','project_tasks','project_milestones','project_entries'):self.assertEqual(after['tables'][name]['row_count'],0)

    def test_failed_0008_ddl_rolls_back_all_existing_facts_and_revision(self):
        with TemporaryDirectory() as t:
            root=Path(t);s=Settings(data_root=root/'hub',legacy_database=root/'unused');initialize_empty(s.data_root)
            engine=create_engine('sqlite+pysqlite:///'+str(s.database),poolclass=NullPool)
            with engine.begin() as c:
                config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0007_phase2')
            engine.dispose();before=audit_database(s.database)
            from sqlalchemy.engine import Connection
            execute=Connection.exec_driver_sql
            def failing(c,statement,*args,**kwargs):
                if 'CREATE TABLE project_tasks' in statement:raise RuntimeError('injected DDL failure')
                return execute(c,statement,*args,**kwargs)
            with patch.object(Connection,'exec_driver_sql',failing):
                with self.assertRaises(RuntimeError):upgrade(s)
            self.assertEqual(audit_database(s.database),before);self.assertEqual(revision(s.database),'0007_phase2')
