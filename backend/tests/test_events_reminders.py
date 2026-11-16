import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest import mock
import uuid

import httpx
from alembic import command
from alembic.config import Config
from backend.tests.async_support import AsyncAPITestCase
from backend.app.core.config import Settings, PROJECT_ROOT
from backend.app.core.database import connection, HubError, SCHEMA_REVISION
from backend.app.main import create_app, database_status
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskCreate, TaskPatch
from backend.app.modules.life.events.service import EventService
from backend.app.modules.life.events.schemas import EventCreate, EventEdit
from backend.app.modules.life.reminders.service import ReminderService
from backend.app.modules.life.reminders.schemas import ReminderCreate
from backend.app.modules.life.reminders.worker import ReminderWorker
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from scripts.data_safety import audit_database


def key():return str(uuid.uuid4())
def all_day(**extra):return dict(title='虚构学习日',is_all_day=True,start_date='2026-11-11',end_date='2026-11-12',**extra)


class EventReminderFacts(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.events=EventService(self.settings.database);self.tasks=TaskService(self.settings.database);self.reminders=ReminderService(self.settings.database)
        self.now=datetime(2026,11,11,12,tzinfo=timezone.utc)
    def tearDown(self):self.temp.cleanup()
    def task(self):return self.tasks.create(TaskCreate(title='虚构练习'),key())[0]
    def event(self,**extra):return self.events.create(EventCreate(**all_day(**extra)),key())[0]
    def reminder(self,row,when=None):
        return self.reminders.create(ReminderCreate(resource_id=row['id'],expected_resource_version=row['version'],
            remind_at=(when or self.now).isoformat()),key())[0]
    def raw(self,sql,args=()):
        with closing(sqlite3.connect(self.settings.database)) as con:
            result=con.execute(sql,args).fetchall();con.commit();return result

    def test_calendar_exclusive_all_day_and_viewer_timezone_boundaries(self):
        event=self.event()
        self.assertEqual(self.events.list('2026-11-10','2026-11-11')['total'],0)
        self.assertEqual(self.events.list('2026-11-11','2026-11-12')['total'],1)
        self.assertEqual(self.events.list('2026-11-12','2026-11-13')['total'],0)
        self.events.create(EventCreate(title='跨午夜',is_all_day=False,start_at='2026-11-11T23:30:00+08:00',end_at='2026-11-12T00:30:00+08:00'),key())
        self.assertEqual(self.events.list('2026-11-12','2026-11-13','Asia/Shanghai')['total'],1)
        self.assertEqual(self.events.list('2026-11-12','2026-11-13','UTC')['total'],0)
        for start,end,zone in [('2026-11-12','2026-11-11','UTC'),('2026-01-01','2026-12-31','UTC'),('2026-11-11','2026-11-12','unknown/zone')]:
            with self.assertRaises(HubError):self.events.list(start,end,zone)

    def test_dst_calendar_day_has_23_or_25_hours_and_exact_end_excluded(self):
        for day,start,end in [('2026-03-08','2026-03-09T03:30:00Z','2026-03-09T04:00:00Z'),
                              ('2026-11-01','2026-11-02T04:30:00Z','2026-11-02T05:00:00Z')]:
            self.events.create(EventCreate(title='DST边界',is_all_day=False,start_at=start,end_at=end,timezone='America/New_York'),key())
            next_day=(datetime.fromisoformat(day)+timedelta(days=1)).date().isoformat()
            self.assertEqual(self.events.list(day,next_day,'America/New_York')['total'],1)
            next_next=(datetime.fromisoformat(day)+timedelta(days=2)).date().isoformat()
            self.assertEqual(self.events.list(next_day,next_next,'America/New_York')['total'],0)

    def test_event_task_independence_hidden_association_and_export_privacy(self):
        task=self.task();event=self.event(task_id=task['id'])
        self.assertEqual(event['linked_task']['title'],task['title'])
        fields=all_day(status='completed')
        saved=self.events.change(event['id'],EventEdit(**fields,expected_version=1))
        self.assertEqual(self.tasks.get(task['id'])['status'],'todo')
        self.raw("UPDATE resources SET privacy_level='private' WHERE id=?",(task['id'],))
        hidden=self.events.get(event['id']);self.assertIsNone(hidden['task_id']);self.assertIsNone(hidden['linked_task'])
        changed=self.events.change(event['id'],EventEdit(**all_day(location='新地点'),expected_version=2))
        self.assertIsNone(changed['linked_task'])
        self.assertEqual(self.raw('SELECT task_id FROM events WHERE resource_id=?',(event['id'],)),[(task['id'],)])
        self.assertNotIn(task['id'],str(self.events.export()))
        with self.assertRaises(HubError):self.event(task_id=task['id'])
        self.raw("UPDATE resources SET privacy_level='private' WHERE id=?",(event['id'],))
        self.assertEqual(self.events.list('2026-11-11','2026-11-12')['total'],0)
        self.assertEqual(self.events.export()['events'],[])
        with self.assertRaises(HubError):self.events.get(event['id'])

    def test_event_idempotency_conflict_trash_restore_and_pending_cancellation(self):
        payload=EventCreate(**all_day());request=key()
        row,replayed=self.events.create(payload,request);self.assertFalse(replayed)
        replay,replayed=self.events.create(payload,request);self.assertTrue(replayed);self.assertEqual(row['id'],replay['id'])
        with self.assertRaises(HubError):self.events.create(EventCreate(**all_day(location='变更')),request)
        reminder=self.reminder(row)
        edited=self.events.change(row['id'],EventEdit(**all_day(start_at=None,location='教室'),expected_version=1))
        self.assertEqual(self.reminders.list('pending')['total'],1)
        with self.assertRaises(HubError):self.events.change(row['id'],EventEdit(**all_day(),expected_version=1))
        self.events.change(row['id'],EventEdit(title='改期',is_all_day=True,start_date='2026-11-12',end_date='2026-11-13',expected_version=2))
        self.assertEqual(self.reminders.list('pending')['total'],0)
        self.assertEqual(self.reminders.list('history')['items'][0]['status'],'cancelled')
        trashed=self.events.trash(row['id'],3)
        self.assertEqual(self.events.list('2026-11-11','2026-11-12',scope='trash')['total'],1)
        with self.assertRaises(HubError):self.events.create(payload,request)
        self.events.trash(row['id'],4,True)
        self.assertEqual(self.reminders.list('pending')['total'],0)

    def test_reminder_restart_concurrent_ticks_boundary_and_ack_retry(self):
        row=self.task()
        fresh=self.reminder(row,self.now-timedelta(hours=24))
        missed=self.reminder(row,self.now-timedelta(hours=24,microseconds=1))
        future=self.reminder(row,self.now+timedelta(seconds=1))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:ReminderService(self.settings.database).drain(self.now),range(2)))
        self.assertEqual(sum(r['delivered'] for r in results),1)
        self.assertEqual(sum(r['missed'] for r in results),1)
        inbox=self.reminders.list();self.assertEqual(inbox['total'],2)
        delivered=next(r for r in inbox['items'] if r['id']==fresh['id'])
        self.assertIsNotNone(delivered['delivered_at'])
        self.assertIsNone(next(r for r in inbox['items'] if r['id']==missed['id'])['delivered_at'])
        ack=self.reminders.change(fresh['id'],delivered['version'],'ack')
        again=self.reminders.change(fresh['id'],delivered['version'],'ack')
        self.assertEqual(ack,again)
        self.assertEqual(self.reminders.list()['total'],1)
        self.assertEqual(self.reminders.drain(self.now+timedelta(seconds=1))['delivered'],1)
        self.assertEqual(self.reminders.drain(self.now+timedelta(days=2))['delivered'],0)

    def test_task_changes_cancel_pending_and_restore_does_not_rearm(self):
        for fields in [dict(status='done'),dict(status='cancelled'),dict(due_date='2026-11-12'),dict(timezone='UTC')]:
            row=self.task();self.reminder(row)
            self.tasks.change(row['id'],TaskPatch(expected_version=1,**fields))
            self.assertEqual(self.reminders.list('pending',row['id'])['total'],0)
        row=self.task();self.reminder(row)
        self.tasks.trash(row['id'],1);self.tasks.trash(row['id'],2,restore=True)
        self.assertEqual(self.reminders.list('history',row['id'])['items'][0]['status'],'cancelled')
        with self.assertRaises(HubError):self.reminder({**row,'version':1})

    def test_ordinary_title_edit_and_unchanged_deadline_preserve_pending_reminder(self):
        row=self.task();self.reminder(row)
        self.tasks.change(row['id'],TaskPatch(expected_version=1,title='只改标题',due_date=None,due_at=None,timezone='Asia/Shanghai'))
        self.assertEqual(self.reminders.list('pending',row['id'])['total'],1)

    def test_event_soft_trash_and_completion_cancel_without_rearming(self):
        for action in ['trash','completed','cancelled']:
            row=self.event();self.reminder(row)
            if action=='trash':
                self.events.trash(row['id'],1);self.events.trash(row['id'],2,True)
            else:self.events.change(row['id'],EventEdit(**all_day(status=action),expected_version=1))
            self.assertEqual(self.reminders.list('history',row['id'])['items'][0]['status'],'cancelled')
            self.assertEqual(self.reminders.list('pending',row['id'])['total'],0)

    def test_reminder_privacy_including_history_worker_and_exports(self):
        row=self.task();reminder=self.reminder(row);self.reminders.drain(self.now)
        self.raw("UPDATE resources SET privacy_level='private' WHERE id=?",(row['id'],))
        for scope in ['history','inbox','pending']:self.assertEqual(self.reminders.list(scope)['total'],0)
        self.assertEqual(self.reminders.export()['reminders'],[])
        with self.assertRaises(HubError):self.reminders.change(reminder['id'],2,'ack')
        other=self.task();self.reminder(other)
        self.raw("UPDATE resources SET privacy_level='private' WHERE id=?",(other['id'],))
        self.assertEqual(self.reminders.drain(self.now)['cancelled'],1)

    def test_reminder_creation_idempotency_resource_version_and_global_key_namespace(self):
        row=self.task();payload=ReminderCreate(resource_id=row['id'],expected_resource_version=1,remind_at=self.now.isoformat());request=key()
        first,_=self.reminders.create(payload,request);second,replayed=self.reminders.create(payload,request)
        self.assertEqual(first,second);self.assertTrue(replayed)
        with self.assertRaises(HubError):self.events.create(EventCreate(**all_day()),request)
        self.tasks.change(row['id'],TaskPatch(expected_version=1,title='修改'))
        with self.assertRaises(HubError):self.reminders.create(payload,key())
        self.assertEqual(self.reminders.create(payload,request)[0]['id'],first['id'])

    def test_event_and_reminder_creation_rollback_when_activity_fails(self):
        with mock.patch('backend.app.modules.life.events.service.activity',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.event()
        self.assertEqual(self.raw('SELECT count(*) FROM events'),[(0,)])
        row=self.task()
        with mock.patch('backend.app.modules.life.reminders.service.activity',side_effect=RuntimeError):
            with self.assertRaises(RuntimeError):self.reminder(row)
        self.assertEqual(self.raw('SELECT count(*) FROM reminders'),[(0,)])

    def test_backup_restores_events_reminders_and_fk_enforcement(self):
        row=self.event(task_id=self.task()['id']);self.reminder(row);self.reminders.drain(self.now)
        before=audit_database(self.settings.database)
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
        self.assertEqual(audit_database(self.root/'restored'/'database.sqlite3'),before)
        with connection(self.settings.database,write=True) as con:
            self.assertEqual(con.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
            with self.assertRaises(Exception):con.exec_driver_sql("DELETE FROM tasks")


class EventReminderAPI(AsyncAPITestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.app=create_app(self.settings);self.life=self.app.router.lifespan_context(self.app);await self.life.__aenter__()
        self.origin='http://127.0.0.1:8765'
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app,raise_app_exceptions=False),base_url=self.origin)
        result=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':self.origin})
        self.headers={'Origin':self.origin,'X-CSRF-Token':result.json()['csrf_token']}
    async def asyncTearDown(self):
        await self.client.aclose();await self.life.__aexit__(None,None,None);self.temp.cleanup()
    async def test_api_full_loop_validation_and_security(self):
        for facts in [dict(is_all_day=True,start_date='2026-11-11',end_date='2026-11-11'),
            dict(is_all_day=False,start_at='2026-11-11T09:00:00',end_at='2026-11-11T10:00:00Z'),
            dict(is_all_day=1,start_date='2026-11-11',end_date='2026-11-12'),
            dict(is_all_day=True,start_date='2026-02-30',end_date='2026-03-01')]:
            response=await self.client.post('/api/v1/events',json={'title':'secret-sentinel',**facts},headers={**self.headers,'Idempotency-Key':key()})
            self.assertEqual(response.status_code,422,response.text);self.assertNotIn('secret-sentinel',response.text)
        headers={**self.headers,'Idempotency-Key':key()}
        response=await self.client.post('/api/v1/events',json=all_day(),headers=headers)
        self.assertEqual(response.status_code,201,response.text);row=response.json()
        self.assertEqual((await self.client.post('/api/v1/events',json=all_day(),headers=headers)).status_code,200)
        self.assertEqual((await self.client.get('/api/v1/events?start_date=2026-11-11&end_date=2026-11-12')).json()['total'],1)
        self.assertEqual((await self.client.get('/api/v1/events/export')).json()['events'][0]['id'],row['id'])
        remind=await self.client.post('/api/v1/reminders',json={'resource_id':row['id'],'expected_resource_version':1,'remind_at':'2026-11-11T10:00:00Z'},headers={**self.headers,'Idempotency-Key':key()})
        self.assertEqual(remind.status_code,201,remind.text)
        # GET inbox never triggers writes.
        self.assertEqual((await self.client.get('/api/v1/reminders')).json()['total'],0)
        ReminderService(self.settings.database).drain(datetime(2026,11,11,11,tzinfo=timezone.utc))
        inbox=(await self.client.get('/api/v1/reminders')).json()['items'][0]
        ack=await self.client.post('/api/v1/reminders/'+inbox['id']+'/ack',json={'expected_version':inbox['version']},headers=self.headers)
        self.assertEqual(ack.status_code,200,ack.text)
        self.assertEqual((await self.client.get('/api/v1/reminders')).json()['total'],0)
        edit=await self.client.put('/api/v1/events/'+row['id'],json={**all_day(), 'expected_version':1,'status':'completed'},headers=self.headers)
        self.assertEqual(edit.status_code,200,edit.text)
        self.assertEqual((await self.client.put('/api/v1/events/'+row['id'],json={**all_day(),'expected_version':1},headers=self.headers)).status_code,409)
        self.assertEqual((await self.client.post('/api/v1/events/'+row['id']+'/trash',json={'expected_version':2},headers=self.headers)).status_code,200)
        self.assertEqual((await self.client.post('/api/v1/events/'+row['id']+'/restore',json={'expected_version':3},headers=self.headers)).status_code,200)
        self.assertEqual((await self.client.get('/api/v1/reminders/export')).status_code,200)
        self.assertEqual((await self.client.post('/api/v1/reminders/'+inbox['id']+'/ack',json={'expected_version':inbox['version']},headers={'Origin':self.origin})).status_code,403)
        self.client.cookies.clear()
        for path in ['/api/v1/events/'+row['id'],'/api/v1/events/export','/api/v1/reminders','/api/v1/reminders/export']:
            self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_worker_shutdown_waits_for_inflight_transaction_before_releasing_ownership(self):
        settings=Settings(data_root=self.settings.data_root,legacy_database=self.settings.legacy_database)
        started=threading.Event();release=threading.Event();finished=threading.Event()
        def transaction():
            started.set()
            if not release.wait(5):raise RuntimeError('test timeout')
            finished.set()
        worker=ReminderWorker(settings)
        with mock.patch.object(ReminderService,'drain',side_effect=transaction):
            await worker.start()
            for _ in range(100):
                if started.is_set():break
                await asyncio.sleep(.01)
            self.assertTrue(started.is_set())
            closing_task=asyncio.create_task(worker.stop());await asyncio.sleep(.03)
            self.assertFalse(closing_task.done());self.assertIsNotNone(worker.descriptor)
            other=ReminderWorker(settings);await other.start();self.assertEqual(other.state,'standby')
            release.set();await closing_task;await other.stop()
        self.assertTrue(finished.is_set());self.assertIsNone(worker.descriptor)

    async def test_worker_ownership_shutdown_and_restart_catchup(self):
        settings=Settings(data_root=self.settings.data_root,legacy_database=self.settings.legacy_database)
        worker=ReminderWorker(settings,interval=.02);other=ReminderWorker(settings,interval=.02)
        row=TaskService(settings.database).create(TaskCreate(title='后台提醒'),key())[0]
        reminder=ReminderService(settings.database).create(ReminderCreate(resource_id=row['id'],expected_resource_version=1,
            remind_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()),key())[0]
        await worker.start();await other.start()
        self.assertEqual(other.state,'standby')
        for _ in range(100):
            if worker.state=='running':break
            await asyncio.sleep(.02)
        self.assertEqual(worker.state,'running')
        await worker.stop();await other.stop()
        self.assertEqual(ReminderService(settings.database).list()['total'],1)
        replacement=ReminderWorker(settings,interval=.02);await replacement.start();await replacement.stop()
        self.assertIsNone(replacement.descriptor)
        self.assertEqual(ReminderService(settings.database).list()['total'],1)
        with closing(sqlite3.connect(settings.database)) as con:
            con.execute("UPDATE alembic_version SET version_num='future'");con.commit()
        failed=ReminderWorker(settings,interval=.02);await failed.start()
        for _ in range(100):
            if failed.state=='degraded':break
            await asyncio.sleep(.02)
        await failed.stop();self.assertEqual(failed.state,'degraded')


class PopulatedMigration(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
        initialize_empty(self.settings.data_root)
        # Build the released 0001 database with genuine previous migration code.
        with connection(self.settings.database,write=True,require_current=False) as con:
            config=Config(str(PROJECT_ROOT/'backend'/'alembic.ini'));config.attributes['connection']=con
            command.upgrade(config,'0001_tasks')
            con.exec_driver_sql("INSERT INTO resources VALUES ('r1','task','虚构原任务',NULL,'personal','2026-11-11','2026-11-11',NULL,1)")
            con.exec_driver_sql("INSERT INTO tasks(resource_id,description,status,priority,timezone,position) VALUES ('r1','完整保留','todo',2,'Asia/Shanghai',0)")
            con.exec_driver_sql("INSERT INTO activities VALUES ('a1','r1','created','user','2026-11-11','personal','{}')")
            con.exec_driver_sql("INSERT INTO operation_requests VALUES ('o1','key1','hash1','task.create','applied','{}',NULL,'2026-11-11','2026-11-11')")
    def tearDown(self):self.temp.cleanup()
    def test_populated_parent_table_copy_preserves_every_typed_row(self):
        self.assertEqual(database_status(self.settings.database),'upgrade_required')
        before=audit_database(self.settings.database);upgrade(self.settings);after=audit_database(self.settings.database)
        for name in ['resources','tasks','activities','operation_requests']:self.assertEqual(before['tables'][name],after['tables'][name])
        self.assertEqual(after['foreign_key_errors'],0)
        with connection(self.settings.database) as con:self.assertEqual(con.exec_driver_sql('PRAGMA foreign_keys').scalar(),1)
    def test_populated_upgrade_failure_after_real_ddl_rolls_back_all_schema_and_rows(self):
        original=command.upgrade;calls=0
        def fail(config,target):
            nonlocal calls
            calls+=1;original(config,target)
            if calls==2:raise RuntimeError('after actual copy')
        before=audit_database(self.settings.database)
        with mock.patch('backend.maintenance.command.upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(calls,2);self.assertEqual(audit_database(self.settings.database),before)
    def test_unknown_resource_trigger_refuses_upgrade_without_losing_trigger(self):
        with connection(self.settings.database,write=True,require_current=False) as con:
            con.exec_driver_sql('CREATE TRIGGER custom_trigger AFTER UPDATE ON resources BEGIN SELECT 1; END')
        before=audit_database(self.settings.database)
        with self.assertRaises(RuntimeError):upgrade(self.settings)
        self.assertEqual(audit_database(self.settings.database),before)
