from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
from io import BytesIO
import unittest,json,zipfile
from pydantic import ValidationError
from backend.app.core.config import Settings
from backend.app.core.database import connection,HubError
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from backend.app.modules.life.finance.schemas import CategoryWrite,EntryWrite,BudgetWrite,Version,minor
from backend.app.modules.life.finance.service import FinanceService,state
from backend.app.modules.life.exercise.schemas import PlanWrite,WorkoutWrite
from backend.app.modules.life.exercise.service import ExerciseService
from backend.app.modules.knowledge.export import portable_export
from backend.app.modules.knowledge.search import SearchService
from scripts.data_safety import audit_database

def key():return str(uuid4())
class FinanceExerciseTests(unittest.TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'unused',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings);self.db=self.settings.database
        self.f=FinanceService(self.db);self.e=ExerciseService(self.db)
        self.category=self.f.category_save(CategoryWrite(name='生活',direction='expense'),key())
    def tearDown(self):self.tmp.cleanup()
    def entry(self,**kw):return EntryWrite(local_date='2026-11-12',category_id=self.category['id'],amount='0.10',**kw)
    def test_money_exact_and_currency_precision(self):
        for n in ['-1','1e2','nan','1.001','01','0']:
            with self.assertRaises(ValueError):minor(n,'CNY')
        with self.assertRaises(ValueError):minor('1.0','JPY')
        for n in [True,1,1.5]:
            with self.assertRaises(ValidationError):self.entry(amount=n) if False else EntryWrite(local_date='2026-11-12',category_id=self.category['id'],amount=n)
        self.assertEqual(minor('0.10','CNY'),10)
        self.f.save('finance_entries',self.entry(),key());self.f.save('finance_entries',self.entry(currency='USD'),key())
        totals=self.f.report('2026-11')['totals'];self.assertEqual([x['expense'] for x in totals],['0.10','0.10']);self.assertEqual(len(totals),2)
    def test_trashing_a_trashed_entry_or_restoring_a_live_one_is_a_state_conflict(self):
        entry=self.f.save('finance_entries',self.entry(),key())
        trashed=state(self.db,'finance_entries',entry['id'],Version(expected_version=entry['version']),key(),False)
        self.assertTrue(trashed['deleted_at'])
        with self.assertRaises(HubError) as again:
            state(self.db,'finance_entries',entry['id'],Version(expected_version=trashed['version']),key(),False)
        self.assertEqual(again.exception.code,'LOCAL_RECORD_STATE_CONFLICT')
        live=state(self.db,'finance_entries',entry['id'],Version(expected_version=trashed['version']),key(),True)
        self.assertIsNone(live['deleted_at'])
        with self.assertRaises(HubError) as redundant:
            state(self.db,'finance_entries',entry['id'],Version(expected_version=live['version']),key(),True)
        self.assertEqual(redundant.exception.code,'LOCAL_RECORD_STATE_CONFLICT')
        with connection(self.db) as c:
            from backend.app.core.local_records import get
            self.assertEqual(get(c,'finance_entries',entry['id'])['version'],live['version'],'a refused call changes nothing')
    def test_month_queries_include_first_and_last_day_and_exclude_neighbours(self):
        for day in ('2026-10-31','2026-11-01','2026-11-30','2026-12-01'):
            self.f.save('finance_entries',EntryWrite(local_date=day,category_id=self.category['id'],amount='1.00'),key())
        listed=self.f.listing('2026-11')
        self.assertEqual(sorted(x['local_date'] for x in listed['items']),['2026-11-01','2026-11-30'])
        self.assertEqual(listed['total'],2)
        self.assertEqual(self.f.report('2026-11')['totals'][0]['expense'],'2.00')
        december=self.f.listing('2026-12');self.assertEqual([x['local_date'] for x in december['items']],['2026-12-01'])
        from backend.app.modules.life.finance.schemas import month_bounds
        self.assertEqual(month_bounds('2026-12'),('2026-12-01','2027-01-01'))
        with connection(self.db) as c:
            plan=' '.join(r[3] for r in c.exec_driver_sql("EXPLAIN QUERY PLAN SELECT * FROM finance_entries WHERE local_date>=? AND local_date<?",month_bounds('2026-11')))
        self.assertIn('ix_finance_month',plan,'the month query must use the date index, not scan the table')
    def test_budget_includes_empty_and_exact_remaining(self):
        budget=BudgetWrite(month='2026-11',category_id=self.category['id'],amount='0.30')
        b=self.f.save('finance_budgets',budget,key());self.assertEqual(self.f.report('2026-11')['budgets'][0]['remaining'],'0.30')
        for _ in range(3):self.f.save('finance_entries',self.entry(),key())
        self.assertEqual(self.f.report('2026-11')['budgets'][0]['remaining'],'0.00')
        with self.assertRaises(HubError):self.f.save('finance_budgets',budget,key())
        b=self.f.save('finance_budgets',budget.model_copy(update={'amount':'0','expected_version':b['version']}),key(),b['id'])
        self.assertEqual(self.f.report('2026-11')['budgets'][0]['remaining'],'-0.30')
    def test_idempotency_edit_and_trash(self):
        p=self.entry();k=key();a=self.f.save('finance_entries',p,k);self.assertEqual(a,self.f.save('finance_entries',p,k))
        with self.assertRaises(HubError):self.f.save('finance_entries',p.model_copy(update={'amount':'1'}),k)
        with self.assertRaises(HubError):self.f.save('finance_entries',p,key(),a['id'])
        p=p.model_copy(update={'expected_version':a['version'],'amount':'1.20'});b=self.f.save('finance_entries',p,key(),a['id'])
        state(self.db,'finance_entries',b['id'],Version(expected_version=2),key(),False)
        self.assertEqual(self.f.report('2026-11')['totals'],[])
        self.assertEqual(self.f.listing('2026-11',True)['total'],1)
        state(self.db,'finance_entries',b['id'],Version(expected_version=3),key(),True)
        self.assertEqual(self.f.report('2026-11')['totals'][0]['expense'],'1.20')
    def test_categories_and_budgets_replay_without_duplicate(self):
        p=CategoryWrite(name='工资',direction='income');k=key();a=self.f.category_save(p,k)
        self.assertEqual(a,self.f.category_save(p,k))
        with self.assertRaises(HubError):self.f.category_save(p.model_copy(update={'direction':'expense','expected_version':1}),key(),a['id'])
        with self.assertRaises(HubError):self.f.save('finance_budgets',BudgetWrite(month='2026-11',category_id=a['id'],amount='1'),key())
        p=BudgetWrite(month='2026-11',category_id=self.category['id'],amount='1');k=key();b=self.f.save('finance_budgets',p,k)
        self.assertEqual(b,self.f.save('finance_budgets',p,k))
    def test_private_finance_never_in_ordinary_search_export_activity(self):
        self.f.save('finance_entries',self.entry(memo='FINANCE_SECRET_ABC'),key());SearchService(self.db).rebuild()
        self.assertEqual(SearchService(self.db).query('FINANCE_SECRET_ABC')['items'],[])
        z=zipfile.ZipFile(BytesIO(portable_export(self.db)));data=z.read('data.json')
        self.assertNotIn(b'FINANCE_SECRET_ABC',data);self.assertNotIn(b'finance_entries',data)
        with connection(self.db) as con:self.assertEqual(con.exec_driver_sql('SELECT count(*) FROM activities').scalar(),0)
        self.assertIn('FINANCE_SECRET_ABC',json.dumps(self.f.export()))
    def test_exercise_plans_not_actual_and_deleted_excluded(self):
        p=self.e.save('exercise_plans',PlanWrite(title='跑步',local_date='2026-11-12',duration_seconds=3600),key())
        self.assertEqual(self.e.month('2026-11')['statistics'],[])
        k=key();body=WorkoutWrite(local_date='2026-11-12',duration_seconds=1800,distance_meters=5000,plan_id=p['id'])
        r=self.e.save('exercise_records',body,k);self.assertEqual(r,self.e.save('exercise_records',body,k))
        stats=self.e.month('2026-11')['statistics'][0];self.assertEqual((stats['sessions'],stats['duration_seconds'],stats['distance_meters']),(1,1800,5000))
        state(self.db,'exercise_records',r['id'],Version(expected_version=1),key(),False)
        self.assertEqual(self.e.month('2026-11')['statistics'],[])
    def test_link_validation_and_units(self):
        with self.assertRaises(ValidationError):WorkoutWrite(local_date='2026-11-12',duration_seconds=1.5)
        with self.assertRaises(HubError):self.e.save('exercise_plans',PlanWrite(title='bad',local_date='2026-11-12',task_id=key()),key())
        self.assertEqual(self.e.month('2026-11')['plans'],[])
    def test_full_snapshot_restores_private_ledger_and_workouts(self):
        self.f.save('finance_entries',self.entry(),key());self.e.save('exercise_records',WorkoutWrite(local_date='2026-11-12',duration_seconds=30),key())
        backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restore')
        self.assertEqual(audit_database(self.db),audit_database(self.root/'restore/db/hub.db'))

    def test_additive_import_atomic_conflict_and_idempotency(self):
        from backend.app.modules.life.finance.schemas import ImportWrite
        cid,eid=key(),key()
        body=ImportWrite(categories=[{'id':cid,'name':'导入支出','direction':'expense'}],entries=[{'id':eid,'local_date':'2026-11-12','category_id':cid,'amount':'0.10'}],budgets=[])
        k=key();r=self.f.import_data(body,k);self.assertEqual(self.f.import_data(body,k),r)
        self.assertEqual(self.f.import_data(body,key())['added']['finance_entries'],0)
        before=audit_database(self.db)
        conflict=body.model_copy(update={'entries':[body.entries[0].model_copy(update={'amount':'1.00'})]})
        with self.assertRaises(HubError):self.f.import_data(conflict,key())
        self.assertEqual(audit_database(self.db),before)

    def test_new_ddl_failure_rolls_back_every_added_table(self):
        from unittest.mock import patch
        from backend.app.core.database import revision
        from backend.maintenance import _apply
        import importlib.util
        from alembic import command
        from alembic.config import Config
        from backend.app.core.config import PROJECT_ROOT
        s=Settings(data_root=self.root/'old',legacy_database=self.root/'unused')
        initialize_empty(s.data_root)
        with connection(s.database,write=True,require_current=False,enforce_foreign_keys=False) as c:
            config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0010_diet')
            c.exec_driver_sql('CREATE INDEX custom_finance_baseline ON resources(updated_at)')
        before=audit_database(s.database)
        real_upgrade=command.upgrade
        def fail(*args,**kwargs):
            real_upgrade(*args,**kwargs)
            raise RuntimeError('injected failure after DDL')
        with patch('backend.maintenance.command.upgrade',side_effect=fail):
            with self.assertRaises(RuntimeError):_apply(s.database,before)
        self.assertEqual(audit_database(s.database),before);self.assertEqual(revision(s.database),'0010_diet')
