from contextlib import closing
from io import BytesIO
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid
import zipfile
import asyncio
import subprocess
import sys
import httpx
from alembic import command
from alembic.config import Config
from backend.tests.async_support import AsyncAPITestCase
from backend.app.core.config import Settings,PROJECT_ROOT
from backend.app.core.database import connection,HubError,SCHEMA_REVISION
from backend.app.core.note_files import NoteFiles,file_lock,digest
from backend.app.core.note_snapshot import verified_snapshot
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate,NoteEdit
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskCreate
from backend.app.main import create_app
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from scripts.data_safety import audit_database,create_backup,SafetyError


def key():return str(uuid.uuid4())


class NoteFacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.service=NoteService(self.settings.database,self.settings.data_root)
    def tearDown(self):self.temp.cleanup()
    def create(self,**extra):
        return self.service.create(NoteCreate(**{'title':'虚构笔记','body':'# 实验\n\n普通内容\n',**extra}),key())[0]
    def change(self,row,**extra):
        return self.service.change(row['id'],NoteEdit(**{'title':row['title'],'body':row['body'],'note_kind':row['note_kind'],
            'local_date':row['local_date'],'expected_version':row['version'],'expected_hash':row['content_sha256'],**extra}),key())[0]
    def raw(self,sql,args=()):
        with closing(sqlite3.connect(self.settings.database)) as con:
            result=con.execute(sql,args).fetchall();con.commit();return result
    def test_revision_loop_preserves_files_and_can_restore_old_content_as_new_revision(self):
        row=self.create();first_path=self.settings.data_root/row['body_path'];original=first_path.read_bytes()
        edited=self.change(row,title='新标题',body='新正文',note_kind='journal',local_date='2026-11-12')
        self.assertNotEqual(edited['body_path'],row['body_path']);self.assertEqual(first_path.read_bytes(),original)
        history=self.service.history(row['id']);self.assertEqual(history['total'],2)
        old=self.service.revision(row['id'],history['items'][1]['id'])
        restored=self.change(edited,title=old['title'],body=old['body'],note_kind=old['note_kind'],local_date=old['local_date'])
        self.assertEqual(restored['version'],3);self.assertEqual(restored['body'],row['body'])
        self.assertEqual(NoteService(self.settings.database).get(row['id']),restored)
        self.assertEqual(first_path.stat().st_mode & 0o777,0o600)
        self.assertEqual(first_path.parent.stat().st_mode & 0o777,0o700)
        self.assertEqual(self.raw("SELECT DISTINCT state FROM file_operations"),[('committed',)])

    def test_create_and_edit_response_loss_are_idempotent_with_global_keys(self):
        request=key();payload=NoteCreate(title='创建重试',body='正文')
        row,_=self.service.create(payload,request);again,replayed=self.service.create(payload,request)
        self.assertEqual(row,again);self.assertTrue(replayed)
        with self.assertRaises(HubError):self.service.create(NoteCreate(title='另内容'),request)
        edit=NoteEdit(title='修改一次',body='新版',expected_version=1,expected_hash=row['content_sha256']);request=key()
        updated,_=self.service.change(row['id'],edit,request);again,replayed=self.service.change(row['id'],edit,request)
        self.assertEqual(updated,again);self.assertTrue(replayed)
        self.assertEqual(self.service.history(row['id'])['total'],2)
        self.assertEqual(self.raw('SELECT count(*) FROM file_operations'),[(2,)])
        with self.assertRaises(HubError):TaskService(self.settings.database).create(TaskCreate(title='跨域key'),request)

    def test_stale_version_or_hash_refuses_without_touching_files(self):
        row=self.create();updated=self.change(row,body='最新')
        before=NoteFiles(self.settings.data_root).inventory()
        with self.assertRaises(HubError) as error:self.change(row,body='过期')
        self.assertEqual(error.exception.code,'VERSION_CONFLICT')
        with self.assertRaises(HubError) as error:self.change(updated,expected_hash='0'*64)
        self.assertEqual(error.exception.code,'NOTE_HASH_CONFLICT')
        self.assertEqual(before,NoteFiles(self.settings.data_root).inventory())
        self.assertEqual(self.service.get(row['id'])['body'],'最新')

    def test_external_changes_are_never_overwritten_and_reads_do_not_repair(self):
        row=self.create();path=self.settings.data_root/row['body_path'];path.write_text('外部修改')
        before=audit_database(self.settings.database)
        self.assertEqual(self.service.list()['items'][0]['storage_state'],'conflict')
        with self.assertRaises(HubError) as error:self.service.get(row['id'])
        self.assertEqual(error.exception.code,'NOTE_FILE_CONFLICT')
        with self.assertRaises(HubError):self.change(row,body='不应覆盖')
        with self.assertRaises(HubError):self.service.export()
        self.assertEqual(path.read_text(),'外部修改');self.assertEqual(audit_database(self.settings.database),before)
        path.unlink()
        self.assertEqual(self.service.list()['items'][0]['storage_state'],'missing')
        with self.assertRaises(HubError) as error:self.service.get(row['id'])
        self.assertEqual(error.exception.code,'NOTE_FILE_MISSING')

    def test_missing_parent_directory_is_reported_without_creating_replacement(self):
        row=self.create();path=self.settings.data_root/row['body_path'];path.unlink();path.parent.rmdir()
        with self.assertRaises(HubError) as error:self.service.get(row['id'])
        self.assertEqual(error.exception.code,'NOTE_FILE_MISSING');self.assertFalse(path.parent.exists())

    def test_commit_failure_preserves_previous_content_and_explicit_reconcile_only_marks(self):
        row=self.create();before=self.service.get(row['id'])
        with mock.patch('backend.app.modules.library.notes.service.activity',side_effect=RuntimeError('fault after file')):
            with self.assertRaises(RuntimeError):self.change(row,body='未提交正文')
        self.assertEqual(self.service.get(row['id']),before)
        self.assertEqual(self.service.history(row['id'])['total'],1)
        inventory=NoteFiles(self.settings.data_root).inventory();self.assertEqual(len(inventory),2)
        audit=audit_database(self.settings.database)
        report=self.service.diagnose();self.assertEqual(report['prepared'],1);self.assertEqual(report['orphaned'],1)
        self.assertEqual(audit_database(self.settings.database),audit)
        report=self.service.diagnose(True);self.assertEqual(report['orphaned'],1)
        self.assertEqual(NoteFiles(self.settings.data_root).inventory(),inventory)
        self.assertEqual(self.raw("SELECT count(*) FROM file_operations WHERE state='orphaned'"),[(1,)])
        saved=self.change(row,body='安全重试');self.assertEqual(saved['version'],2)

    def test_create_failure_after_file_write_never_publishes_partial_identity(self):
        with mock.patch('backend.app.modules.library.notes.service.record',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.create()
        self.assertEqual(self.service.list()['total'],0)
        for table in ['resources','notes','note_revisions','activities','operation_requests']:
            self.assertEqual(self.raw('SELECT count(*) FROM '+table),[(0,)])
        self.assertEqual(self.service.diagnose()['prepared'],1)
        self.assertEqual(len(NoteFiles(self.settings.data_root).inventory()),1)

    def test_file_write_and_fsync_failure_leave_durable_intent_without_losing_saved_note(self):
        row=self.create()
        with mock.patch('backend.app.core.note_files.os.fsync',side_effect=OSError('disk error')):
            with self.assertRaises(OSError):self.change(row,body='中断保存')
        self.assertEqual(self.service.get(row['id'])['body'],row['body'])
        self.assertEqual(self.service.diagnose()['prepared'],1)
        self.assertEqual(self.service.diagnose(True)['revision_issues'],0)

    def test_file_after_link_before_db_failure_is_recoverable_and_not_deleted(self):
        row=self.create();real=self.service.files.write_revision
        def fail(*args):real(*args);raise RuntimeError('crash after link')
        with mock.patch.object(self.service.files,'write_revision',side_effect=fail):
            with self.assertRaises(RuntimeError):self.change(row,body='另一正文')
        self.assertEqual(self.service.get(row['id'])['body'],row['body'])
        self.assertEqual(self.service.diagnose()['orphaned'],1)

    def test_symlink_traversal_nonregular_and_unsafe_paths_are_refused(self):
        row=self.create();path=self.settings.data_root/row['body_path']
        other=self.root/'outside';other.write_text('不能读取');other.chmod(0o600)
        original=path.read_bytes();path.unlink();path.symlink_to(other)
        with self.assertRaises(HubError):self.service.get(row['id'])
        self.assertEqual(other.read_text(),'不能读取')
        path.unlink();path.write_bytes(original);path.chmod(0o600)
        parent=path.parent;new_parent=parent.with_name(parent.name+'-saved');parent.rename(new_parent);parent.symlink_to(new_parent,target_is_directory=True)
        with self.assertRaises(HubError):self.service.get(row['id'])
        for value in ['../outside','/etc/passwd','notes/../../outside','notes/x/../x','notes\\outside']:
            with self.assertRaises(HubError):self.service.files.read(value)

    def test_pending_unsafe_file_is_marked_conflict_without_following_or_deleting(self):
        with mock.patch.object(self.service.files,'write_revision',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.create()
        final=self.raw("SELECT final_path FROM file_operations WHERE state='prepared'")[0][0]
        destination=self.settings.data_root/final;destination.parent.mkdir(mode=0o700,parents=True)
        target=self.root/'external';target.write_text('外部');destination.symlink_to(target)
        self.assertEqual(self.service.diagnose(True)['conflict'],1);self.assertTrue(destination.is_symlink());self.assertEqual(target.read_text(),'外部')

    def test_privacy_and_soft_delete_cover_history_markdown_export_and_idempotent_replay(self):
        request=key();row,_=self.service.create(NoteCreate(title='不应泄漏',body='私密哨兵'),request)
        revision_id=self.service.history(row['id'])['items'][0]['id']
        trashed=self.service.trash(row['id'],1)
        self.assertEqual(self.service.list()['total'],0);self.assertEqual(self.service.list('trash')['total'],1)
        with self.assertRaises(HubError):self.service.create(NoteCreate(title='不应泄漏',body='私密哨兵'),request)
        self.service.trash(row['id'],2,True)
        self.raw("UPDATE resources SET privacy_level='private' WHERE id=?",(row['id'],))
        for action in [lambda:self.service.get(row['id']),lambda:self.service.history(row['id']),lambda:self.service.revision(row['id'],revision_id),lambda:self.service.markdown(row['id']),lambda:self.service.trash(row['id'],3)]:
            with self.assertRaises(HubError):action()
        self.assertEqual(self.service.list()['total'],0)
        with zipfile.ZipFile(BytesIO(self.service.export())) as bundle:
            self.assertEqual(bundle.namelist(),['notes.json']);self.assertNotIn('私密哨兵',bundle.read('notes.json').decode())

    def test_export_zip_preserves_metadata_markdown_and_deleted_revisions(self):
        row=self.create();self.change(row,title='第二版',body='第二稿')
        self.service.trash(row['id'],2)
        with zipfile.ZipFile(BytesIO(self.service.export())) as bundle:
            metadata=json.loads(bundle.read('notes.json'))
            self.assertEqual(len(metadata['notes']),1);self.assertEqual(len(metadata['revisions']),2)
            self.assertIsNotNone(metadata['notes'][0]['deleted_at'])
            for rev in metadata['revisions']:self.assertEqual(digest(bundle.read(rev['body_path'])),rev['content_sha256'])
            self.assertEqual(len(metadata['activities']),3)

    def test_process_crash_after_file_publication_preserves_intent_and_previous_revision(self):
        row=self.create()
        script="""
import os,sys,uuid
from pathlib import Path
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteEdit
service=NoteService(Path(sys.argv[1]));row=service.get(sys.argv[2]);original=service.files.write_revision

def abrupt(*args):
    original(*args)
    os._exit(23)
service.files.write_revision=abrupt
service.change(row['id'],NoteEdit(title=row['title'],body='crash-draft',expected_version=row['version'],expected_hash=row['content_sha256']),str(uuid.uuid4()))
"""
        result=subprocess.run([sys.executable,'-B','-c',script,str(self.settings.database),row['id']],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,23,result.stderr.decode())
        self.assertEqual(self.service.get(row['id'])['body'],row['body'])
        self.assertEqual(self.service.diagnose()['orphaned'],1)
        self.assertEqual(self.service.diagnose(True)['prepared'],1)
        self.assertEqual(self.change(row,body='restart-save')['version'],2)

    def test_partial_full_restore_keeps_marker_and_refuses_startup(self):
        self.create();output=self.root/'backup';backup_hub(self.settings,output)
        from backend.app.core import note_snapshot
        original=note_snapshot.copy_database;calls=0
        def fail(source,target):
            nonlocal calls
            calls+=1;original(source,target)
            raise RuntimeError('failure after runtime db copied')
        restored=self.root/'partial'
        with mock.patch.object(note_snapshot,'copy_database',side_effect=fail):
            with self.assertRaises(RuntimeError):restore_hub(self.settings,output,restored)
        self.assertTrue((restored/'HUB_INCOMPLETE.json').exists())
        from backend.app.main import database_status
        self.assertEqual(database_status(restored/'db'/'hub.db'),'unavailable')
        with self.assertRaises(HubError):NoteService(restored/'db'/'hub.db').list()

    def test_snapshot_symlink_parent_and_public_note_directory_are_refused(self):
        row=self.create();link=self.root/'alias';link.symlink_to(self.root,target_is_directory=True)
        with self.assertRaises(HubError):backup_hub(self.settings,link/'unsafe-output')
        self.assertFalse((self.root/'unsafe-output').exists())
        (self.settings.data_root/'notes').chmod(0o755)
        with self.assertRaises(HubError):self.service.get(row['id'])
        self.assertEqual(self.service.list()['items'][0]['storage_state'],'conflict')

    def test_file_lock_contention_refuses_instead_of_duplicate_or_hanging(self):
        with file_lock(self.settings.data_root):
            with self.assertRaises(HubError) as error:self.create()
            self.assertEqual(error.exception.code,'NOTE_FILES_BUSY')
        self.assertEqual(self.service.list()['total'],0)

    def test_backup_restore_all_files_and_orphans_into_runnable_data_root(self):
        row=self.create();self.change(row,body='第二稿')
        with mock.patch('backend.app.modules.library.notes.service.activity',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.create(body='未提交也应保留')
        inventory=NoteFiles(self.settings.data_root).inventory()
        before=audit_database(self.settings.database)
        output=self.root/'snapshot';backup_hub(self.settings,output)
        self.assertEqual(NoteFiles(output).inventory(),inventory)
        restored=self.root/'restored';restore_hub(self.settings,output,restored)
        self.assertEqual(audit_database(restored/'db'/'hub.db'),before)
        self.assertEqual(NoteFiles(restored).inventory(),inventory)
        self.assertEqual(NoteService(restored/'db'/'hub.db').get(row['id'])['body'],'第二稿')
        self.assertEqual(NoteService(restored/'db'/'hub.db').diagnose()['prepared'],1)
        with self.assertRaises(SafetyError):restore_hub(self.settings,output,restored)

    def test_full_snapshot_marker_is_required_for_note_revision_even_when_empty(self):
        output=self.root/'sqlite-only';create_backup(self.settings.database,output)
        with self.assertRaises(HubError) as error:restore_hub(self.settings,output,self.root/'bad-restore')
        self.assertEqual(error.exception.code,'NOTE_SNAPSHOT_INCOMPLETE');self.assertFalse((self.root/'bad-restore').exists())

    def test_tampered_missing_extra_and_traversal_snapshot_files_refuse_restore(self):
        row=self.create()
        for variant in ['tamper','missing','extra','traversal','symlink','duplicate']:
            output=self.root/variant;backup_hub(self.settings,output)
            path=output/row['body_path'];marker=output/'hub-files.json'
            if variant=='tamper':path.write_text('篡改')
            elif variant=='missing':path.unlink()
            elif variant=='extra':extra=path.with_name('extra.md');extra.write_text('额外');extra.chmod(0o600)
            elif variant=='symlink':path.unlink();path.symlink_to(self.settings.data_root/row['body_path'])
            elif variant=='duplicate':marker.write_text(marker.read_text().replace('"state": "verified",','"state": "verified","state": "verified",'))
            else:
                metadata=json.loads(marker.read_text());metadata['files']['../escape']={'sha256':'0'*64,'size':0};marker.write_text(json.dumps(metadata))
            destination=self.root/(variant+'-restored')
            with self.assertRaises(HubError):restore_hub(self.settings,output,destination)
            self.assertFalse(destination.exists())

    def test_external_edit_during_snapshot_is_detected_and_full_marker_not_published(self):
        row=self.create();path=self.settings.data_root/row['body_path']
        from backend.app.core import note_snapshot
        original=note_snapshot.copy_file
        def modify(*args):original(*args);path.write_text('备份中途外改')
        with mock.patch.object(note_snapshot,'copy_file',side_effect=modify):
            with self.assertRaises(HubError):backup_hub(self.settings,self.root/'changed')
        self.assertFalse((self.root/'changed'/'hub-files.json').exists())
        with self.assertRaises(HubError):verified_snapshot(self.root/'changed')


class NoteAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.app=create_app(self.settings);self.life=self.app.router.lifespan_context(self.app);await self.life.__aenter__()
        self.origin='http://127.0.0.1:8765'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,raise_app_exceptions=False),base_url=self.origin)
        login=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':self.origin})
        self.headers={'Origin':self.origin,'X-CSRF-Token':login.json()['csrf_token']}
    async def asyncTearDown(self):
        await self.client.aclose();await self.life.__aexit__(None,None,None);self.temp.cleanup()
    async def test_api_crud_history_markdown_zip_and_session_boundary(self):
        headers={**self.headers,'Idempotency-Key':key()}
        response=await self.client.post('/api/v1/notes',json={'title':'普通笔记','body':'# Markdown\n<script>不执行</script>'},headers=headers)
        self.assertEqual(response.status_code,201,response.text);row=response.json()
        self.assertEqual((await self.client.get('/api/v1/notes')).json()['total'],1)
        saved=await self.client.put('/api/v1/notes/'+row['id'],json={'title':'修改','body':'第二稿','expected_version':1,'expected_hash':row['content_sha256']},headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(saved.status_code,200,saved.text)
        history=(await self.client.get('/api/v1/notes/'+row['id']+'/revisions')).json();self.assertEqual(history['total'],2)
        old=await self.client.get(f"/api/v1/notes/{row['id']}/revisions/{history['items'][1]['id']}")
        self.assertIn('<script>',old.json()['body'])
        markdown=await self.client.get('/api/v1/notes/'+row['id']+'/markdown')
        self.assertEqual(markdown.text,'第二稿');self.assertIn('attachment',markdown.headers['content-disposition'])
        exported=await self.client.get('/api/v1/notes/export');self.assertEqual(exported.status_code,200);self.assertEqual(exported.headers['content-type'],'application/zip')
        self.assertEqual((await self.client.post('/api/v1/notes/'+row['id']+'/trash',json={'expected_version':2},headers=self.headers)).status_code,200)
        self.assertEqual((await self.client.get('/api/v1/notes/'+row['id'])).status_code,404)
        self.assertEqual((await self.client.post('/api/v1/notes/'+row['id']+'/restore',json={'expected_version':3},headers=self.headers)).status_code,200)
        self.assertEqual((await self.client.post('/api/v1/notes',json={'title':'x'},headers={'Origin':self.origin})).status_code,403)
        self.client.cookies.clear()
        for path in ['/api/v1/notes','/api/v1/notes/export','/api/v1/notes/'+row['id'],'/api/v1/notes/'+row['id']+'/markdown','/api/v1/notes/'+row['id']+'/revisions']:
            self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_validation_forbids_private_paths_and_invalid_text_without_echo(self):
        for fields in [{'title':' '},{'body_path':'/etc/passwd'},{'privacy_level':'private'},{'note_kind':'private_journal'},
            {'title':'\ud800'},{'note_kind':'treehole'},{'local_date':'2026-02-30'},{'body':'\ud800'},{'body':'x'*200001}]:
            response=await self.client.post('/api/v1/notes',content=json.dumps({'title':'secret-sentinel',**fields}),headers={**self.headers,'Idempotency-Key':key(),'Content-Type':'application/json'})
            self.assertEqual(response.status_code,422,response.text[:200]);self.assertNotIn('secret-sentinel',response.text)
        self.assertEqual((await self.client.get('/api/v1/notes?limit=101')).status_code,422)
        self.assertEqual((await self.client.get('/api/v1/notes?scope=private')).status_code,422)

    async def test_note_write_lock_parallel_requests_safe_retry_after_contention(self):
        headers={**self.headers,'Idempotency-Key':key()};payload={'title':'并发提交','body':'一个事实'}
        responses=await asyncio.gather(*[self.client.post('/api/v1/notes',json=payload,headers=headers) for _ in range(3)])
        self.assertTrue(any(r.status_code in [200,201] for r in responses))
        for result in responses:self.assertIn(result.status_code,[200,201,409],result.text)
        response=await self.client.post('/api/v1/notes',json=payload,headers=headers)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((await self.client.get('/api/v1/notes')).json()['total'],1)


class NoteMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        with connection(self.settings.database,write=True,require_current=False,enforce_foreign_keys=False) as con:
            config=Config(str(PROJECT_ROOT/'backend'/'alembic.ini'));config.attributes['connection']=con;command.upgrade(config,'0002_events_reminders')
            con.exec_driver_sql("INSERT INTO resources VALUES ('t','task','任务',NULL,'personal','2026','2026',NULL,1)")
            con.exec_driver_sql("INSERT INTO tasks(resource_id,status,priority,timezone,position) VALUES ('t','todo',2,'UTC',0)")
            con.exec_driver_sql("INSERT INTO resources VALUES ('e','event','事件',NULL,'personal','2026','2026',NULL,1)")
            con.exec_driver_sql("INSERT INTO events(resource_id,is_all_day,start_date,end_date,timezone,status,task_id) VALUES ('e',1,'2026-11-11','2026-11-12','UTC','scheduled','t')")
            con.exec_driver_sql("INSERT INTO reminders VALUES ('m','e','2026','pending',NULL,NULL,'key','2026','2026',1)")
    def tearDown(self):self.temp.cleanup()
    def test_populated_task_event_reminder_migration_preserves_every_original_row(self):
        before=audit_database(self.settings.database);upgrade(self.settings);after=audit_database(self.settings.database)
        for name,value in before['tables'].items():
            if name!='alembic_version':self.assertEqual(after['tables'][name],value)
        self.assertEqual(after['foreign_key_errors'],0)
        NoteService(self.settings.database).create(NoteCreate(title='升级后笔记'),key())
    def test_real_note_ddl_failure_rolls_back_parent_copy_and_new_tables(self):
        before=audit_database(self.settings.database);original=command.upgrade;calls=0
        def fail(config,target):
            nonlocal calls
            calls+=1;original(config,target)
            if calls==2:raise RuntimeError('after new notes DDL')
        with mock.patch('backend.maintenance.command.upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(calls,2);self.assertEqual(audit_database(self.settings.database),before)
    def test_unknown_expression_index_cannot_be_silently_lost(self):
        with connection(self.settings.database,write=True,require_current=False) as con:
            con.exec_driver_sql('CREATE INDEX custom_title_lower ON resources(lower(title))')
        before=audit_database(self.settings.database)
        with self.assertRaises(HubError) as error:upgrade(self.settings)
        self.assertEqual(error.exception.code,'MIGRATION_SCHEMA_OBJECT_MISMATCH')
        self.assertEqual(audit_database(self.settings.database),before)

    def test_legacy_sqlite_only_backup_restore_is_still_supported(self):
        backup=self.root/'legacy-backup';create_backup(self.settings.database,backup)
        result=restore_hub(self.settings,backup,self.root/'legacy-restored')
        self.assertEqual(result['revision'],'0002_events_reminders')
        self.assertEqual(audit_database(self.settings.database),audit_database(self.root/'legacy-restored'/'database.sqlite3'))
