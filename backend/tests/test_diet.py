"""Real SQLite quantities, authority boundaries and migrations; no live user data."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
import json
import unittest
import zipfile
import httpx
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.engine import Connection
from sqlalchemy.pool import NullPool
from alembic import command
from alembic.config import Config
from backend.manage import initialize_empty
from backend.maintenance import upgrade, backup_hub, restore_hub
from backend.app.core.config import Settings, PROJECT_ROOT
from backend.app.core.database import connection, HubError, revision, SCHEMA_REVISION
from backend.app.core.note_snapshot import SnapshotFiles, verified_snapshot
from backend.app.modules.life.diet.service import DietService
from backend.app.modules.life.diet.schemas import (IngredientWrite, RecipeWrite, RecipeItem, MealWrite, DishWrite, Version,
    ShoppingWrite, ShoppingState, ConfirmSuggestions, SuggestionSelection, StockWrite, milli)
from backend.app.modules.knowledge.service import KnowledgeService
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.knowledge.export import portable_export
from backend.app.modules.dashboard.service import DashboardService
from backend.app.modules.dashboard.schemas import PreferencesWrite
from backend.app.main import create_app
from backend.tests.async_support import AsyncAPITestCase
from scripts.data_safety import audit_database

WEEK=date(2026,11,9)
def key(): return str(uuid4())

class Fixture:
    def setup(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'unused',reminders_enabled=False)
        initialize_empty(self.settings.data_root);upgrade(self.settings)
        self.s=DietService(self.settings.database)
        self.i=self.s.ingredient_save(IngredientWrite(name='大米'),key())
        self.r=self.s.recipe_save(RecipeWrite(title='米饭',base_servings=2,ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='0.2',unit='kg')]),key())
    def cleanup(self):self.tmp.cleanup()
    def meal(self,**kw):
        return self.s.meal_save(MealWrite(local_date='2026-11-09',slot='lunch',recipe_id=self.r['id'],**kw),key())
    def confirm(self,preview=None,operation=None):
        p=preview or self.s.suggestions(WEEK)
        return self.s.confirm(WEEK,ConfirmSuggestions(expected_version=p['version'],digest=p['digest'],selected=[{'ingredient_id':r['ingredient_id'],'unit':r['unit']} for r in p['items'] if float(r['quantity'])>0]),operation or key())
    def error(self,code,fn):
        with self.assertRaises(HubError) as e:fn()
        self.assertEqual(e.exception.code,code)

class Diet(Fixture,unittest.TestCase):
    def setUp(self):self.setup()
    def tearDown(self):self.cleanup()

    def test_reads_are_side_effect_free_and_today_is_date_scoped(self):
        before=audit_database(self.settings.database)
        self.s.meals(WEEK);self.s.today(WEEK);self.s.recipes();self.s.ingredients();self.s.shopping(WEEK);self.s.suggestions(WEEK);self.s.stock()
        self.assertEqual(audit_database(self.settings.database),before)
        self.meal();self.assertEqual(len(self.s.today(WEEK)['items']),1)
        self.assertEqual(self.s.today(date(2026,11,10))['items'],[])
        self.error('DIET_MONDAY_REQUIRED',lambda:self.s.meals(date(2026,11,10)))

    def test_fixed_decimal_and_input_validation_rejects_floats_exponents_duplicate_compatible_units(self):
        self.assertEqual(milli('0.001'),1)
        for invalid in ('1e3','NaN','-1','01','1.0001','1000000.001',1.2,True):
            with self.assertRaises((ValueError,ValidationError)):
                RecipeItem(ingredient_id=self.i['id'],quantity=invalid,unit='g')
        for invalid in ('0','0.000'):
            with self.assertRaises(ValidationError):RecipeItem(ingredient_id=self.i['id'],quantity=invalid,unit='g')
        with self.assertRaises(ValidationError):
            RecipeWrite(title='bad',ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='1',unit=u) for u in ('kg','g')])
        with self.assertRaises(ValidationError):StockWrite(ingredient_id=self.i['id'],unit='kg',quantity='1',expected_version=0)
        for value in (1,'true'):
            with self.assertRaises(ValidationError):RecipeWrite(title='bad',favorite=value)

    def test_names_normalization_no_automatic_merge_and_cas_idempotence(self):
        p=IngredientWrite(name='  Ｍｉｌｋ   粉 ');op=key()
        a=self.s.ingredient_save(p,op);self.assertEqual(a['name'],'Milk 粉');self.assertEqual(self.s.ingredient_save(p,op),a)
        self.error('INGREDIENT_NAME_EXISTS',lambda:self.s.ingredient_save(IngredientWrite(name='milk 粉'),key()))
        self.s.ingredient_save(IngredientWrite(name='米',expected_version=1),key(),self.i['id'])
        self.assertEqual(self.s.recipe(self.r['id'])['ingredients'][0]['name'],'米')
        self.error('VERSION_CONFLICT',lambda:self.s.ingredient_save(IngredientWrite(name='旧改名',expected_version=1),key(),self.i['id']))
        self.error('IDEMPOTENCY_CONFLICT',lambda:self.s.ingredient_save(IngredientWrite(name='different'),op))

    def test_rounded_servings_conversion_and_incompatible_units_stay_separate(self):
        recipe=self.s.recipe_save(RecipeWrite(title='分份',base_servings=3,ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='1',unit=u) for u in ('g','ml','piece','portion')]),key())
        self.meal(servings=1)
        self.s.meal_save(MealWrite(local_date=WEEK,slot='dinner',recipe_id=recipe['id'],servings=1),key())
        out={r['unit']:r for r in self.s.suggestions(WEEK)['items']}
        self.assertEqual(set(out),{'g','ml','piece','portion'})
        self.assertEqual(out['g']['required'],'100.334')
        for unit in ('ml','piece','portion'):self.assertEqual(out[unit]['required'],'0.334')
        liquid=self.s.recipe_save(RecipeWrite(title='水',ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='0.001',unit='l')]),key())
        self.s.meal_save(MealWrite(local_date=WEEK,slot='breakfast',recipe_id=liquid['id']),key())
        self.assertEqual(next(r for r in self.s.suggestions(WEEK)['items'] if r['unit']=='ml')['required'],'1.334')

    def test_full_plan_stock_confirm_purchase_edit_and_menu_changes_never_overwrite_facts(self):
        self.s.stock_save(StockWrite(ingredient_id=self.i['id'],quantity='25',unit='g',expected_version=0),key())
        self.meal(servings=2);p=self.s.suggestions(WEEK);self.assertEqual(p['items'][0]['quantity'],'175')
        listing=self.confirm(p);item=listing['items'][0];self.assertEqual(item['quantity'],'175');self.assertEqual(item['origin'],'menu')
        listing=self.s.shopping_save(WEEK,ShoppingWrite(ingredient_id=self.i['id'],quantity='180',unit='g',note='买常用品牌',purchased=True,expected_version=listing['version']),key(),item['id'])
        old=list(listing['items']);self.meal(servings=1)
        self.assertEqual(self.s.shopping(WEEK)['items'],old)
        p=self.s.suggestions(WEEK);self.assertEqual(p['items'][0]['quantity'],'95');self.assertEqual(p['items'][0]['confirmed'],'180')
        self.assertEqual(self.s.stock()['items'][0]['quantity'],'25')
        self.s.recipe_save(RecipeWrite(title='饭',expected_version=1,ingredients=[]),key(),self.r['id'])
        self.assertEqual(self.s.suggestions(WEEK)['items'],[]);self.assertEqual(self.s.shopping(WEEK)['items'],old)

    def test_preview_stale_for_menu_recipe_name_stock_or_list_changes(self):
        self.meal()
        changes=[
            lambda:self.meal(),
            lambda:self.s.recipe_save(RecipeWrite(title='新饭',expected_version=1,ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='100',unit='g')]),key(),self.r['id']),
            lambda:self.s.ingredient_save(IngredientWrite(name='米',expected_version=1),key(),self.i['id']),
            lambda:self.s.stock_save(StockWrite(ingredient_id=self.i['id'],quantity='1',unit='g',expected_version=0),key()),
            lambda:self.s.shopping_save(WEEK,ShoppingWrite(ingredient_id=self.i['id'],quantity='1',unit='g',expected_version=0),key())]
        for change in changes:
            p=self.s.suggestions(WEEK);change();before=audit_database(self.settings.database)
            self.error('DIET_PREVIEW_STALE',lambda:self.confirm(p))
            self.assertEqual(audit_database(self.settings.database),before)

    def test_double_confirm_and_parallel_confirms_add_once(self):
        self.meal();p=self.s.suggestions(WEEK);op=key();a=self.confirm(p,op);self.assertEqual(self.confirm(p,op),a)
        self.error('DIET_PREVIEW_STALE',lambda:self.confirm(p))
        self.assertEqual(self.s.suggestions(WEEK)['items'][0]['quantity'],'0')
        self.meal();p=self.s.suggestions(WEEK);barrier=Barrier(2)
        def run(_):
            barrier.wait(timeout=5)
            try:self.confirm(p);return 'ok'
            except HubError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(run,range(2)))
        self.assertEqual(sorted(results),['DIET_PREVIEW_STALE','ok'])
        self.assertEqual(len(self.s.shopping(WEEK)['items']),2)

    def test_shopping_remove_restore_manual_units_and_cas(self):
        self.meal();listing=self.s.shopping_save(WEEK,ShoppingWrite(ingredient_id=self.i['id'],quantity='0.05',unit='kg',expected_version=0),key());sid=listing['items'][0]['id']
        self.assertEqual(self.s.suggestions(WEEK)['items'][0]['quantity'],'50')
        self.error('VERSION_CONFLICT',lambda:self.s.shopping_save(WEEK,ShoppingWrite(ingredient_id=self.i['id'],quantity='99',unit='g',expected_version=0),key(),sid))
        listing=self.s.shopping_state(WEEK,sid,ShoppingState(expected_version=1,removed=True),key())
        self.assertEqual(self.s.suggestions(WEEK)['items'][0]['quantity'],'100')
        listing=self.s.shopping_state(WEEK,sid,ShoppingState(expected_version=2,removed=False),key())
        self.assertEqual(listing['items'][0]['quantity'],'0.05');self.assertEqual(self.s.suggestions(WEEK)['items'][0]['quantity'],'50')

    def test_stock_manual_absolute_zero_and_parallel_cas(self):
        p=StockWrite(ingredient_id=self.i['id'],quantity='10',unit='g',expected_version=0);op=key()
        self.s.stock_save(p,op);self.s.stock_save(p,op)
        self.assertEqual(self.s.stock()['items'][0]['quantity'],'10')
        barrier=Barrier(2)
        def update(value):
            barrier.wait(timeout=5)
            try:self.s.stock_save(StockWrite(ingredient_id=self.i['id'],quantity=value,unit='g',expected_version=1),key());return 'ok'
            except HubError as e:return e.code
        with ThreadPoolExecutor(max_workers=2) as pool:result=list(pool.map(update,['2','3']))
        self.assertEqual(sorted(result),['VERSION_CONFLICT','ok'])
        self.s.stock_save(StockWrite(ingredient_id=self.i['id'],quantity='0',unit='g',expected_version=2),key())
        self.assertEqual(self.s.stock()['items'][0]['quantity'],'0')

    def test_recipe_save_is_atomic_unknown_ingredient_and_version_conflict(self):
        before=audit_database(self.settings.database)
        bad=RecipeWrite(title='no partial',expected_version=1,ingredients=[RecipeItem(ingredient_id=key(),quantity='1',unit='g')])
        self.error('INGREDIENT_NOT_FOUND',lambda:self.s.recipe_save(bad,key(),self.r['id']))
        self.assertEqual(audit_database(self.settings.database),before)
        self.error('VERSION_CONFLICT',lambda:self.s.recipe_save(RecipeWrite(title='stale',expected_version=0),key(),self.r['id']))
        self.assertEqual(audit_database(self.settings.database),before)

    def test_meal_edit_remove_idempotence_and_stock_unchanged(self):
        meal=self.meal();p=MealWrite(local_date='2026-11-10',slot='dinner',recipe_id=self.r['id'],servings=2,expected_version=1);op=key()
        a=self.s.meal_save(p,op,meal['id']);self.assertEqual(self.s.meal_save(p,op,meal['id']),a)
        self.error('VERSION_CONFLICT',lambda:self.s.meal_save(p,key(),meal['id']))
        op=key();self.s.meal_remove(meal['id'],Version(expected_version=2),op);self.s.meal_remove(meal['id'],Version(expected_version=2),op)
        self.assertEqual(self.s.meals(WEEK)['items'],[]);self.assertEqual(self.s.stock()['items'],[])

    def test_private_or_trashed_recipe_filters_menu_suggestions_search_export_and_replay(self):
        meal=self.meal();p=self.s.suggestions(WEEK);self.confirm(p)
        ordinary_shopping=self.s.shopping(WEEK)
        request=key();payload=RecipeWrite(title='普通食谱',expected_version=1,ingredients=[]);self.s.recipe_save(payload,request,self.r['id'])
        SearchService(self.settings.database).rebuild()
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(self.r['id'],))
        self.assertEqual(self.s.recipes()['total'],0);self.assertEqual(self.s.meals(WEEK)['items'],[]);self.assertEqual(self.s.suggestions(WEEK)['items'],[])
        self.error('RESOURCE_NOT_FOUND',lambda:self.s.recipe_save(payload,request,self.r['id']))
        self.error('RESOURCE_NOT_FOUND',lambda:self.s.meal_remove(meal['id'],Version(expected_version=1),key()))
        self.assertEqual(SearchService(self.settings.database).query('普通食谱')['total'],0)
        with zipfile.ZipFile(BytesIO(portable_export(self.settings.database))) as z:data=json.loads(z.read('data.json'))['tables']
        self.assertEqual(data['recipes'],[]);self.assertEqual(data['recipe_ingredients'],[]);self.assertEqual(data['meal_plans'],[])
        self.assertEqual(self.s.shopping(WEEK),ordinary_shopping)
        with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='personal' WHERE id=?",(self.r['id'],))
        self.s.recipe_trash(self.r['id'],Version(expected_version=2),key())
        self.assertEqual(self.s.meals(WEEK)['items'],[]);self.assertEqual(self.s.recipes(trash=True)['total'],1)
        self.s.recipe_trash(self.r['id'],Version(expected_version=3),key(),True)
        self.assertEqual(len(self.s.meals(WEEK)['items']),1)

    def test_recipe_resource_search_and_full_export_restore_nonempty_graph(self):
        self.meal();self.confirm();self.s.stock_save(StockWrite(ingredient_id=self.i['id'],quantity='2',unit='piece',expected_version=0),key())
        self.assertEqual(KnowledgeService(self.settings.database).get(self.r['id'])['kind'],'recipe')
        SearchService(self.settings.database).rebuild();self.assertEqual(SearchService(self.settings.database).query('米饭',kind='recipe')['total'],1)
        with zipfile.ZipFile(BytesIO(portable_export(self.settings.database))) as z:data=json.loads(z.read('data.json'))['tables']
        for table in ('recipes','diet_ingredients','recipe_ingredients','meal_plans','shopping_lists','shopping_items','inventory_balances'):self.assertEqual(len(data[table]),1,table)
        backup=self.root/'snapshot';backup_hub(self.settings,backup);restore_hub(self.settings,backup,self.root/'restore')
        self.assertEqual(audit_database(self.root/'restore/db/hub.db'),audit_database(self.settings.database))
        self.assertEqual(SnapshotFiles(self.root/'restore').inventory(),SnapshotFiles(self.settings.data_root).inventory())
        manifest=backup/'note-files-manifest.json'
        # require v3 for the new revision as for earlier file-bearing revisions
        for path in backup.glob('*.json'):
            value=json.loads(path.read_text())
            if value.get('format')=='personal-hub-full-snapshot':
                value['format_version']=2;path.write_text(json.dumps(value))
                self.error('NOTE_SNAPSHOT_INVALID',lambda:verified_snapshot(backup))
                break
        else:self.fail('full manifest missing')

    def test_old_dashboard_config_appends_hidden_meals_without_writing(self):
        d=DashboardService(self.settings.database);prefs=d.get();self.assertFalse(next(w for w in prefs['widgets'] if w['id']=='meals')['enabled'])
        payload=PreferencesWrite(widgets=prefs['widgets'][:-1],shortcuts=prefs['shortcuts'],expected_version=0);d.save(payload,key())
        before=audit_database(self.settings.database);read=d.get()
        self.assertEqual(next(w for w in read['widgets'] if w['id']=='meals'),{'id':'meals','enabled':False});self.assertEqual(audit_database(self.settings.database),before)

class DietMigration(unittest.TestCase):
    def old(self,root):
        s=Settings(data_root=root/'hub',legacy_database=root/'unused');initialize_empty(s.data_root)
        engine=create_engine('sqlite+pysqlite:///'+str(s.database),poolclass=NullPool)
        with engine.begin() as c:
            config=Config(str(PROJECT_ROOT/'backend/alembic.ini'));config.attributes['connection']=c;command.upgrade(config,'0009_project_tools')
            c.exec_driver_sql("INSERT INTO resources(id,kind,title,privacy_level,created_at,updated_at,version) VALUES('old','project','keep','personal','2026-11-12','2026-11-12',1)")
            c.exec_driver_sql("INSERT INTO projects(resource_id) VALUES('old')")
            c.exec_driver_sql('CREATE INDEX ix_diet_fixture ON resources(lower(title))')
            c.exec_driver_sql('CREATE TRIGGER diet_fixture AFTER UPDATE ON projects BEGIN SELECT 1; END')
        engine.dispose();return s

    def test_nonempty_0009_preserves_all_old_rows_expression_index_and_trigger(self):
        with TemporaryDirectory() as t:
            s=self.old(Path(t));before=audit_database(s.database);self.assertEqual(upgrade(s)['rehearsal'],'passed');after=audit_database(s.database)
            for name,facts in before['tables'].items():
                if name!='alembic_version':self.assertEqual(after['tables'][name],facts,name)
            with connection(s.database) as c:
                self.assertIsNotNone(c.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name='ix_diet_fixture'").first())
                self.assertIsNotNone(c.exec_driver_sql("SELECT 1 FROM sqlite_schema WHERE name='diet_fixture'").first())
            self.assertEqual(revision(s.database),SCHEMA_REVISION)

    def test_late_0010_failure_rolls_back_parent_rebuild_new_tables_and_revision(self):
        with TemporaryDirectory() as t:
            s=self.old(Path(t));before=audit_database(s.database);execute=Connection.exec_driver_sql
            def fail(c,sql,*args,**kwargs):
                if 'CREATE TABLE inventory_balances' in sql:raise RuntimeError('injected late failure')
                return execute(c,sql,*args,**kwargs)
            with patch.object(Connection,'exec_driver_sql',fail):
                with self.assertRaises(RuntimeError):upgrade(s)
            self.assertEqual(audit_database(s.database),before);self.assertEqual(revision(s.database),'0009_project_tools')

class DietAPI(Fixture,AsyncAPITestCase):
    async def asyncSetUp(self):
        self.setup();self.app=create_app(self.settings)
        self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1:8765')
        response=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':'http://127.0.0.1:8765'})
        self.headers={'Origin':'http://127.0.0.1:8765','X-CSRF-Token':response.json()['csrf_token'],'Idempotency-Key':key()}
    async def asyncTearDown(self):await self.client.aclose();self.cleanup()
    async def test_api_validation_security_and_explicit_confirmation(self):
        p={'local_date':'2026-11-09','slot':'dinner','recipe_id':self.r['id'],'servings':1}
        response=await self.client.post('/api/v1/diet/meals',json=p,headers=self.headers);self.assertEqual(response.status_code,200)
        for _ in range(2):self.assertEqual((await self.client.post('/api/v1/diet/meals',json=p,headers=self.headers)).status_code,200)
        self.assertEqual(len(self.s.meals(WEEK)['items']),1)
        response=await self.client.get('/api/v1/diet/suggestions?week_start=2026-11-09');self.assertEqual(response.status_code,200);preview=response.json()
        body={'digest':preview['digest'],'expected_version':0,'selected':[{'ingredient_id':self.i['id'],'unit':'g'}]}
        response=await self.client.post('/api/v1/diet/suggestions/2026-11-09/confirm',json=body,headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(response.status_code,200)
        self.assertEqual((await self.client.get('/api/v1/diet/meals?week_start=2026-11-10')).status_code,422)
        self.assertEqual((await self.client.post('/api/v1/diet/meals',json={**p,'servings':True},headers=self.headers)).status_code,422)
        self.assertEqual((await self.client.post('/api/v1/diet/meals',json=p,headers={'Origin':'http://127.0.0.1:8765'})).status_code,403)
        self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/diet/stock')).status_code,401)

class LargeShopping(Fixture,unittest.TestCase):
    def setUp(self):self.setup()
    def tearDown(self):self.cleanup()
    def test_generated_canonical_large_quantity_remains_editable(self):
        self.s.recipe_save(RecipeWrite(title='大份',expected_version=1,ingredients=[RecipeItem(ingredient_id=self.i['id'],quantity='2000',unit='kg')]),key(),self.r['id'])
        self.meal();p=self.s.suggestions(WEEK);self.assertEqual(p['items'][0]['quantity'],'2000000')
        result=self.confirm(p);item=result['items'][0]
        result=self.s.shopping_save(WEEK,ShoppingWrite(ingredient_id=self.i['id'],quantity=item['quantity'],unit='g',note='改备注',expected_version=result['version']),key(),item['id'])
        self.assertEqual(result['items'][0]['quantity'],'2000000');self.assertEqual(result['items'][0]['note'],'改备注')


class MealDishTests(unittest.TestCase):
    """Meals record what will be eaten, independent of saved recipes."""

    def setUp(self):
        self.tmp = TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root/'hub', legacy_database=self.root/'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.s = DietService(self.settings.database)

    def tearDown(self): self.tmp.cleanup()

    def test_meal_saved_before_dishes_existed_hides_with_its_trashed_recipe(self):
        """Pre-0015 meals carry only meal_plans.recipe_id and have no meal_dishes rows."""
        ingredient = self.s.ingredient_save(IngredientWrite(name='大米'), key())['id']
        recipe = self.s.recipe_save(RecipeWrite(title='米饭', base_servings=2, ingredients=[RecipeItem(ingredient_id=ingredient, quantity='0.2', unit='kg')]), key())
        meal = self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', recipe_id=recipe['id']), key())
        with connection(self.settings.database, write=True) as c:
            c.exec_driver_sql('DELETE FROM meal_dishes WHERE meal_id=?', (meal['id'],))
        self.assertEqual(len(self.s.meals(WEEK)['items']), 1, 'a legacy meal is shown while its recipe is live')
        self.assertEqual(len(self.s.suggestions(WEEK)['items']), 1)
        self.s.recipe_trash(recipe['id'], Version(expected_version=1), key())
        self.assertEqual(self.s.meals(WEEK)['items'], [], 'a trashed recipe hides the legacy meal like any other')
        with self.assertRaises(HubError) as hidden:
            self.s.meal_remove(meal['id'], Version(expected_version=1), key())
        self.assertEqual(hidden.exception.code, 'RESOURCE_NOT_FOUND')
        self.assertEqual(self.s.suggestions(WEEK)['items'], [], 'the shopping suggestion for the week must still be buildable')
        self.s.recipe_trash(recipe['id'], Version(expected_version=2), key(), True)
        self.assertEqual(len(self.s.meals(WEEK)['items']), 1, 'restoring the recipe brings the meal back')

    def test_free_text_dish_is_stored_and_returned_without_a_recipe(self):
        meal = self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(label='番茄炒蛋')]), key())
        self.assertEqual([d['name'] for d in meal['dishes']], ['番茄炒蛋'])
        self.assertEqual(meal['title'], '番茄炒蛋')
        self.assertIsNone(meal['dishes'][0]['recipe_id'])
        week = self.s.meals(WEEK)['items']
        self.assertEqual([d['name'] for d in week[0]['dishes']], ['番茄炒蛋'])

    def test_several_dishes_keep_their_order_and_mix_sources(self):
        ingredient = self.s.ingredient_save(IngredientWrite(name='米饭'), key())['id']
        recipe = self.s.recipe_save(RecipeWrite(title='米饭', ingredients=[RecipeItem(ingredient_id=ingredient, quantity='1', unit='piece')]), key())
        meal = self.s.meal_save(MealWrite(local_date=WEEK, slot='dinner', dishes=[
            DishWrite(label='清炒时蔬'), DishWrite(recipe_id=recipe['id']), DishWrite(label='紫菜汤')]), key())
        self.assertEqual([d['name'] for d in meal['dishes']], ['清炒时蔬', '米饭', '紫菜汤'])
        self.assertEqual([d['position'] for d in meal['dishes']], [0, 1, 2])

    def test_editing_replaces_the_dish_list_as_a_whole(self):
        meal = self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(label='旧菜')]), key())
        updated = self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(label='新菜一'), DishWrite(label='新菜二')], expected_version=1), key(), meal['id'])
        self.assertEqual([d['name'] for d in updated['dishes']], ['新菜一', '新菜二'])

    def test_meal_requires_food(self):
        with self.assertRaises(Exception):
            MealWrite(local_date=WEEK, slot='lunch')
        with self.assertRaises(Exception):
            DishWrite(recipe_id=uuid4(), label='同时填写')
        with self.assertRaises(Exception):
            DishWrite()

    def test_free_text_dishes_never_create_purchase_suggestions(self):
        self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(label='外面吃')]), key())
        self.assertEqual(self.s.suggestions(WEEK)['items'], [])

    def test_recipe_dishes_still_drive_purchase_suggestions(self):
        ingredient = self.s.ingredient_save(IngredientWrite(name='鸡蛋'), key())['id']
        recipe = self.s.recipe_save(RecipeWrite(title='炒蛋', base_servings=1, ingredients=[RecipeItem(ingredient_id=ingredient, quantity='2', unit='piece')]), key())
        self.s.meal_save(MealWrite(local_date=WEEK, slot='breakfast', dishes=[DishWrite(recipe_id=recipe['id'])]), key())
        items = self.s.suggestions(WEEK)['items']
        self.assertEqual([(i['name'], i['required']) for i in items], [('鸡蛋', '2')])

    def test_trashing_a_referenced_recipe_hides_the_meal_but_keeps_others(self):
        ingredient = self.s.ingredient_save(IngredientWrite(name='米'), key())['id']
        recipe = self.s.recipe_save(RecipeWrite(title='米饭', ingredients=[RecipeItem(ingredient_id=ingredient, quantity='1', unit='piece')]), key())
        self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(recipe_id=recipe['id'])]), key())
        self.s.meal_save(MealWrite(local_date=WEEK, slot='dinner', dishes=[DishWrite(label='面条')]), key())
        self.s.recipe_trash(recipe['id'], Version(expected_version=recipe['version']), key())
        meals = self.s.meals(WEEK)['items']
        self.assertEqual([m['slot'] for m in meals], ['dinner'])
        self.assertEqual([d['name'] for d in meals[0]['dishes']], ['面条'])


class StockRemovalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory(); self.root = Path(self.tmp.name)
        self.settings = Settings(data_root=self.root/'hub', legacy_database=self.root/'legacy', reminders_enabled=False)
        initialize_empty(self.settings.data_root); upgrade(self.settings)
        self.s = DietService(self.settings.database)
        self.ing = self.s.ingredient_save(IngredientWrite(name='牛奶'), key())['id']

    def tearDown(self): self.tmp.cleanup()

    def _stock(self, quantity='2', unit='piece'):
        return self.s.stock_save(StockWrite(ingredient_id=self.ing, quantity=quantity, unit=unit, expected_version=0), key())

    def test_replaying_a_save_after_the_row_was_removed_reports_removal_not_a_server_error(self):
        request = key()
        saved = self.s.stock_save(StockWrite(ingredient_id=self.ing, quantity='2', unit='piece', expected_version=0), request)
        self.s.stock_remove(saved['id'], Version(expected_version=saved['version']), key())
        again = self.s.stock_save(StockWrite(ingredient_id=self.ing, quantity='2', unit='piece', expected_version=0), request)
        self.assertEqual(again, {'id': saved['id'], 'status': 'removed'})

    def test_delete_removes_only_that_row(self):
        row = self._stock()
        result = self.s.stock_remove(row['id'], Version(expected_version=row['version']), key())
        self.assertEqual(result['status'], 'removed')
        self.assertEqual(self.s.stock()['items'], [])

    def test_delete_is_idempotent_for_the_same_key(self):
        row = self._stock(); op = key()
        first = self.s.stock_remove(row['id'], Version(expected_version=row['version']), op)
        self.assertEqual(self.s.stock_remove(row['id'], Version(expected_version=row['version']), op), first)

    def test_stale_version_cannot_delete(self):
        row = self._stock()
        with self.assertRaises(HubError) as error:
            self.s.stock_remove(row['id'], Version(expected_version=row['version'] + 5), key())
        self.assertEqual(error.exception.code, 'VERSION_CONFLICT')
        self.assertEqual(len(self.s.stock()['items']), 1)

    def test_missing_row_is_reported(self):
        with self.assertRaises(HubError) as error:
            self.s.stock_remove(str(uuid4()), Version(expected_version=1), key())
        self.assertEqual(error.exception.code, 'STOCK_NOT_FOUND')

    def test_deleting_stock_does_not_touch_confirmed_shopping(self):
        row = self._stock()
        ingredient = self.s.ingredient_save(IngredientWrite(name='鸡蛋'), key())['id']
        recipe = self.s.recipe_save(RecipeWrite(title='炒蛋', ingredients=[RecipeItem(ingredient_id=ingredient, quantity='2', unit='piece')]), key())
        self.s.meal_save(MealWrite(local_date=WEEK, slot='lunch', dishes=[DishWrite(recipe_id=recipe['id'])]), key())
        preview = self.s.suggestions(WEEK)
        self.s.confirm(WEEK, ConfirmSuggestions(expected_version=preview['version'], digest=preview['digest'],
                                                selected=[SuggestionSelection(ingredient_id=ingredient, unit='piece')]), key())
        before = self.s.shopping(WEEK)
        self.s.stock_remove(row['id'], Version(expected_version=row['version']), key())
        self.assertEqual(self.s.shopping(WEEK), before)
