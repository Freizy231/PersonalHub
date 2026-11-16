from datetime import datetime,timedelta,timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import json,unittest,zipfile
from io import BytesIO
from pydantic import ValidationError
from sqlalchemy import text
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from backend.app.core.config import Settings
from backend.app.core.database import connection,HubError
from backend.app.modules.learning.service import LearningService
from backend.app.modules.learning.schemas import Goal,PathWrite,Node,Assess,Session,Practice,Review
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskPatch
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate,NoteEdit
from backend.app.modules.knowledge.export import portable_export
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.dashboard.service import DashboardService
from backend.app.modules.dashboard.schemas import default_preferences
from scripts.data_safety import audit_database

def key():return str(uuid4())
class LearningFacts(unittest.TestCase):
 def setUp(self):
  self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name);self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
  initialize_empty(self.settings.data_root);upgrade(self.settings);self.s=LearningService(self.settings.database)
  self.goal=self.s.save('learning_goal',Goal(title='掌握 PE 分析',acceptance_criteria='能够独立还原导入表'),key())
  self.path=self.s.save('learning_path',PathWrite(title='逆向学习路线',goal_id=self.goal['id']),key())
  self.node=self.s.save('knowledge_node',Node(title='PE 导入表',path_id=self.path['id'],acceptance_criteria='分析一个 PE 并给出可复核报告'),key())
 def tearDown(self):self.tmp.cleanup()
 def note(self,title='实验成果'):
  return NoteService(self.settings.database).create(NoteCreate(title=title,body='已手动分析导入函数，附复核步骤。',note_kind='experiment'),key())[0]
 def test_week_boundary_is_local_midnight_expressed_in_utc(self):
  """A session just after local Monday 00:00 must count this week even when that is Sunday in UTC."""
  from datetime import date
  from unittest import mock
  today=date.today();monday=today-timedelta(days=today.weekday())
  local_midnight=datetime.combine(monday,datetime.min.time()).astimezone()
  def stamp(moment):return moment.astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')
  after=local_midnight+timedelta(minutes=5);before=local_midnight-timedelta(minutes=5)
  with connection(self.settings.database,write=True) as c:
   for moment,seconds in ((after,600),(before,60)):
    c.exec_driver_sql('INSERT INTO learning_sessions VALUES(?,?,?,?,?,?,?)',(key(),self.node['id'],stamp(moment),stamp(moment+timedelta(seconds=seconds)),seconds,None,stamp(moment)))
  self.assertEqual(self.s.overview()['study_seconds'],600,'only the session after local Monday 00:00 belongs to this week')

 def test_tree_cycle_cross_path_cas_and_order(self):
  child=self.s.save('knowledge_node',Node(title='IAT',path_id=self.path['id'],parent_id=self.node['id'],position=2),key())
  with self.assertRaises(HubError) as error:self.s.save('knowledge_node',Node(title='循环',path_id=self.path['id'],parent_id=child['id'],expected_version=1),key(),self.node['id'])
  self.assertEqual(error.exception.code,'LEARNING_CYCLE')
  other=self.s.save('learning_path',PathWrite(title='另一条路线'),key())
  with self.assertRaises(HubError):self.s.save('knowledge_node',Node(title='跨路线',path_id=other['id'],parent_id=child['id']),key())
  self.assertEqual(self.s.tree(self.path['id'])['progress']['total'],2)
  with self.assertRaises(HubError):self.s.save('knowledge_node',Node(title='旧版本',path_id=self.path['id'],expected_version=8),key(),self.node['id'])
 def test_ten_hours_never_verifies_and_verified_requires_criteria_and_real_output(self):
  ended=datetime.now(timezone.utc)-timedelta(minutes=1);started=ended-timedelta(hours=10)
  self.s.session(Session(node_id=self.node['id'],started_at=started,ended_at=ended),key())
  self.assertEqual(self.s.context(self.node['id'])['node']['mastery'],'unlearned')
  with self.assertRaises(HubError) as error:self.s.assess(self.node['id'],Assess(expected_version=1,mastery='verified',conclusion='学了十小时'),key())
  self.assertEqual(error.exception.code,'LEARNING_EVIDENCE_REQUIRED')
  empty=NoteService(self.settings.database).create(NoteCreate(title='空白成果'),key())[0]
  with self.assertRaises(HubError):self.s.assess(self.node['id'],Assess(expected_version=1,mastery='verified',conclusion='只有空笔记',evidence=[empty['id']]),key())
  note=self.note();result=self.s.assess(self.node['id'],Assess(expected_version=1,mastery='verified',conclusion='按验收标准完成复核',evidence=[note['id']]),key())
  self.assertEqual(result['node']['mastery'],'verified');self.assertEqual(result['assessments'][0]['criteria_snapshot'],self.node['acceptance_criteria'])
  self.assertEqual(self.s.tree(self.path['id'])['progress']['verified'],1)
 def test_practice_shared_today_task_atomic_idempotent_and_done_evidence(self):
  operation=key();payload=Practice(expected_version=1,title='解析 IAT',planned_date='2026-11-12',description='独立输出函数清单')
  task=self.s.practice(self.node['id'],payload,operation);self.assertEqual(self.s.practice(self.node['id'],payload,operation)['id'],task['id'])
  tasks=TaskService(self.settings.database);self.assertEqual(tasks.list(scope='today',on_date='2026-11-12')['total'],1)
  with self.assertRaises(HubError):self.s.assess(self.node['id'],Assess(expected_version=2,mastery='verified',conclusion='尚未完成',evidence=[task['id']]),key())
  task=tasks.change(task['id'],TaskPatch(expected_version=1,status='done'))
  result=self.s.assess(self.node['id'],Assess(expected_version=2,mastery='verified',conclusion='手动完成，检查输出正确',evidence=[task['id']]),key());self.assertEqual(result['assessments'][0]['evidence'][0]['evidence_type'],'practice')
 def test_assessments_immutable_snapshot_evidence_changed_and_downgrade_history(self):
  note=self.note();payload=Assess(expected_version=1,mastery='verified',conclusion='初次验收完成',evidence=[note['id']]);operation=key()
  first=self.s.assess(self.node['id'],payload,operation);self.s.assess(self.node['id'],payload,operation)
  self.assertEqual(len(self.s.context(self.node['id'])['assessments']),1)
  NoteService(self.settings.database).change(note['id'],NoteEdit(title='修改后的成果',body='补充事实',note_kind='experiment',expected_version=1,expected_hash=note['content_sha256']),key())
  current=self.s.context(self.node['id']);self.assertTrue(current['assessments'][0]['evidence'][0]['changed']);self.assertEqual(current['assessments'][0]['evidence'][0]['title_snapshot'],'实验成果')
  self.s.assess(self.node['id'],Assess(expected_version=2,mastery='practiced',conclusion='复习后发现仍有遗漏'),key())
  self.assertEqual(len(self.s.context(self.node['id'])['assessments']),2)
 def test_review_uses_markdown_idempotency_and_restore_all_relationships(self):
  payload=Review(expected_version=1,title='PE 周复盘',body='完成导入表实验；下一步研究重定位。',period_start='2026-11-09',period_end='2026-11-12');operation=key()
  result=self.s.review(self.path['id'],payload,operation);self.assertEqual(self.s.review(self.path['id'],payload,operation),result)
  self.assertEqual(NoteService(self.settings.database).get(result['resource_id'])['body'],payload.body)
  self.assertEqual(self.s.reviews(self.path['id'])['items'][0]['note_id'],result['resource_id'])
  backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
  self.assertEqual(audit_database(self.root/'restored/db/hub.db'),audit_database(self.settings.database));self.assertEqual(LearningService(self.root/'restored/db/hub.db').tree(self.path['id']),self.s.tree(self.path['id']))
 def test_private_evidence_and_parent_hidden_no_assessment_counts_export_or_ids(self):
  note=self.note();self.s.assess(self.node['id'],Assess(expected_version=1,mastery='verified',conclusion='含有私密化证据的结论',evidence=[note['id']]),key())
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(note['id'],))
  self.assertEqual(self.s.context(self.node['id'])['assessments'],[]);self.assertEqual(self.s.tree(self.path['id'])['progress']['verified'],0);self.assertEqual(self.s.overview()['verified_assessments_this_week'],0)
  exported=zipfile.ZipFile(BytesIO(portable_export(self.settings.database))).read('data.json').decode();self.assertNotIn('含有私密化证据的结论',exported);self.assertNotIn(note['id'],exported)
  self.assertEqual(json.loads(exported)['tables']['knowledge_nodes'][0]['mastery'],'unavailable')
  SearchService(self.settings.database).rebuild();self.assertEqual(SearchService(self.settings.database).query('verified')['total'],0)
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(self.path['id'],))
  self.assertEqual(self.s.list('knowledge_node')['total'],0)
  with self.assertRaises(HubError):self.s.context(self.node['id'])
 def test_soft_delete_blocks_active_children_restores_and_preserves_independent_output(self):
  with self.assertRaises(HubError):self.s.trash('learning_path',self.path['id'],1)
  note=self.note();n=self.s.trash('knowledge_node',self.node['id'],1);self.assertTrue(n['deleted_at']);self.assertEqual(self.s.tree(self.path['id'])['progress']['total'],0)
  self.s.trash('knowledge_node',self.node['id'],2,True);self.assertEqual(self.s.tree(self.path['id'])['progress']['total'],1);NoteService(self.settings.database).get(note['id'])
 def test_session_timezone_duration_future_validation(self):
  now=datetime.now(timezone.utc)
  for start,end in [(now,now),(now+timedelta(hours=1),now+timedelta(hours=2)),(now-timedelta(days=2),now),(now.replace(tzinfo=None)-timedelta(hours=1),now.replace(tzinfo=None))]:
   with self.assertRaises(ValidationError):Session(node_id=self.node['id'],started_at=start,ended_at=end)
 def test_old_five_widget_configuration_projects_disabled_new_widgets_without_write(self):
  prefs=default_preferences();old=prefs['widgets'][:5]
  with connection(self.settings.database,write=True) as c:
   c.exec_driver_sql("INSERT INTO dashboard_preferences VALUES('dashboard',1,?,?,1,?,?)",(json.dumps(old),json.dumps(prefs['shortcuts']),'2026-11-12','2026-11-12'))
  before=audit_database(self.settings.database);result=DashboardService(self.settings.database).get()
  self.assertEqual(result['widgets'][:5],old);self.assertEqual([w['id'] for w in result['widgets'][5:]],['reading','learning','weekly','projects','meals','intelligence']);self.assertTrue(all(not w['enabled'] for w in result['widgets'][5:]));self.assertEqual(audit_database(self.settings.database),before)
 def test_existing_task_practice_has_one_authority_and_unlink_updates_learning_view(self):
  from backend.app.modules.knowledge.service import KnowledgeService
  from backend.app.modules.knowledge.router import Relation
  from backend.app.modules.life.tasks.schemas import TaskCreate
  task=TaskService(self.settings.database).create(TaskCreate(title='已存在练习'),key())[0]
  k=KnowledgeService(self.settings.database);context=k.relate(self.node['id'],Relation(target_id=task['id'],relation_type='practice',expected_version=1),key())
  self.assertEqual(self.s.context(self.node['id'])['tasks'][0]['id'],task['id'])
  with connection(self.settings.database) as c:
   self.assertEqual(c.exec_driver_sql("SELECT count(*) FROM resource_relations WHERE relation_type='practice'").scalar_one(),0)
  k.unlink(self.node['id'],context['relations'][0]['id'],context['resource']['version'])
  self.assertEqual(self.s.context(self.node['id'])['tasks'],[])
 def test_hidden_learning_ancestry_filtered_from_catalog_search_tags_collections_and_export(self):
  from backend.app.modules.knowledge.service import KnowledgeService
  from backend.app.modules.knowledge.router import CollectionWrite
  from backend.app.modules.tags.service import TagService
  from backend.app.modules.tags.schemas import TagAssignment
  k=KnowledgeService(self.settings.database);tags=TagService(self.settings.database)
  tags.assign(self.node['id'],TagAssignment(names=['不应泄漏学习标签'],expected_version=1,expected_tag_versions={}),key())
  collection=k.save_collection(CollectionWrite(title='集合',members=[self.node['id']]),key());SearchService(self.settings.database).rebuild()
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(self.path['id'],))
  self.assertEqual(k.catalog(q='PE 导入表')['items'],[]);self.assertEqual(SearchService(self.settings.database).query('PE 导入表')['total'],0);self.assertEqual(tags.list()['total'],0)
  self.assertEqual(k.collection(collection['id'])['members'],[])
  k.save_collection(CollectionWrite(title='集合改名',expected_version=1,members=[]),key(),collection['id'])
  with connection(self.settings.database) as c:self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM collection_members WHERE collection_id=?',(collection['id'],)).scalar_one(),1)
  exported=zipfile.ZipFile(BytesIO(portable_export(self.settings.database))).read('data.json').decode();self.assertNotIn(self.node['id'],exported);self.assertNotIn(self.path['id'],exported);self.assertNotIn('不应泄漏学习标签',exported)

 def test_reparenting_subtree_cannot_exceed_total_tree_depth_budget(self):
  from backend.app.modules.knowledge.service import create_resource
  child=self.s.save('knowledge_node',Node(title='下级练习',path_id=self.path['id'],parent_id=self.node['id']),key())
  parent=None
  with connection(self.settings.database,write=True) as c:
   for depth in range(64):
    rid=create_resource(c,'knowledge_node','深度测试 '+str(depth))
    c.exec_driver_sql('INSERT INTO knowledge_nodes(resource_id,path_id,parent_id) VALUES(?,?,?)',(rid,self.path['id'],parent))
    parent=rid
  with self.assertRaises(HubError) as error:
   self.s.save('knowledge_node',Node(title='PE 导入表',path_id=self.path['id'],parent_id=parent,expected_version=1),key(),self.node['id'])
  self.assertEqual(error.exception.code,'LEARNING_TREE_TOO_DEEP')
  self.assertIsNone(self.s.get('knowledge_node',self.node['id'])['parent_id'])
