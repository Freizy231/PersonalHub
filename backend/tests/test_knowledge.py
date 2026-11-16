from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4
import unittest
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from backend.app.core.config import Settings
from backend.app.core.database import HubError,connection
from backend.app.modules.knowledge.service import KnowledgeService
from backend.app.modules.knowledge.router import CollectionWrite,Relation
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.library.notes.service import NoteService
from backend.app.modules.library.notes.schemas import NoteCreate
from backend.app.modules.life.tasks.service import TaskService
from backend.app.modules.life.tasks.schemas import TaskCreate
from backend.app.modules.tags.service import TagService
from backend.app.modules.tags.schemas import TagAssignment

def key():return str(uuid4())
class KnowledgeFacts(unittest.TestCase):
 def setUp(self):
  self.temp=TemporaryDirectory();self.root=Path(self.temp.name);self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy')
  initialize_empty(self.settings.data_root);upgrade(self.settings)
  self.k=KnowledgeService(self.settings.database);self.s=SearchService(self.settings.database);self.n=NoteService(self.settings.database)
  self.task=TaskService(self.settings.database).create(TaskCreate(title='练习PE',description='导入表分析'),key())[0]
 def tearDown(self):self.temp.cleanup()
 def test_collections_are_pageable_and_report_their_total(self):
  for index in range(5):self.k.save_collection(CollectionWrite(title=f'集合{index}'),key())
  first=self.k.collections(limit=2,offset=0);second=self.k.collections(limit=2,offset=2);last=self.k.collections(limit=2,offset=4)
  self.assertEqual((first['total'],len(first['items']),len(second['items']),len(last['items'])),(5,2,2,1))
  ids=[row['id'] for page in (first,second,last) for row in page['items']]
  self.assertEqual(len(set(ids)),5,'paging neither repeats nor skips a collection')
  self.assertEqual(len(self.k.collections()['items']),5,'the default call still returns everything up to 100')
 def test_shared_task_tags_and_export(self):
  t=TagService(self.settings.database);n=self.n.create(NoteCreate(title='普通笔记'),key())[0]
  for rid in [self.task['id'],n['id']]:t.assign(rid,TagAssignment(names=['逆向'],expected_version=1,expected_tag_versions={}),key())
  self.assertEqual(t.list()['items'][0]['usage_count'],2)
  tid=t.list()['items'][0]['id'];tasks=TaskService(self.settings.database)
  self.assertEqual(tasks.list(tag_id=tid)['total'],1);self.assertEqual(tasks.export()['tasks'][0]['tags'][0]['id'],tid)
  from backend.app.modules.life.events.service import EventService
  from backend.app.modules.life.events.schemas import EventCreate
  from backend.app.modules.knowledge.export import portable_export
  from io import BytesIO
  import json,zipfile
  event=EventService(self.settings.database).create(EventCreate(title='公开日程',is_all_day=True,start_date='2026-11-12',end_date='2026-11-13',task_id=self.task['id']),key())[0]
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(self.task['id'],))
  exported=zipfile.ZipFile(BytesIO(portable_export(self.settings.database))).read('data.json').decode()
  self.assertNotIn(self.task['id'],exported);self.assertIsNone(json.loads(exported)['tables']['events'][0]['task_id'])
 def test_collection_cas_order_trash_does_not_delete_members(self):
  n=self.n.create(NoteCreate(title='资料'),key())[0]
  c=self.k.save_collection(CollectionWrite(title='逆向合集',members=[self.task['id'],n['id']]),key())
  self.assertEqual([r['id'] for r in c['members']],[self.task['id'],n['id']])
  self.k.save_collection(CollectionWrite(title='修改',expected_version=1,members=[n['id'],self.task['id']]),key(),c['id'])
  with self.assertRaises(HubError):self.k.save_collection(CollectionWrite(title='旧版本',expected_version=1),key(),c['id'])
  self.k.trash_collection(c['id'],2);self.assertEqual(self.k.collections()['items'],[]);self.k.get(n['id'])
  self.k.trash_collection(c['id'],3,True);self.assertEqual(len(self.k.collection(c['id'])['members']),2)
 def test_fts_chinese_short_literals_hash_privacy_and_backlinks(self):
  n=self.n.create(NoteCreate(title='PE知识',body='恶意代码分析 导入表\n'+ 'hub://resource/'+self.task['id']),key())[0]
  self.s.rebuild();self.assertEqual(self.s.query('恶意代码')['total'],1);self.assertEqual(self.s.query('导入')['total'],2)
  self.assertEqual(self.s.query('" OR *')['total'],0);self.assertEqual(self.k.context(self.task['id'])['backlinks'][0]['id'],n['id'])
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private' WHERE id=?",(n['id'],))
  self.assertEqual(self.s.query('恶意代码')['total'],0);self.assertEqual(self.k.context(self.task['id'])['backlinks'],[])
 def test_relations_privacy_conflict_and_snapshot(self):
  n=self.n.create(NoteCreate(title='实验记录'),key())[0]
  result=self.k.relate(self.task['id'],Relation(target_id=n['id'],expected_version=1,relation_type='output'),key())
  self.assertEqual(len(result['relations']),1)
  with self.assertRaises(HubError):self.k.relate(self.task['id'],Relation(target_id=n['id'],expected_version=1),key())
  backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
  self.assertEqual(KnowledgeService(self.root/'restored/db/hub.db').context(self.task['id']),self.k.context(self.task['id']))
 def test_incremental_search_refresh_and_external_file_change_revoke_cached_hits(self):
  n=self.n.create(NoteCreate(title='增量索引',body='罕见全文测试词'),key())[0]
  self.assertEqual(self.s.query('罕见全文')['total'],0)
  self.s.refresh();self.assertEqual(self.s.query('罕见全文')['total'],1)
  # Warm the integrity cache, then change equal-length content on disk; ctime invalidates it.
  with connection(self.settings.database) as c:path=c.exec_driver_sql('SELECT body_path FROM notes WHERE resource_id=?',(n['id'],)).scalar_one()
  from os import utime
  file=self.settings.data_root/path;before=file.stat();file.write_text('外部修改测试词');utime(file,ns=(before.st_atime_ns,before.st_mtime_ns))
  self.assertEqual(self.s.query('罕见全文')['total'],0);self.assertEqual(self.s.rebuild()['skipped'],1)
