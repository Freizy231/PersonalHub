"""Real SQLite/files with synthetic installations; never starts desktop tools."""
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock,patch
from uuid import uuid4
import json
import os
import subprocess
import unittest
import zipfile
import httpx
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
from sqlalchemy.engine import Connection
from pydantic import ValidationError
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from backend.app.core.config import Settings,PROJECT_ROOT
from backend.app.core.database import connection,HubError,SCHEMA_REVISION,revision
from backend.app.core.note_snapshot import SnapshotFiles,verified_snapshot
from backend.app.main import create_app
from backend.app.modules.projects import tools
from backend.app.modules.projects.tools import ProjectToolService,ToolProfile,ToolState,ToolPreview,ToolConfirm
from backend.app.modules.projects.schemas import ProjectWrite
from backend.app.modules.knowledge.export import portable_export
from backend.tests.async_support import AsyncAPITestCase
from scripts.data_safety import audit_database

def key():return str(uuid4())

class ToolFixture:
    def setup_facts(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'unused',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.bin=self.root/'bin';self.bin.mkdir(mode=0o700)
        self.programs=[]
        for n in ('terminal','bash','code','codex'):
            p=self.bin/n;p.write_text('#!/bin/sh\nexit 0\n');p.chmod(0o700);self.programs.append(p)
        self.catalog={'terminal':[(str(self.programs[0]),str(self.programs[1]))],
            'vscode':[(str(self.programs[2]),)],'codex':[(str(self.programs[0]),str(self.programs[3]))]}
        self.catalog_patch=patch.object(tools,'CANDIDATES',self.catalog);self.catalog_patch.start()
        self.directory=self.root/'lab;$(not-a-command)';self.directory.mkdir(mode=0o700)
        self.original=self.directory/'facts.txt';self.original.write_text('synthetic fixture')
        self.s=ProjectToolService(self.settings.database)
        self.project=self.s.save(ProjectWrite(title='Malware Lab',local_directory=str(self.directory),status='active'),key())
        self.profile=self.add_profile('terminal')
    def cleanup_facts(self):self.catalog_patch.stop();self.tmp.cleanup()
    def add_profile(self,adapter):
        i=next(i for i in self.s.profiles()['installations'] if i['adapter']==adapter)
        return self.s.profile_save(ToolProfile(name=adapter,adapter=adapter,installation_id=i['installation_id']),key())
    def preview(self,profile=None,operation=None):
        return self.s.preview(ToolPreview(project_id=self.project['id'],profile_id=(profile or self.profile)['id'],expected_project_version=self.s.get(self.project['id'])['version']),operation or key())
    def error(self,code,fn):
        with self.assertRaises(HubError) as e:fn()
        self.assertEqual(e.exception.code,code)

class ProjectTools(ToolFixture,unittest.TestCase):
    def setUp(self):self.setup_facts()
    def tearDown(self):self.cleanup_facts()

    def test_catalog_get_has_no_registration_or_process_side_effect_and_strict_schema(self):
        before=audit_database(self.settings.database)
        with patch.object(tools.subprocess,'Popen') as spawn:
            result=self.s.profiles();self.assertEqual(len(result['installations']),3);self.assertEqual(len(result['items']),1);spawn.assert_not_called()
        self.assertEqual(audit_database(self.settings.database),before)
        for value in (False,1,'true',None):
            with self.assertRaises(ValidationError):ToolConfirm(confirmed=value)
        self.assertTrue(ToolConfirm(confirmed=True).confirmed)
        with self.assertRaises(ValidationError):ToolProfile(name='bad',adapter='terminal',installation_id='0'*64,executable='/bin/sh',arguments=['-c','touch fixture'])
        with self.assertRaises(ValidationError):ToolPreview(project_id='bad',profile_id=key(),expected_project_version=1)

    def test_profile_idempotency_cas_uniqueness_disable_missing_and_rebind(self):
        i=self.s.profiles()['installations'][0];payload=ToolProfile(name='renamed',adapter='terminal',installation_id=i['installation_id'],expected_version=1);operation=key()
        saved=self.s.profile_save(payload,operation,self.profile['id']);again=self.s.profile_save(payload,operation,self.profile['id']);self.assertEqual(saved,again)
        self.error('VERSION_CONFLICT',lambda:self.s.profile_save(payload,key(),self.profile['id']))
        self.error('TOOL_PROFILE_EXISTS',lambda:self.s.profile_save(payload,key()))
        self.programs[0].write_text('#!/bin/sh\nexit 1\n')
        self.assertFalse(next(p for p in self.s.profiles()['items'] if p['id']==saved['id'])['available'])
        disabled=self.s.profile_state(saved['id'],ToolState(expected_version=2,enabled=False),key());self.assertEqual(disabled['enabled'],0)
        self.error('TOOL_INSTALLATION_CHANGED',lambda:self.s.profile_save(ToolProfile(name='old',adapter='terminal',installation_id=i['installation_id'],expected_version=3),key(),saved['id']))
        new=next(i for i in self.s.profiles()['installations'] if i['adapter']=='terminal')
        rebound=self.s.profile_save(ToolProfile(name='new',adapter='terminal',installation_id=new['installation_id'],expected_version=3),key(),saved['id']);self.assertEqual(rebound['version'],4)

    def test_preview_is_idempotent_version_bound_no_spawn_or_project_version_change(self):
        operation=key();v=self.s.get(self.project['id'])['version']
        with patch.object(tools.subprocess,'Popen') as spawn:
            first=self.preview(operation=operation);again=self.preview(operation=operation);self.assertEqual(first,again);spawn.assert_not_called()
        self.assertEqual(self.s.get(self.project['id'])['version'],v)
        self.assertEqual(self.s.history()['total'],0)
        p=ToolPreview(project_id=self.project['id'],profile_id=self.profile['id'],expected_project_version=999)
        self.error('VERSION_CONFLICT',lambda:self.s.preview(p,key()))

    def test_confirm_once_no_shell_safe_environment_pinned_cwd_and_literal_path(self):
        preview=self.preview();process=Mock();process.wait.return_value=0
        def opened(*args,**kwargs):
            descriptor=kwargs['pass_fds'][0]
            self.assertEqual(os.readlink(kwargs['cwd']),str(self.directory))
            self.assertEqual(os.fstat(descriptor).st_ino,self.directory.stat().st_ino)
            return process
        with patch.dict(os.environ,{'GITHUB_TOKEN':'synthetic-secret','LD_PRELOAD':'untrusted','BASH_ENV':'untrusted','PYTHONPATH':'untrusted'}),patch.object(tools.subprocess,'Popen',side_effect=opened) as spawn:
            first=self.s.confirm(preview['id']);again=self.s.confirm(preview['id']);self.assertEqual(first,again);self.assertEqual(spawn.call_count,1)
            args=spawn.call_args.args[0];kwargs=spawn.call_args.kwargs
            self.assertEqual(args,preview['arguments']);self.assertIn('--working-directory='+str(self.directory),args);self.assertFalse(kwargs['shell'])
            self.assertEqual(args[-2:],['--noprofile','--norc']);self.assertEqual(kwargs['stdin'],subprocess.DEVNULL);self.assertTrue(kwargs['start_new_session'])
            self.assertTrue(kwargs['pass_fds']);self.assertTrue(kwargs['cwd'].startswith('/proc/self/fd/'))
            self.assertFalse({'GITHUB_TOKEN','LD_PRELOAD','BASH_ENV','PYTHONPATH'}&kwargs['env'].keys())
        self.assertEqual(first['status'],'started');self.assertEqual(self.original.read_text(),'synthetic fixture')
        self.assertEqual(self.s.history(self.project['id'])['total'],1)

    def test_parallel_duplicate_confirmation_has_at_most_one_spawn(self):
        preview=self.preview();process=Mock(wait=Mock(return_value=0))
        with patch.object(tools.subprocess,'Popen',return_value=process) as spawn,ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:self.s.confirm(preview['id']),range(2)))
        self.assertEqual(spawn.call_count,1);self.assertEqual(len({r['id'] for r in results}),1);self.assertTrue(all(r['status'] in ('started','uncertain') for r in results))

    def test_expiry_project_profile_and_disabled_changes_block_spawn(self):
        preview=self.preview()
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE project_tool_previews SET expires_at='2000-01-01T00:00:00Z' WHERE id=?",(preview['id'],))
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('TOOL_PREVIEW_EXPIRED',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()
        preview=self.preview();self.s.profile_state(self.profile['id'],ToolState(expected_version=1,enabled=False),key())
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('TOOL_PREVIEW_EXPIRED',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()
        self.s.profile_state(self.profile['id'],ToolState(expected_version=2,enabled=True),key());preview=self.preview()
        self.s.save(ProjectWrite(title='changed',local_directory=str(self.directory),expected_version=1),key(),self.project['id'])
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('TOOL_PREVIEW_EXPIRED',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()

    def test_directory_replacement_symlink_missing_and_unconfigured_block(self):
        preview=self.preview();moved=self.root/'moved';self.directory.rename(moved);self.directory.mkdir(mode=0o700)
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('TOOL_PREVIEW_CHANGED',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()
        self.directory.rmdir();self.directory.symlink_to(moved,target_is_directory=True)
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('PROJECT_DIRECTORY_UNAVAILABLE',lambda:self.preview());spawn.assert_not_called()
        self.directory.unlink();self.error('PROJECT_DIRECTORY_UNAVAILABLE',lambda:self.preview())
        self.s.save(ProjectWrite(title='no directory',expected_version=1),key(),self.project['id'])
        self.error('PROJECT_DIRECTORY_REQUIRED',lambda:self.preview())

    def test_binary_changed_symlink_retargeted_or_unsafe_permissions_block(self):
        link=self.bin/'codex-link';link.symlink_to(self.programs[3]);self.catalog['codex']=[(str(self.programs[0]),str(link))]
        p=self.add_profile('codex');preview=self.preview(p);link.unlink();link.symlink_to(self.programs[1])
        with patch.object(tools.subprocess,'Popen') as spawn:self.error('TOOL_INSTALLATION_CHANGED',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()
        self.programs[0].chmod(0o777)
        self.assertFalse(any(i['adapter']=='terminal' for i in self.s.profiles()['installations']))

    def test_fixed_vscode_and_codex_plans_no_automatic_prompt_or_bypass(self):
        code=self.preview(self.add_profile('vscode'));self.assertIn('--disable-extensions',code['arguments']);self.assertEqual(code['arguments'][-2:],['--',str(self.directory)])
        codex=self.preview(self.add_profile('codex'));args=codex['arguments']
        self.assertIn('--sandbox',args);self.assertEqual(args[args.index('--sandbox')+1],'read-only');self.assertEqual(args[args.index('--ask-for-approval')+1],'on-request');self.assertEqual(args[-1],'--no-daemon')
        self.assertFalse(any(a in args for a in ('exec','--dangerously-bypass-approvals-and-sandbox','--dangerously-bypass-hook-trust','--approve-for-me')))

    def test_failure_early_exit_timeout_and_crash_never_repeat_side_effect(self):
        for outcome,code in ((OSError('synthetic'),'TOOL_START_FAILED'),(Mock(wait=Mock(return_value=5)),'TOOL_EXITED')):
            preview=self.preview()
            options={'side_effect':outcome} if isinstance(outcome,Exception) else {'return_value':outcome}
            with patch.object(tools.subprocess,'Popen',**options) as spawn:
                result=self.s.confirm(preview['id']);self.assertEqual(result['status'],'failed');self.assertEqual(result['error_code'],code);self.s.confirm(preview['id']);self.assertEqual(spawn.call_count,1)
        preview=self.preview();process=Mock();process.wait.side_effect=[subprocess.TimeoutExpired('fixture',.25),0]
        with patch.object(tools.subprocess,'Popen',return_value=process):self.assertEqual(self.s.confirm(preview['id'])['status'],'started')
        preview=self.preview()
        with patch.object(tools.subprocess,'Popen',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.s.confirm(preview['id'])
        with patch.object(tools.subprocess,'Popen') as spawn:self.assertEqual(self.s.confirm(preview['id'])['status'],'uncertain');spawn.assert_not_called()

    def test_post_spawn_record_failure_remains_uncertain_no_reexecution(self):
        preview=self.preview();execute=Connection.exec_driver_sql
        def failing(c,statement,*args,**kwargs):
            if statement.startswith('UPDATE project_tool_history SET status='):raise RuntimeError('injected finalization failure')
            return execute(c,statement,*args,**kwargs)
        with patch.object(tools.subprocess,'Popen',return_value=Mock(wait=Mock(return_value=0))) as spawn,patch.object(Connection,'exec_driver_sql',failing):
            with self.assertRaises(RuntimeError):self.s.confirm(preview['id'])
            self.assertEqual(spawn.call_count,1)
        with patch.object(tools.subprocess,'Popen') as spawn:self.assertEqual(self.s.confirm(preview['id'])['status'],'uncertain');spawn.assert_not_called()

    def test_archive_trash_privacy_filter_history_replay_and_no_spawn(self):
        preview=self.preview()
        with patch.object(tools.subprocess,'Popen',return_value=Mock(wait=Mock(return_value=0))):self.s.confirm(preview['id'])
        self.s.save(ProjectWrite(title='archived',status='archived',local_directory=str(self.directory),expected_version=1),key(),self.project['id'])
        self.error('PROJECT_ARCHIVED',lambda:self.preview())
        with patch.object(tools.subprocess,'Popen') as spawn:self.assertEqual(self.s.confirm(preview['id'])['status'],'started');spawn.assert_not_called()
        for update in ("privacy_level='private'","privacy_level='personal',deleted_at='2026-11-12'"):
            with connection(self.settings.database,write=True) as c:c.exec_driver_sql('UPDATE resources SET '+update+' WHERE id=?',(self.project['id'],))
            self.assertEqual(self.s.history()['total'],0)
            with patch.object(tools.subprocess,'Popen') as spawn:self.error('RESOURCE_NOT_FOUND',lambda:self.s.confirm(preview['id']));spawn.assert_not_called()

    def test_full_backup_nonempty_launch_graph_restore_and_portable_exclusion(self):
        preview=self.preview()
        with patch.object(tools.subprocess,'Popen',return_value=Mock(wait=Mock(return_value=0))):self.s.confirm(preview['id'])
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
        self.assertEqual(audit_database(self.root/'restored/db/hub.db'),before);self.assertEqual(SnapshotFiles(self.root/'restored').inventory(),files)
        restored=ProjectToolService(self.root/'restored/db/hub.db')
        with patch.object(tools.subprocess,'Popen') as spawn:self.assertEqual(restored.confirm(preview['id'])['status'],'started');spawn.assert_not_called()
        archive=zipfile.ZipFile(BytesIO(portable_export(self.settings.database)));tables=json.loads(archive.read('data.json'))['tables']
        self.assertFalse({'project_tool_profiles','project_tool_previews','project_tool_history'}&tables.keys())
        meta=self.root/'backup/hub-files.json';d=json.loads(meta.read_text());d['format_version']=2;meta.write_text(json.dumps(d));self.error('NOTE_SNAPSHOT_INVALID',lambda:verified_snapshot(self.root/'backup'))

class ProjectToolsAPI(ToolFixture,AsyncAPITestCase):
    async def asyncSetUp(self):
        self.setup_facts();self.app=create_app(self.settings)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1:8765')
        r=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':'http://127.0.0.1:8765'})
        self.headers={'Origin':'http://127.0.0.1:8765','X-CSRF-Token':r.json()['csrf_token']}
    async def asyncTearDown(self):await self.client.aclose();self.cleanup_facts()
    async def test_api_csrf_uuid_explicit_confirmation_idempotence_and_history(self):
        r=await self.client.post('/api/v1/project-tools/preview',json={'project_id':self.project['id'],'profile_id':self.profile['id'],'expected_project_version':1},headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(r.status_code,200);pid=r.json()['id']
        for value in (False,1,'true'):
            r=await self.client.post('/api/v1/project-tools/confirm/'+pid,json={'confirmed':value},headers=self.headers);self.assertEqual(r.status_code,422)
        r=await self.client.post('/api/v1/project-tools/confirm/'+pid,json={'confirmed':True},headers={'Origin':'http://127.0.0.1:8765'});self.assertEqual(r.status_code,403)
        r=await self.client.post('/api/v1/project-tools/confirm/not-uuid',json={'confirmed':True},headers=self.headers);self.assertEqual(r.status_code,422)
        with patch.object(tools.subprocess,'Popen',return_value=Mock(wait=Mock(return_value=0))) as spawn:
            for _ in range(2):
                r=await self.client.post('/api/v1/project-tools/confirm/'+pid,json={'confirmed':True},headers=self.headers);self.assertEqual(r.status_code,200)
            self.assertEqual(spawn.call_count,1)
        self.assertEqual((await self.client.get('/api/v1/project-tools/history')).json()['total'],1)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/project-tools')).status_code,401)

class ProjectToolMigration(unittest.TestCase):
    def test_nonempty_0008_additive_upgrade_preserves_every_old_row_and_object(self):
        with TemporaryDirectory() as t:
            root=Path(t);s=Settings(data_root=root/'hub',legacy_database=root/'unused');initialize_empty(s.data_root)
            engine=create_engine('sqlite+pysqlite:///'+str(s.database),poolclass=NullPool)
            with engine.begin() as c:
                config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0008_projects')
                c.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES('old','project','original','personal','2026-11-12','2026-11-12',1)")
                c.exec_driver_sql("INSERT INTO projects(resource_id,objective) VALUES('old','retain facts')")
                c.exec_driver_sql('CREATE INDEX ix_fixture_tool ON projects(objective)')
                c.exec_driver_sql('CREATE TRIGGER fixture_tool_preserve AFTER UPDATE ON projects BEGIN SELECT 1; END')
            engine.dispose();before=audit_database(s.database);self.assertEqual(upgrade(s)['rehearsal'],'passed');after=audit_database(s.database)
            for name,facts in before['tables'].items():
                if name!='alembic_version':self.assertEqual(after['tables'][name],facts,name)
            self.assertEqual(revision(s.database),SCHEMA_REVISION)
    def test_failed_0009_ddl_rolls_back_revision_tables_and_data(self):
        with TemporaryDirectory() as t:
            root=Path(t);s=Settings(data_root=root/'hub',legacy_database=root/'unused');initialize_empty(s.data_root)
            engine=create_engine('sqlite+pysqlite:///'+str(s.database),poolclass=NullPool)
            with engine.begin() as c:
                config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0008_projects')
            engine.dispose();before=audit_database(s.database);execute=Connection.exec_driver_sql
            def fail(c,statement,*args,**kwargs):
                if 'CREATE TABLE project_tool_history' in statement:raise RuntimeError('injected migration failure')
                return execute(c,statement,*args,**kwargs)
            with patch.object(Connection,'exec_driver_sql',fail):
                with self.assertRaises(RuntimeError):upgrade(s)
            self.assertEqual(audit_database(s.database),before);self.assertEqual(revision(s.database),'0008_projects')
