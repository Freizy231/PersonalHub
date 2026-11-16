"""Preferences concurrency, URL boundary, non-destructive migration and full recovery."""
import asyncio
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
from uuid import uuid4

from alembic import command
from alembic.config import Config
import httpx
from pydantic import ValidationError

from backend.app.core.config import Settings, PROJECT_ROOT
from backend.app.core.database import connection, HubError, SCHEMA_REVISION
from backend.app.core.note_snapshot import SnapshotFiles
from backend.app.main import create_app
from backend.app.modules.dashboard.schemas import PreferencesWrite, default_preferences
from backend.app.modules.dashboard.service import DashboardService
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate
from backend.app.modules.library.notes.vault import VaultNoteStore
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from backend.tests.async_support import AsyncAPITestCase
from scripts.data_safety import audit_database


def key(): return str(uuid4())
def body(version=0, **kw): return {**default_preferences(), 'expected_version':version, **kw}


class PreferenceFacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.service=DashboardService(self.settings.database)
    def tearDown(self):self.temp.cleanup()
    def test_get_defaults_is_read_only_and_save_survives_new_service(self):
        before=audit_database(self.settings.database)
        self.assertEqual(self.service.get()['version'],0);self.assertEqual(audit_database(self.settings.database),before)
        widgets=default_preferences()['widgets'][::-1];widgets[0]['enabled']=False
        result=self.service.save(PreferencesWrite(**body(widgets=widgets)),key())
        self.assertEqual(result['widgets'],widgets);self.assertEqual(result['version'],1)
        self.assertEqual(DashboardService(self.settings.database).get(),result)
        with connection(self.settings.database) as con:
            self.assertEqual(con.exec_driver_sql('SELECT count(*) FROM activities').scalar(),0)
            self.assertEqual(con.exec_driver_sql('SELECT count(*) FROM resources').scalar(),0)
    def test_retry_and_stale_write_do_not_duplicate_or_overwrite_newer_configuration(self):
        operation=key();payload=PreferencesWrite(**body());first=self.service.save(payload,operation)
        self.assertEqual(self.service.save(payload,operation),first)
        changed=body(1,widgets=default_preferences()['widgets'][::-1]);second=self.service.save(PreferencesWrite(**changed),key())
        self.assertEqual(self.service.save(payload,operation),second) # current facts on replay
        with self.assertRaises(HubError) as e:self.service.save(PreferencesWrite(**body()),key())
        self.assertEqual(e.exception.code,'VERSION_CONFLICT')
        with self.assertRaises(HubError) as e:self.service.save(PreferencesWrite(**body(shortcuts=[])),operation)
        self.assertEqual(e.exception.code,'IDEMPOTENCY_CONFLICT')
        self.assertEqual(self.service.get(),second)
    def test_idempotency_failure_rolls_back_preference_creation(self):
        before=audit_database(self.settings.database)
        with mock.patch('backend.app.modules.dashboard.service.record',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.service.save(PreferencesWrite(**body()),key())
        self.assertEqual(audit_database(self.settings.database),before)
    def test_corrupt_unknown_schema_unknown_widget_and_unsafe_stored_url_fall_back_without_repair(self):
        self.service.save(PreferencesWrite(**body()),key())
        for column,value in [('widgets_json','{bad json'),('schema_version',2),
            ('widgets_json',json.dumps([{'id':'future-widget','enabled':True}]*5)),
            ('shortcuts_json',json.dumps([{'id':key(),'label':'unsafe','kind':'web','target':'javascript:alert(1)'}]))]:
            with connection(self.settings.database,write=True) as con:
                con.execute(__import__('sqlalchemy').text(f'UPDATE dashboard_preferences SET {column}=:value'),{'value':value})
            before=audit_database(self.settings.database);result=self.service.get()
            self.assertEqual(result['state'],'invalid');self.assertEqual(result['shortcuts'],[])
            self.assertEqual(result['widgets'],default_preferences()['widgets'])
            with self.assertRaises(HubError) as e:self.service.save(PreferencesWrite(**body(1)),key())
            self.assertEqual(e.exception.code,'DASHBOARD_CONFIG_INVALID');self.assertEqual(audit_database(self.settings.database),before)
            # Restore only the just-created fictitious row for the next independent variant.
            with connection(self.settings.database,write=True) as con:
                con.exec_driver_sql("UPDATE dashboard_preferences SET schema_version=1,widgets_json=?,shortcuts_json=?",(json.dumps(default_preferences()['widgets']),json.dumps(default_preferences()['shortcuts'])))
    def test_all_hidden_and_empty_shortcuts_remain_valid_and_backup_restores(self):
        widgets=[{'id':w['id'],'enabled':False} for w in default_preferences()['widgets']]
        saved=self.service.save(PreferencesWrite(**body(widgets=widgets,shortcuts=[])),key())
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
        self.assertEqual(DashboardService(self.root/'restored'/'db'/'hub.db').get(),saved)


class PreferenceValidation(unittest.TestCase):
    def test_web_routes_reject_protocol_credentials_controls_queries_and_command_paths(self):
        bad=['javascript:alert(1)','data:text/html,x','file:///tmp/a','vscode://x','//evil.test','/api/v1/session',
            'http://user:pass@example.com/','http://@example.com/','https://example.com/?token=sentinel',
            'https://example.com/#password','https://example.com?','https://example.com#',
            'https://example.com/\n','https://example.com/\\evil','https://example.com/%0aevil',
            'https://example.com/%5Cevil','https://example.com:0','https://example.com:65536','https:///missing',
            'https://[::1','https://[fe80::1%25eth0]/','http://999.999.999.999/','https://-invalid.test/',
            'https://example.com/ with-space','https://例子.测试/']
        for target in bad:
            with self.subTest(target=target),self.assertRaises(ValidationError):
                PreferencesWrite(**body(shortcuts=[{'id':key(),'label':'虚构','kind':'web','target':target}]))
        for target in ['/api/v1/health','/life?view=unknown','/life?view=vault&open=1','/../../etc/passwd','https://example.com']:
            with self.assertRaises(ValidationError):PreferencesWrite(**body(shortcuts=[{'id':key(),'label':'虚构','kind':'internal','target':target}]))
    def test_valid_targets_are_normalized_without_network_access(self):
        for source,target in [('HTTPS://EXAMPLE.COM','https://example.com/'),('http://localhost:8501','http://localhost:8501/'),('http://[::1]:8765/','http://[::1]:8765/')]:
            result=PreferencesWrite(**body(shortcuts=[{'id':key(),'label':'  学习  ','kind':'web','target':source}]))
            self.assertEqual(result.shortcuts[0].target,target);self.assertEqual(result.shortcuts[0].label,'学习')
        self.assertEqual(PreferencesWrite(**body(shortcuts=[{'id':key(),'label':'私密入口','kind':'internal','target':'/life?view=vault'}])).shortcuts[0].target,'/life?view=vault')
    def test_every_route_the_shell_links_to_is_accepted_and_default_shortcuts_point_at_real_pages(self):
        for target in ['/', '/tasks', '/events', '/notes', '/vault', '/diet', '/finance', '/exercise',
                       '/learning', '/library', '/projects', '/intelligence', '/ai', '/search', '/settings']:
            saved = PreferencesWrite(**body(shortcuts=[{'id': key(), 'label': '入口', 'kind': 'internal', 'target': target}]))
            self.assertEqual(saved.shortcuts[0].target, target)
        # Layouts saved before 0.14 stay valid; the page redirects them instead of rejecting the save.
        legacy = PreferencesWrite(**body(shortcuts=[{'id': key(), 'label': '旧入口', 'kind': 'internal', 'target': '/life?view=notes'}]))
        self.assertEqual(legacy.shortcuts[0].target, '/life?view=notes')
        current = {'/', '/tasks', '/events', '/notes', '/vault', '/diet', '/finance', '/exercise', '/learning',
                   '/library', '/projects', '/intelligence', '/ai', '/search', '/settings'}
        for shortcut in default_preferences()['shortcuts']:
            self.assertIn(shortcut['target'], current, 'a fresh install must not ship shortcuts to retired routes')

    def test_bounded_schema_rejects_ambiguous_booleans_duplicates_private_payloads_and_unknown_fields(self):
        default=default_preferences();variants=[{'schema_version':True},{'schema_version':2},{'expected_version':True},
            {'widgets':default['widgets'][1:]}, {'widgets':[default['widgets'][0]]*5},
            {'widgets':[{'id':w['id'],'enabled':1} for w in default['widgets']]},
            {'shortcuts':[default['shortcuts'][0]]*2}, {'shortcuts':[{**default['shortcuts'][0],'id':key()} for _ in range(13)]},
            {'private_title':'sentinel'},{'shortcuts':[{'id':key(),'label':' ','kind':'internal','target':'/life'}]},
            {'shortcuts':[{'id':key(),'label':'\ud800','kind':'internal','target':'/life'}]}]
        for fields in variants:
            with self.subTest(fields=list(fields)),self.assertRaises(ValidationError):PreferencesWrite(**body(**fields))


class PreferenceMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        # Build an actual released v0.5 schema, not a fabricated migration marker.
        with connection(self.settings.database,write=True,require_current=False,enforce_foreign_keys=False) as con:
            cfg=Config(str(PROJECT_ROOT/'backend/alembic.ini'));cfg.attributes['connection']=con;command.upgrade(cfg,'0003_notes')
        (self.settings.data_root/'config').mkdir(mode=0o700)
        with mock.patch('backend.app.core.database.SCHEMA_REVISION','0003_notes'):
            NoteService(self.settings.database,self.settings.data_root).create(NoteCreate(title='普通虚构资料',body='保留的普通正文'),key())
        vault=VaultNoteStore(self.settings.data_root);vault.open('owner','fictitious-password-only',create=True)
        vault.save('owner',{'title':'虚构私密','body':'虚构正文','note_kind':'treehole','local_date':None},key());vault.lock()
    def tearDown(self):self.temp.cleanup()
    def test_populated_v050_upgrade_preserves_every_old_row_and_note_vault_files(self):
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        result=upgrade(self.settings);self.assertEqual(result['revision'],SCHEMA_REVISION)
        after=audit_database(self.settings.database)
        for name,table in before['tables'].items():
            if name!='alembic_version':self.assertEqual(after['tables'][name],table)
        self.assertEqual(after['tables']['dashboard_preferences']['row_count'],0)
        self.assertEqual(SnapshotFiles(self.settings.data_root).inventory(),files)
        backup_hub(self.settings,self.root/'snapshot');restore_hub(self.settings,self.root/'snapshot',self.root/'restored')
        self.assertEqual(audit_database(self.root/'restored'/'db'/'hub.db'),after)
        self.assertEqual(SnapshotFiles(self.root/'restored').inventory(),files)
    def test_late_failure_after_real_additive_ddl_rolls_back_all_old_facts(self):
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory();original=command.upgrade;calls=0
        def failure(*args):
            nonlocal calls
            calls+=1;original(*args)
            if calls==2:raise RuntimeError('after dashboard DDL')
        with mock.patch('backend.maintenance.command.upgrade',side_effect=failure):
            with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(calls,2);self.assertEqual(audit_database(self.settings.database),before)
        self.assertEqual(SnapshotFiles(self.settings.data_root).inventory(),files)


class PreferenceAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.app=create_app(self.settings);self.origin='http://127.0.0.1:8765'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.origin)
        response=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':self.origin})
        self.headers={'Origin':self.origin,'X-CSRF-Token':response.json()['csrf_token'],'Idempotency-Key':key()}
    async def asyncTearDown(self):await self.client.aclose();self.temp.cleanup()
    async def test_auth_origin_csrf_request_size_validation_and_no_input_echo(self):
        response=await self.client.get('/api/v1/dashboard/preferences');self.assertEqual(response.status_code,200);self.assertEqual(response.json()['version'],0)
        self.assertEqual((await self.client.put('/api/v1/dashboard/preferences',json=body(),headers={'Origin':self.origin})).status_code,403)
        self.assertEqual((await self.client.put('/api/v1/dashboard/preferences',json=body(),headers={**self.headers,'Origin':'https://evil.invalid'})).status_code,403)
        response=await self.client.put('/api/v1/dashboard/preferences',json=body(private_title='private-sentinel'),headers=self.headers)
        self.assertEqual(response.status_code,422);self.assertNotIn('private-sentinel',response.text)
        response=await self.client.put('/api/v1/dashboard/preferences',content=b'x'*65537,headers=self.headers);self.assertEqual(response.status_code,413)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/dashboard/preferences')).status_code,401)
    async def test_concurrent_first_write_and_same_key_retry_are_atomic(self):
        first,second=await asyncio.gather(*[self.client.put('/api/v1/dashboard/preferences',json=body(),headers={**self.headers,'Idempotency-Key':key()}) for _ in range(2)])
        self.assertEqual(sorted([first.status_code,second.status_code]),[200,409])
        headers={**self.headers,'Idempotency-Key':key()};data=body(1,shortcuts=[])
        responses=await asyncio.gather(*[self.client.put('/api/v1/dashboard/preferences',json=data,headers=headers) for _ in range(3)])
        self.assertTrue(all(r.status_code==200 for r in responses));self.assertTrue(all(r.json()['version']==2 for r in responses))
        self.assertEqual((await self.client.get('/api/v1/dashboard/preferences')).json()['version'],2)
    async def test_missing_or_incomplete_database_never_creates_preferences(self):
        marker=self.settings.data_root/'HUB_INCOMPLETE.json';marker.write_text('{}')
        response=await self.client.put('/api/v1/dashboard/preferences',json=body(),headers=self.headers);self.assertEqual(response.status_code,503)
        marker.unlink()
        with connection(self.settings.database) as con:self.assertEqual(con.exec_driver_sql('SELECT count(*) FROM dashboard_preferences').scalar(),0)
