import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock
import uuid

import httpx

from backend.app.core.config import Settings
from backend.app.core.database import HubError, SCHEMA_REVISION, revision
from backend.app.main import create_app, database_status
from backend.app.modules.life.tasks.service import TaskService
from backend.manage import initialize_empty
from backend.maintenance import backup_hub, process_lock, restore_hub, upgrade
from scripts.data_safety import audit_database, verified_backup
from backend.tests.async_support import AsyncAPITestCase


class TaskAPITests(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.settings = Settings(reminders_enabled=False, data_root=self.root/'hub', legacy_database=self.root/'legacy.db')
        initialize_empty(self.settings.data_root)
        upgrade(self.settings)
        self.app = create_app(self.settings)
        self.life = self.app.router.lifespan_context(self.app)
        await self.life.__aenter__()
        self.origin = 'http://127.0.0.1:8765'
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app, raise_app_exceptions=False), base_url=self.origin)
        result = await self.client.post('/api/v1/session/exchange', json={'token': self.app.state.auth.bootstrap}, headers={'Origin':self.origin})
        self.headers = {'Origin':self.origin,'X-CSRF-Token':result.json()['csrf_token']}

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.life.__aexit__(None,None,None)
        self.temp.cleanup()

    async def create(self, **fields):
        response = await self.client.post('/api/v1/tasks', json={'title':'虚构任务',**fields},
            headers={**self.headers,'Idempotency-Key':str(uuid.uuid4())})
        self.assertEqual(response.status_code,201,response.text)
        return response.json()

    async def patch(self, row, **fields):
        return await self.client.patch('/api/v1/tasks/'+row['id'], json={'expected_version':row['version'],**fields},headers=self.headers)

    def read(self, sql):
        with closing(sqlite3.connect(self.settings.database)) as con:
            return con.execute(sql).fetchall()

    async def test_task_loop_edit_complete_reopen_and_new_process_persistence(self):
        row = await self.create(title='  PE 导入表练习  ', planned_date='2026-11-11', due_date='2026-11-12',priority=3)
        self.assertEqual(row['title'],'PE 导入表练习')
        result = await self.client.get('/api/v1/tasks?scope=today&on_date=2026-11-11')
        self.assertEqual(result.json()['items'][0]['id'],row['id'])
        response = await self.patch(row,title='提交实验记录',status='done',description='虚构实验')
        self.assertEqual(response.status_code,200,response.text)
        done = response.json()
        self.assertEqual(done['version'],2)
        self.assertIsNotNone(done['completed_at'])
        response = await self.patch(done,status='todo')
        self.assertIsNone(response.json()['completed_at'])
        # New app/auth session reads the same SQLite facts; no migration at startup.
        app = create_app(self.settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url=self.origin) as other:
                await other.post('/api/v1/session/exchange',json={'token':app.state.auth.bootstrap},headers={'Origin':self.origin})
                persisted = await other.get('/api/v1/tasks/'+row['id'])
                self.assertEqual(persisted.json()['version'],3)
                self.assertEqual(persisted.json()['description'],'虚构实验')
        self.assertFalse(self.settings.legacy_database.exists())

    async def test_a_task_with_an_exact_deadline_sorts_by_that_day_not_as_undated(self):
        await self.create(title='无截止')
        await self.create(title='有截止时刻', due_at='2026-11-13T09:00:00+08:00', timezone='Asia/Shanghai')
        await self.create(title='较早截止日', due_date='2026-11-12')
        listing = (await self.client.get('/api/v1/tasks?scope=all')).json()['items']
        self.assertEqual([t['title'] for t in listing], ['较早截止日', '有截止时刻', '无截止'])

    async def test_resaving_a_done_task_does_not_log_completed_again(self):
        row = await self.create(title='已完成的')
        done = (await self.patch(row, status='done')).json()
        self.assertEqual(self.read("SELECT COUNT(*) FROM activities WHERE action='completed'")[0][0], 1)
        again = await self.patch(done, status='done', title='改个名字')
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(self.read("SELECT COUNT(*) FROM activities WHERE action='completed'")[0][0], 1)

    async def test_extreme_instants_and_zone_directories_are_422_not_500(self):
        for body in ({'due_at': '9999-12-31T23:59:59-05:00'}, {'timezone': 'America'}):
            response = await self.client.post('/api/v1/tasks', json={'title': 'x', **body},
                headers={**self.headers, 'Idempotency-Key': str(uuid.uuid4())})
            self.assertEqual(response.status_code, 422, (body, response.text))

    async def test_parallel_duplicate_creation_and_changed_payload_key_conflict(self):
        headers = {**self.headers,'Idempotency-Key':str(uuid.uuid4())}
        responses = await asyncio.gather(*[self.client.post('/api/v1/tasks',json={'title':'一次创建'},headers=headers) for _ in range(3)])
        self.assertEqual(sorted(r.status_code for r in responses),[200,200,201])
        self.assertEqual(len({r.json()['id'] for r in responses}),1)
        response = await self.client.post('/api/v1/tasks',json={'title':'不同内容'},headers=headers)
        self.assertEqual(response.status_code,409)
        self.assertEqual(self.read('SELECT count(*) FROM resources'),[(1,)])
        self.assertEqual(self.read('SELECT count(*) FROM activities'),[(1,)])

    async def test_stale_version_never_overwrites_and_retry_does_not_duplicate_activity(self):
        row = await self.create()
        saved = await self.patch(row,title='最新名称')
        rejected = await self.patch(row,title='过时覆盖')
        self.assertEqual(rejected.status_code,409)
        self.assertEqual(rejected.json()['code'],'VERSION_CONFLICT')
        same = await self.patch(saved.json(),title='最新名称')
        self.assertEqual(same.json()['version'],2)
        self.assertEqual(self.read('SELECT count(*) FROM activities'),[(2,)])

    async def test_soft_delete_undo_and_private_scope_cannot_be_bypassed(self):
        row = await self.create()
        deleted = await self.client.post(f"/api/v1/tasks/{row['id']}/trash",json={'expected_version':1},headers=self.headers)
        self.assertEqual(deleted.status_code,200)
        self.assertEqual((await self.client.get('/api/v1/tasks')).json()['total'],0)
        self.assertEqual((await self.client.get('/api/v1/tasks?scope=trash')).json()['total'],1)
        self.assertEqual((await self.client.get('/api/v1/tasks/'+row['id'])).status_code,404)
        restored = await self.client.post(f"/api/v1/tasks/{row['id']}/restore",json={'expected_version':2},headers=self.headers)
        self.assertEqual(restored.json()['version'],3)
        with closing(sqlite3.connect(self.settings.database)) as con:
            con.execute("UPDATE resources SET privacy_level='private' WHERE id=?",(row['id'],));con.commit()
        for path in ['/api/v1/tasks','/api/v1/tasks?scope=trash']:
            self.assertEqual((await self.client.get(path)).json()['total'],0)
        self.assertEqual((await self.client.get('/api/v1/tasks/'+row['id'])).status_code,404)
        self.assertEqual((await self.client.get('/api/v1/tasks/export')).json()['tasks'],[])
        self.assertEqual((await self.client.post(f"/api/v1/tasks/{row['id']}/trash",json={'expected_version':3},headers=self.headers)).status_code,404)

    async def test_validation_and_errors_never_echo_sensitive_input(self):
        for fields in [{'title':' '},{'priority':True},{'priority':4},{'timezone':'fake/zone'},
                       {'due_date':'2026-02-30'},{'planned_date':1000},{'due_at':'2026-11-11T12:00:00'},
                       {'due_at':1000},{'title':'x','privacy_level':'private'},
                       {'due_date':'2026-11-11','due_at':'2026-11-11T00:00:00Z'}]:
            response = await self.client.post('/api/v1/tasks',json={'title':'secret-sentinel',**fields},
                headers={**self.headers,'Idempotency-Key':str(uuid.uuid4())})
            self.assertEqual(response.status_code,422,response.text)
            self.assertNotIn('secret-sentinel',response.text)
        row = await self.create()
        for fields in [{'priority':None},{'title':None},{'timezone':None},{'status':None}]:
            self.assertEqual((await self.patch(row,**fields)).status_code,422)
        self.assertEqual((await self.client.get('/api/v1/tasks?limit=101')).status_code,422)
        self.assertEqual((await self.client.get('/api/v1/tasks?scope=invalid')).status_code,422)

    async def test_activity_failure_rolls_back_identity_task_and_idempotency(self):
        with mock.patch.object(TaskService,'_activity',side_effect=RuntimeError('private-error-body')):
            result = await self.client.post('/api/v1/tasks',json={'title':'rollback'},headers={**self.headers,'Idempotency-Key':str(uuid.uuid4())})
        self.assertEqual(result.status_code,500)
        self.assertNotIn('private-error-body',result.text)
        for table in ['resources','tasks','activities','operation_requests']:
            self.assertEqual(self.read(f'SELECT count(*) FROM {table}'),[(0,)])

    async def test_timezone_deadline_end_of_day_precision_and_completed_exclusion(self):
        instant = datetime(2026,11,11,0,30,tzinfo=timezone.utc)
        await self.create(title='上海已过昨天',due_date='2026-11-10',timezone='Asia/Shanghai')
        await self.create(title='檀香山仍是当天',due_date='2026-11-10',timezone='Pacific/Honolulu')
        precise = await self.create(title='精确时刻',due_at='2026-11-11T08:29:00+08:00')
        with mock.patch('backend.app.core.database.datetime',wraps=datetime) as dbdt, \
                mock.patch('backend.app.modules.life.tasks.service.datetime',wraps=datetime) as servicedt:
            dbdt.now.return_value = instant;servicedt.now.return_value = instant
            # utc_now uses the same mocked core clock.
            response = await self.client.get('/api/v1/tasks?scope=overdue')
            titles = {r['title'] for r in response.json()['items']}
            self.assertEqual(titles,{'上海已过昨天','精确时刻'})
            self.assertTrue(all(r['is_overdue'] for r in response.json()['items']))
            await self.patch(precise,status='done')
            self.assertEqual((await self.client.get('/api/v1/tasks?scope=overdue')).json()['total'],1)
        self.assertEqual(precise['due_at'],'2026-11-11T00:29:00.000000Z')
        # Merged-field deadline validation also covers PATCH.
        self.assertEqual((await self.patch(await self.create(due_date='2026-11-12'),due_at='2026-11-11T12:00:00Z')).status_code,422)

    async def test_pagination_export_and_csrf_auth_boundaries(self):
        for n in range(3): await self.create(title=f'任务{n}',planned_date='2026-11-11',priority=n)
        first = (await self.client.get('/api/v1/tasks?limit=2')).json()
        second = (await self.client.get('/api/v1/tasks?limit=2&offset=2')).json()
        self.assertEqual(first['total'],3)
        self.assertFalse({r['id'] for r in first['items']} & {r['id'] for r in second['items']})
        exported = await self.client.get('/api/v1/tasks/export')
        self.assertIn('attachment',exported.headers['content-disposition'])
        self.assertEqual(len(exported.json()['tasks']),3)
        self.assertEqual(len(exported.json()['activities']),3)
        row = first['items'][0]
        self.assertEqual((await self.client.patch('/api/v1/tasks/'+row['id'],json={'expected_version':1,'status':'done'},headers={'Origin':self.origin})).status_code,403)
        self.assertEqual((await self.client.post('/api/v1/tasks',json={'title':'x'},headers={'Origin':'https://evil.invalid'})).status_code,403)
        self.client.cookies.clear()
        for path in ['/api/v1/tasks','/api/v1/tasks/export','/api/v1/tasks/'+row['id']]:
            self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_missing_or_future_database_refuses_crud_without_creation_or_repair(self):
        with closing(sqlite3.connect(self.settings.database)) as con:
            con.execute("UPDATE alembic_version SET version_num='future_revision'");con.commit()
        before = self.settings.database.read_bytes()
        response = await self.client.post('/api/v1/tasks',json={'title':'x'},headers={**self.headers,'Idempotency-Key':str(uuid.uuid4())})
        self.assertEqual(response.status_code,503)
        self.assertEqual(before,self.settings.database.read_bytes())
        missing = self.root/'never.db'
        with self.assertRaises(HubError):TaskService(missing).list()
        self.assertFalse(missing.exists())

    async def test_lost_create_retry_after_trash_never_resurrects_or_duplicates(self):
        headers = {**self.headers, 'Idempotency-Key': str(uuid.uuid4())}
        created = await self.client.post('/api/v1/tasks', json={'title': '重试样例'}, headers=headers)
        row = created.json()
        await self.client.post(f"/api/v1/tasks/{row['id']}/trash", json={'expected_version': 1}, headers=self.headers)
        replay = await self.client.post('/api/v1/tasks', json={'title': '重试样例'}, headers=headers)
        self.assertEqual(replay.status_code, 409)
        self.assertEqual(replay.json()['code'], 'IDEMPOTENCY_RESULT_TRASHED')
        self.assertEqual(self.read('SELECT count(*) FROM tasks'), [(1,)])
        self.assertEqual((await self.client.get('/api/v1/tasks?scope=trash')).json()['total'], 1)

    async def test_static_shell_assets_and_spa_routes_never_serve_personal_files(self):
        dist = self.root / 'dist'
        (dist / 'assets').mkdir(parents=True)
        (dist / 'index.html').write_text('<html>public-shell-only</html>')
        (dist / 'assets' / 'shell.js').write_text('console.log("public-asset")')
        app = create_app(Settings(reminders_enabled=False, data_root=self.settings.data_root,
            legacy_database=self.settings.legacy_database, frontend_dist=dist))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=self.origin) as client:
                for path in ['/', '/life', '/settings', '/runtime/personal-hub/db/hub.db']:
                    response = await client.get(path)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.text, '<html>public-shell-only</html>')
                    self.assertEqual(response.headers['cache-control'], 'no-store')
                self.assertEqual((await client.get('/assets/shell.js')).text, 'console.log("public-asset")')
                self.assertEqual((await client.get('/api/v1/unknown')).status_code, 404)


class TaskMaintenanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(reminders_enabled=False, data_root=self.root/'hub',legacy_database=self.root/'legacy.db')
        self.settings.legacy_database.write_bytes(b'legacy-sentinel-do-not-touch')
        initialize_empty(self.settings.data_root)

    def tearDown(self):self.temp.cleanup()

    def test_explicit_upgrade_rehearsal_backup_preserves_existing_rows_and_no_legacy_writes(self):
        with closing(sqlite3.connect(self.settings.database)) as con:
            con.executescript("CREATE TABLE preserved(id INTEGER PRIMARY KEY,payload BLOB);INSERT INTO preserved VALUES(7,X'00FF');")
        before=audit_database(self.settings.database)
        result=upgrade(self.settings)
        self.assertEqual(result['revision'],SCHEMA_REVISION)
        self.assertEqual(result['rehearsal'],'passed')
        _,manifest=verified_backup(Path(result['backup_directory']))
        self.assertEqual(manifest['database']['audit'],before)
        self.assertEqual(audit_database(self.settings.database)['tables']['preserved'],before['tables']['preserved'])
        self.assertEqual(self.settings.legacy_database.read_bytes(),b'legacy-sentinel-do-not-touch')
        self.assertEqual(upgrade(self.settings)['status'],'up_to_date')

    def test_actual_migration_failure_rolls_back_all_ddl_and_revision(self):
        from alembic import command
        original=command.upgrade;calls=0
        def failure(config,target):
            nonlocal calls
            calls+=1;original(config,target)
            if calls==2:raise RuntimeError('simulated-after-real-ddl')
        before=audit_database(self.settings.database)
        with mock.patch('backend.maintenance.command.upgrade',side_effect=failure):
            with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(calls,2)
        self.assertEqual(audit_database(self.settings.database),before)

    def test_lifetime_lock_blocks_upgrade_and_backup_and_missing_paths_never_create(self):
        with process_lock(self.settings):
            for operation in [lambda:upgrade(self.settings),lambda:backup_hub(self.settings,self.root/'backup')]:
                with self.assertRaises(HubError) as result:operation()
                self.assertEqual(result.exception.code,'HUB_RUNNING_STOP_FIRST')
        self.assertFalse((self.root/'backup').exists())
        missing=Settings(reminders_enabled=False, data_root=self.root/'missing',legacy_database=self.settings.legacy_database)
        with self.assertRaises(HubError):upgrade(missing)
        self.assertFalse(missing.data_root.exists())
        link=self.settings.data_root/'db-link';link.symlink_to(self.settings.database.parent,target_is_directory=True)
        with self.assertRaises(HubError):TaskService(link/'hub.db').list()

    def test_task_backup_restore_preserves_all_facts_and_never_overwrites(self):
        from backend.app.modules.life.tasks.schemas import TaskCreate,TaskPatch
        upgrade(self.settings)
        service=TaskService(self.settings.database)
        row,_=service.create(TaskCreate(title='持久化样例',planned_date='2026-11-11'),str(uuid.uuid4()))
        service.change(row['id'],TaskPatch(expected_version=1,status='done'))
        expected=audit_database(self.settings.database)
        backup_hub(self.settings,self.root/'backup')
        result=restore_hub(self.settings,self.root/'backup',self.root/'isolated')
        self.assertEqual(result['status'],'verified')
        restored=self.root/'isolated'/'database.sqlite3'
        self.assertEqual(audit_database(restored),expected)
        self.assertEqual(TaskService(restored).get(row['id'])['status'],'done')
        with self.assertRaises(HubError):restore_hub(self.settings,self.root/'backup',self.settings.data_root)
        self.assertEqual(audit_database(self.settings.database),expected)

    def test_unknown_revision_cannot_be_backed_up_or_upgraded(self):
        with closing(sqlite3.connect(self.settings.database)) as con:
            con.execute("UPDATE alembic_version SET version_num='future'");con.commit()
        before=hashlib.sha256(self.settings.database.read_bytes()).hexdigest()
        with self.assertRaises(HubError):upgrade(self.settings)
        with self.assertRaises(HubError):backup_hub(self.settings,self.root/'backup')
        self.assertEqual(hashlib.sha256(self.settings.database.read_bytes()).hexdigest(),before)
        self.assertFalse((self.root/'backup').exists())
