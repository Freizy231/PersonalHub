"""Ordinary tag uniqueness, stale classification safety, privacy and complete recovery."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from uuid import uuid4
import zipfile

from alembic import command
from alembic.config import Config
import httpx
from pydantic import ValidationError
from sqlalchemy import update
from backend.app.core.config import Settings, PROJECT_ROOT
from backend.app.core.database import connection, HubError, SCHEMA_REVISION
from backend.app.core.note_snapshot import SnapshotFiles
from backend.app.core.resources import resources
from backend.app.main import create_app
from backend.app.modules.tags.service import TagService
from backend.app.modules.tags.models import tags
from backend.app.modules.tags.schemas import TagAssignment, TagRename, tag_name
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate, NoteEdit
from backend.app.modules.library.notes.vault import VaultNoteStore
from backend.app.modules.dashboard.service import DashboardService
from backend.app.modules.dashboard.schemas import PreferencesWrite, default_preferences
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskCreate
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from backend.tests.async_support import AsyncAPITestCase
from scripts.data_safety import audit_database


def key(): return str(uuid4())
def assignment(current,names):
    return TagAssignment(names=names,expected_version=current['resource_version'],expected_tag_versions={t['id']:t['version'] for t in current['tags']})


class TagValidation(unittest.TestCase):
    def test_nfkc_casefold_whitespace_and_chinese_without_synonym_merging(self):
        self.assertEqual(tag_name('  ＰＥ　 分析  '),'PE 分析')
        self.assertEqual(tag_name('Straße').casefold(),'strasse')
        self.assertNotEqual(tag_name('逆向工程'),tag_name('逆向'))
        self.assertEqual(tag_name('a,b'),'a,b')
    def test_invalid_ambiguous_oversized_or_duplicate_names_refused(self):
        for names in [[''],['x\n'],['x\t'],['a\u200b'],['\ud800'],['a'*65],['ß'*40],['AI','ａｉ'],['x']*21]:
            with self.subTest(names=repr(names)),self.assertRaises(ValidationError):
                TagAssignment(names=names,expected_version=1,expected_tag_versions={})
        for extra in [{'expected_version':True},{'expected_tag_versions':{key():True}},{'private':True}]:
            with self.assertRaises(ValidationError):TagAssignment(**{'names':['x'],'expected_version':1,'expected_tag_versions':{},**extra})


class TagFacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.service=TagService(self.settings.database);self.notes=NoteService(self.settings.database)
        self.note=self.notes.create(NoteCreate(title='虚构普通笔记',body='普通正文'),key())[0]
    def tearDown(self):self.temp.cleanup()
    def assign(self,names,rid=None):
        rid=rid or self.note['id'];return self.service.assign(rid,assignment(self.service.get(rid),names),key())
    def test_get_is_readonly_and_assignment_reuses_normalized_identity_without_new_markdown(self):
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        self.assertEqual(self.service.get(self.note['id'])['tags'],[]);self.assertEqual(self.service.list()['total'],0)
        self.assertEqual(audit_database(self.settings.database),before)
        first=self.assign([' ＡＩ ','恶意代码']);self.assertEqual(first['resource_version'],2)
        second=self.notes.create(NoteCreate(title='第二篇'),key())[0]
        attached=self.assign(['ai'],second['id']);aid=next(t['id'] for t in first['tags'] if t['name']=='AI')
        self.assertEqual(attached['tags'][0]['id'],aid);self.assertEqual(attached['tags'][0]['name'],'AI')
        self.assertEqual(attached['tags'][0]['usage_count'],2)
        self.assertEqual(self.notes.history(self.note['id'])['total'],1)
        self.assertEqual(self.notes.get(self.note['id'])['body'],self.note['body'])
        # The first note's immutable file was not touched.
        for name,info in files.items(): self.assertEqual(SnapshotFiles(self.settings.data_root).inventory()[name],info)
    def test_same_set_noop_retry_returns_current_facts_and_stale_write_refuses(self):
        initial=self.service.get(self.note['id']);payload=assignment(initial,['AI']);operation=key()
        first=self.service.assign(self.note['id'],payload,operation)
        self.assertEqual(self.service.assign(self.note['id'],assignment(first,['ａｉ']),key())['resource_version'],2)
        second=self.assign(['安全'])
        self.assertEqual(self.service.assign(self.note['id'],payload,operation),second)
        with self.assertRaises(HubError) as e:self.service.assign(self.note['id'],payload,key())
        self.assertEqual(e.exception.code,'VERSION_CONFLICT')
        with self.assertRaises(HubError) as e:self.service.assign(self.note['id'],assignment(initial,[]),operation)
        self.assertEqual(e.exception.code,'IDEMPOTENCY_CONFLICT')
    def test_concurrent_create_same_normalized_name_uses_one_tag(self):
        other=self.notes.create(NoteCreate(title='并发普通笔记'),key())[0]
        pairs=[(n['id'],assignment(self.service.get(n['id']),[name])) for n,name in [(self.note,'AI'),(other,'ａｉ')]]
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda pair:self.service.assign(*pair,key()),pairs))
        self.assertEqual(results[0]['tags'][0]['id'],results[1]['tags'][0]['id'])
        self.assertEqual(self.service.list()['total'],1);self.assertEqual(self.service.list()['items'][0]['usage_count'],2)
    def test_rename_is_shared_cas_and_blocks_old_name_assignment_without_rewriting_body(self):
        current=self.assign(['AI']);tag=current['tags'][0];old_payload=assignment(current,['AI','新标签'])
        second=self.notes.create(NoteCreate(title='共享引用'),key())[0];self.assign(['AI'],second['id'])
        files=SnapshotFiles(self.settings.data_root).inventory();operation=key()
        payload=TagRename(name='人工智能',expected_version=tag['version'])
        renamed=self.service.rename(tag['id'],payload,operation)
        self.assertEqual(renamed['version'],2);self.assertEqual(self.service.get(second['id'])['tags'][0]['name'],'人工智能')
        self.assertEqual(self.service.get(self.note['id'])['resource_version'],current['resource_version'])
        with self.assertRaises(HubError) as e:self.service.assign(self.note['id'],old_payload,key())
        self.assertEqual(e.exception.code,'VERSION_CONFLICT')
        later=self.service.rename(tag['id'],TagRename(name='机器学习',expected_version=2),key())
        self.assertEqual(self.service.rename(tag['id'],payload,operation),later)
        with self.assertRaises(HubError):self.service.rename(tag['id'],payload,key())
        self.assertEqual(SnapshotFiles(self.settings.data_root).inventory(),files)
    def test_rename_collision_no_merge_and_unlink_keeps_identity_for_reuse(self):
        current=self.assign(['AI','安全']);first,second=current['tags']
        before=audit_database(self.settings.database)
        with self.assertRaises(HubError) as e:self.service.rename(first['id'],TagRename(name=second['name'],expected_version=1),key())
        self.assertEqual(e.exception.code,'TAG_NAME_CONFLICT');self.assertEqual(audit_database(self.settings.database),before)
        empty=self.assign([]);self.assertEqual(empty['tags'],[]);self.assertEqual(self.service.list()['total'],0)
        reused=self.assign([first['name']]);self.assertEqual(reused['tags'][0]['id'],first['id'])
    def test_record_failure_rolls_back_tags_edges_resource_version_and_activity(self):
        before=audit_database(self.settings.database)
        with mock.patch('backend.app.modules.tags.service.record',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.assign(['事务回滚'])
        self.assertEqual(audit_database(self.settings.database),before)
        current=self.assign(['AI']);before=audit_database(self.settings.database)
        with mock.patch('backend.app.modules.tags.service.record',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.service.rename(current['tags'][0]['id'],TagRename(name='改名回滚',expected_version=1),key())
        self.assertEqual(audit_database(self.settings.database),before)
    def test_hidden_trash_unknown_and_task_ids_do_not_leak_in_directory_counts_or_assignment(self):
        first=self.assign(['共同','仅第一篇']);second=self.notes.create(NoteCreate(title='其他笔记'),key())[0]
        self.assign(['共同'],second['id'])
        with connection(self.settings.database,write=True) as con:con.execute(update(resources).where(resources.c.id==self.note['id']).values(privacy_level='private'))
        visible=self.service.list();self.assertEqual(visible['total'],1);self.assertEqual(visible['items'][0]['usage_count'],1)
        hidden_tid=next(t['id'] for t in first['tags'] if t['name']=='仅第一篇')
        with self.assertRaises(HubError) as e:self.service.rename(hidden_tid,TagRename(name='猜测',expected_version=1),key())
        self.assertEqual(e.exception.code,'TAG_NOT_FOUND')
        task=TaskService(self.settings.database).create(TaskCreate(title='不是笔记'),key())[0]
        self.assertEqual(self.service.get(task['id'])['tags'],[])
        for rid in [self.note['id'],key()]:
            with self.assertRaises(HubError) as e:self.service.get(rid)
            self.assertEqual(e.exception.code,'RESOURCE_TAGS_NOT_FOUND')
            with self.assertRaises(HubError):self.service.assign(rid,TagAssignment(names=['不落盘'],expected_version=1,expected_tag_versions={}),key())
        self.notes.trash(second['id'],2);self.assertEqual(self.service.list()['total'],0)
        self.assertEqual(self.notes.list(scope='trash',tag_id=visible['items'][0]['id'])['total'],1)
        self.notes.trash(second['id'],3,True);self.assertEqual(self.service.list()['total'],1)
        exported=zipfile.ZipFile(BytesIO(self.notes.export())).read('notes.json').decode()
        self.assertNotIn('仅第一篇',exported);self.assertNotIn(self.note['id'],exported)
    def test_filter_pagination_literal_query_export_and_resource_version_protect_body_editor(self):
        current=self.assign(['AI','100%','a_b'])
        tid=next(t['id'] for t in current['tags'] if t['name']=='AI')
        second=self.notes.create(NoteCreate(title='无标签'),key())[0]
        self.assertEqual(self.notes.list(tag_id=tid)['total'],1)
        self.assertEqual(self.notes.list(tag_id=key())['total'],0)
        self.assertEqual(self.service.list(q='%')['total'],1);self.assertEqual(self.service.list(q='_')['total'],1)
        self.assertEqual(self.service.list(q='ａｉ')['total'],1)
        self.assertEqual(self.service.list(limit=1,offset=1)['total'],3);self.assertEqual(len(self.service.list(limit=1,offset=1)['items']),1)
        with self.assertRaises(HubError):self.notes.change(self.note['id'],NoteEdit(title='旧正文编辑',body='不应覆盖',expected_version=1,expected_hash=self.note['content_sha256']),key())
        exported=json.loads(zipfile.ZipFile(BytesIO(self.notes.export())).read('notes.json'))
        row=next(r for r in exported['notes'] if r['id']==self.note['id']);self.assertEqual(len(row['tags']),3)
        self.assertEqual(self.notes.list(tag_id=tid)['items'][0]['tags'][0]['name'],'100%')
    def test_reserved_name_and_foreign_keys_fail_without_overwriting_retained_identity(self):
        current=self.assign(['保留名称']);tid=current['tags'][0]['id'];self.assign([])
        with connection(self.settings.database,write=True) as con:con.execute(update(tags).where(tags.c.id==tid).values(deleted_at='2026-11-12T00:00:00Z'))
        before=audit_database(self.settings.database)
        with self.assertRaises(HubError) as e:self.assign(['保留名称'])
        self.assertEqual(e.exception.code,'TAG_NAME_RESERVED');self.assertEqual(audit_database(self.settings.database),before)
        from sqlalchemy.exc import IntegrityError
        with self.assertRaises(IntegrityError):
            with connection(self.settings.database,write=True) as con:
                con.exec_driver_sql('INSERT INTO resource_tags(resource_id,tag_id,created_at) VALUES (?,?,?)',(key(),tid,'2026-11-12T00:00:00Z'))
        self.assertEqual(audit_database(self.settings.database),before)

    def test_complete_snapshot_restores_tag_identity_links_prefs_and_vault_ciphertext(self):
        self.assign(['恢复标签']);DashboardService(self.settings.database).save(PreferencesWrite(**default_preferences(),expected_version=0),key())
        vault=VaultNoteStore(self.settings.data_root);vault.open('fake-session','fictitious-password-only',create=True);vault.lock()
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
        self.assertEqual(audit_database(self.root/'restored/db/hub.db'),before)
        self.assertEqual(SnapshotFiles(self.root/'restored').inventory(),files)
        self.assertEqual(TagService(self.root/'restored/db/hub.db').get(self.note['id']),self.service.get(self.note['id']))


class TagMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        with connection(self.settings.database,write=True,require_current=False,enforce_foreign_keys=False) as con:
            config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=con;command.upgrade(config,'0004_dashboard_preferences')
        (self.settings.data_root/'config').mkdir(mode=0o700)
        with mock.patch('backend.app.core.database.SCHEMA_REVISION','0004_dashboard_preferences'), mock.patch('backend.app.modules.life.tasks.service.tag_map',side_effect=lambda con,ids:{rid:[] for rid in ids}):
            NoteService(self.settings.database).create(NoteCreate(title='升级前虚构笔记',body='不变正文'),key())
            TaskService(self.settings.database).create(TaskCreate(title='升级前虚构任务'),key())
            DashboardService(self.settings.database).save(PreferencesWrite(**default_preferences(),expected_version=0),key())
        vault=VaultNoteStore(self.settings.data_root);vault.open('fake','fake-password-only',create=True);vault.lock()
    def tearDown(self):self.temp.cleanup()
    def test_nonempty_v060_upgrade_is_purely_additive_and_files_unchanged(self):
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory()
        result=upgrade(self.settings);self.assertEqual(result['revision'],SCHEMA_REVISION)
        after=audit_database(self.settings.database)
        for name,table in before['tables'].items():
            if name!='alembic_version':self.assertEqual(table,after['tables'][name])
        for name in ['tags','resource_tags']:self.assertEqual(after['tables'][name]['row_count'],0)
        self.assertEqual(files,SnapshotFiles(self.settings.data_root).inventory())
    def test_late_real_ddl_failure_rolls_back_both_tables_and_revision(self):
        before=audit_database(self.settings.database);files=SnapshotFiles(self.settings.data_root).inventory();original=command.upgrade;calls=0
        def fail(*args):
            nonlocal calls
            calls+=1;original(*args)
            if calls==2:raise RuntimeError('after real tag DDL')
        with mock.patch('backend.maintenance.command.upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(calls,2);self.assertEqual(audit_database(self.settings.database),before)
        self.assertEqual(SnapshotFiles(self.settings.data_root).inventory(),files)


class TagAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings);self.app=create_app(self.settings)
        self.origin='http://127.0.0.1:8765';self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url=self.origin)
        response=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':self.origin})
        self.headers={'Origin':self.origin,'X-CSRF-Token':response.json()['csrf_token']}
        self.note=NoteService(self.settings.database).create(NoteCreate(title='API虚构笔记'),key())[0]
        self.path=f"/api/v1/resources/{self.note['id']}/tags"
    async def asyncTearDown(self):await self.client.aclose();self.temp.cleanup()
    async def test_session_origin_csrf_dto_limit_and_private_values_are_not_echoed(self):
        data={'names':['公开测试'],'expected_version':1,'expected_tag_versions':{}}
        self.assertEqual((await self.client.get(self.path)).status_code,200)
        self.assertEqual((await self.client.put(self.path,json=data,headers={'Origin':self.origin})).status_code,403)
        self.assertEqual((await self.client.put(self.path,json=data,headers={**self.headers,'Origin':'https://evil.invalid'})).status_code,403)
        response=await self.client.put(self.path,json={**data,'private':'never-echo-sentinel'},headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(response.status_code,422);self.assertNotIn('never-echo-sentinel',response.text)
        self.assertEqual((await self.client.put(self.path,content=b'x'*65537,headers=self.headers)).status_code,413)
        self.assertEqual((await self.client.get('/api/v1/tags?limit=101')).status_code,422)
        self.assertEqual((await self.client.get('/api/v1/notes?tag_id=bad')).status_code,422)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/tags')).status_code,401)
    async def test_concurrent_assignment_cas_and_same_key_replay(self):
        data={'names':['并发标签'],'expected_version':1,'expected_tag_versions':{}}
        responses=await asyncio.gather(*[self.client.put(self.path,json=data,headers={**self.headers,'Idempotency-Key':key()}) for _ in range(2)])
        self.assertEqual(sorted(r.status_code for r in responses),[200,409])
        current=(await self.client.get(self.path)).json();data={'names':[],'expected_version':2,'expected_tag_versions':{t['id']:t['version'] for t in current['tags']}}
        headers={**self.headers,'Idempotency-Key':key()}
        responses=await asyncio.gather(*[self.client.put(self.path,json=data,headers=headers) for _ in range(3)])
        self.assertTrue(all(r.status_code==200 and r.json()['resource_version']==3 for r in responses))
        self.assertEqual((await self.client.get('/api/v1/tags')).json()['total'],0)

    async def test_tagged_note_filter_shared_rename_and_incomplete_root_refusal(self):
        data={'names':['  ＡＩ  '],'expected_version':1,'expected_tag_versions':{}}
        response=await self.client.put(self.path,json=data,headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(response.status_code,200);tid=response.json()['tags'][0]['id']
        listing=await self.client.get('/api/v1/notes',params={'tag_id':tid})
        self.assertEqual(listing.json()['total'],1);self.assertEqual(listing.json()['items'][0]['tags'][0]['name'],'AI')
        response=await self.client.put('/api/v1/tags/'+tid,json={'name':'人工智能','expected_version':1},headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(response.status_code,200);self.assertEqual(response.json()['version'],2)
        self.assertEqual((await self.client.get('/api/v1/tags?q=人工')).json()['total'],1)
        before=audit_database(self.settings.database)
        marker=self.settings.data_root/'HUB_INCOMPLETE.json';marker.write_text('{}')
        self.assertEqual((await self.client.get(self.path)).status_code,503)
        marker.unlink();self.assertEqual(audit_database(self.settings.database),before)
