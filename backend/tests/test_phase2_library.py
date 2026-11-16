import asyncio,hashlib,json,os,tempfile,unittest,zipfile
from io import BytesIO
from pathlib import Path
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch,Mock
import httpx
from pydantic import ValidationError
from backend.tests.async_support import AsyncAPITestCase
from backend.tests.pdf_fixtures import pdf
from backend.manage import initialize_empty
from backend.maintenance import upgrade,backup_hub,restore_hub
from backend.app.core.config import Settings
from backend.app.core.database import connection,HubError
from backend.app.core.note_snapshot import SnapshotFiles,verified_snapshot
from backend.app.main import create_app
from backend.app.modules.library.documents.service import DocumentService
from backend.app.modules.library.documents.storage import BlobFiles,MAX_PDF
from backend.app.modules.library.documents.worker import ExtractionWorker
from backend.app.modules.library.documents.reader import ReaderService,Profile,Preview
from backend.app.modules.library.documents.router import BookmarkWrite,DocumentWrite
from backend.app.modules.library.documents.extractor import extract,run
from backend.app.modules.library.catalog import CatalogService,Book,WebBookmark,Course
from backend.app.modules.knowledge.search import SearchService
from backend.app.modules.knowledge.export import portable_export
from scripts.data_safety import audit_database

def key():return str(uuid4())
class LibraryFacts(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
  initialize_empty(self.settings.data_root);upgrade(self.settings);self.d=DocumentService(self.settings.database);self.worker=ExtractionWorker(self.settings);self.c=CatalogService(self.settings.database)
 def tearDown(self):self.tmp.cleanup()
 def add(self,data=None,name='sample.pdf',operation=None):
  data=pdf() if data is None else data;iid=key();relative,fd=self.d.begin(iid)
  with os.fdopen(fd,'wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
  return self.d.commit(iid,relative,len(data),hashlib.sha256(data).hexdigest(),name,operation or key())
 def test_dedupe_same_key_across_names_concurrency_original_unchanged(self):
  data=pdf();operation=key();first=self.add(data,operation=operation)
  again=self.add(data,operation=operation);self.assertTrue(again['duplicate']);self.assertEqual(first['document']['id'],again['document']['id'])
  with ThreadPoolExecutor(max_workers=2) as pool:
   # file_lock fails immediately under contention, so caller must retry explicitly.
   def attempt(_):
    try:return self.add(data)
    except HubError as error:
     if error.code!='NOTE_FILES_BUSY':raise
     return None
   results=list(pool.map(attempt,range(2)))
  results=[r if r is not None else self.add(data) for r in results]
  self.assertTrue(all(r['duplicate'] for r in results));self.assertEqual(self.d.original(first['document']['id'])[0],data)
  with connection(self.settings.database) as c:
   self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM blobs').scalar_one(),1);self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM documents').scalar_one(),1)
  self.d.trash(first['document']['id'],1)
  with self.assertRaises(HubError) as error:self.add(data)
  self.assertEqual(error.exception.code,'PDF_DUPLICATE_IN_TRASH')
 def test_real_poppler_normal_blank_corrupt_encrypted_multilingual_states(self):
  expected=[(pdf(), 'ready'),(pdf(blank=True),'no_text'),(b'%PDF-1.4\nnot a pdf','failed'),(pdf(encrypted=True),'password_required'),(pdf(chinese=True),'ready')]
  ids=[]
  for data,status in expected:
   d=self.add(data)['document'];ids.append(d['id']);self.assertTrue(self.worker.once());current=self.d.get(d['id']);self.assertEqual(current['extraction_status'],status)
   if status in ('ready','no_text'):self.assertEqual(current['page_count'],1)
  self.assertEqual(SearchService(self.settings.database).query('恶意代码')['items'][0]['id'],ids[-1])
 def test_reading_valid_pages_bookmarks_conflicts_and_retry_cancel_restart(self):
  d=self.add()['document'];self.worker.once();d=self.d.get(d['id'])
  with self.assertRaises(HubError):self.d.bookmark(d['id'],BookmarkWrite(expected_version=d['version'],page=2))
  d=self.d.bookmark(d['id'],BookmarkWrite(expected_version=d['version'],page=1,label='PE import'))
  self.assertEqual(d['bookmarks'][0]['page'],1)
  d=self.d.patch(d['id'],DocumentWrite(title='人工标题',authors='人工作者',expected_version=d['version'],read_state='reading',current_page=1))
  self.assertEqual(d['title'],'人工标题');self.assertEqual(d['current_page'],1)
  with self.assertRaises(HubError):self.d.patch(d['id'],DocumentWrite(title='越界页',expected_version=d['version'],current_page=2))
  d=self.d.job_action(d['id'],d['version']);self.d.job_action(d['id'],d['version'],True);self.worker.once();self.assertEqual(self.d.get(d['id'])['extraction_status'],'cancelled')
  d=self.d.get(d['id']);self.d.job_action(d['id'],d['version'])
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE extraction_jobs SET state='running' WHERE state='queued'")
  self.worker.recover();self.worker.once();self.assertEqual(self.d.get(d['id'])['extraction_status'],'ready')
 def test_extraction_cancel_timeout_budget_does_not_publish_late_result(self):
  d=self.add()['document']
  with patch('backend.app.modules.library.documents.worker.extract',side_effect=HubError('PDF_TIMEOUT')):self.worker.once()
  self.assertEqual(self.d.get(d['id'])['extraction_status'],'failed')
  d=self.d.get(d['id']);self.d.job_action(d['id'],d['version'])
  def cancelled_extract(*args):
   with connection(self.settings.database,write=True) as c:c.exec_driver_sql('UPDATE extraction_jobs SET cancel_requested=1 WHERE document_id=?',(d['id'],))
   return {'page_count':1,'title':'late','author':'','subject':'','created':'','text':b'late result'}
  with patch('backend.app.modules.library.documents.worker.extract',side_effect=cancelled_extract):self.worker.once()
  self.assertEqual(self.d.get(d['id'])['extraction_status'],'cancelled')
  with connection(self.settings.database) as c:self.assertIsNone(c.exec_driver_sql('SELECT extracted_text_path FROM documents WHERE resource_id=?',(d['id'],)).scalar_one())
  source=self.root/'file.pdf';source.write_bytes(pdf())
  with patch('backend.app.modules.library.documents.extractor.MAX_PAGES',0):
   with self.assertRaises(HubError) as error:extract(source)
  self.assertEqual(error.exception.code,'PDF_PAGE_BUDGET')
 def test_blob_traversal_symlink_permissions_corruption_and_snapshot_all_files(self):
  d=self.add()['document'];self.worker.once();files=SnapshotFiles(self.settings.data_root).inventory();self.assertTrue(any(p.startswith('library/') for p in files))
  backup_hub(self.settings,self.root/'backup');restore_hub(self.settings,self.root/'backup',self.root/'restored')
  self.assertEqual(SnapshotFiles(self.root/'restored').inventory(),files);self.assertEqual(audit_database(self.root/'restored/db/hub.db'),audit_database(self.settings.database))
  with self.assertRaises(HubError):self.d.files.read('library/blobs/../x')
  link=self.settings.data_root/'library/staging/link.pdf';link.symlink_to(self.root/'outside')
  with self.assertRaises(HubError):self.d.files.read('library/staging/link.pdf')
  link.unlink() # Only this fixture's intentional symlink, no user's file.
  blob=next(p for p in files if p.startswith('library/blobs/'));path=self.settings.data_root/blob;path.write_bytes(b'%PDF-changed')
  with self.assertRaises(HubError):self.d.original(d['id'])
  with self.assertRaises(HubError):backup_hub(self.settings,self.root/'bad-backup')
 def test_catalog_no_file_validation_cas_idempotency_and_no_fetch(self):
  book=self.c.save('book',Book(title='Windows Internals',total_pages=100,current_page=12),key());self.assertEqual(book['progress_percent'],12)
  web=self.c.save('bookmark',WebBookmark(title='技术文章',url='https://example.invalid/?q=pe',summary='人工摘要'),key());self.assertEqual(web['summary'],'人工摘要')
  course=self.c.save('course',Course(title='课程',total_units=10,completed_units=3),key());self.assertEqual(course['completed_units'],3)
  with self.assertRaises(ValidationError):Book(title='越界',total_pages=10,current_page=11)
  with self.assertRaises(ValidationError):WebBookmark(title='注入',url='javascript:alert(1)')
  with self.assertRaises(HubError):self.c.save('book',Book(title='过期',expected_version=9),key(),book['id'])
  trashed=self.c.trash('book',book['id'],book['version']);self.c.trash('book',book['id'],trashed['version'],True)
 def test_private_metadata_dedupe_search_export_and_reader_deny(self):
  d=self.add()['document'];self.worker.once();SearchService(self.settings.database).rebuild()
  with connection(self.settings.database,write=True) as c:c.exec_driver_sql("UPDATE resources SET privacy_level='private',title='NEVER_EXPORT_PRIVATE' WHERE id=?",(d['id'],))
  with self.assertRaises(HubError):self.d.get(d['id'])
  with self.assertRaises(HubError):self.d.original(d['id'])
  with self.assertRaises(HubError) as error:self.add()
  self.assertEqual(error.exception.code,'PDF_IMPORT_UNAVAILABLE');self.assertEqual(SearchService(self.settings.database).query('Personal Hub')['total'],0)
  archive=zipfile.ZipFile(BytesIO(portable_export(self.settings.database)));self.assertNotIn(b'NEVER_EXPORT_PRIVATE',archive.read('data.json'));self.assertFalse(any(p.startswith('library/blobs') for p in archive.namelist()))
 def test_reader_integrity_check_never_runs_inside_the_write_transaction(self):
  d=self.add()['document'];reader=ReaderService(self.settings.database)
  seen=[]
  original=reader.files.read
  def spy(*args,**kwargs):
   # A second writer must be able to start while the original is being hashed.
   try:
    with connection(self.settings.database,write=True) as other:other.exec_driver_sql('SELECT 1')
    seen.append('writable')
   except Exception as error:seen.append(type(error).__name__)
   return original(*args,**kwargs)
  reader.files.read=spy
  reader.verify(d['id'])
  self.assertEqual(seen,['writable'],'verification must not hold the SQLite write lock')
  # A tampered original is refused before any preview row is written.
  with connection(self.settings.database) as c:
   relative=c.exec_driver_sql('SELECT b.relative_path FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(d['id'],)).scalar_one()
  reader.files.read=original
  target=self.settings.data_root/relative
  target.chmod(0o600);target.write_bytes(target.read_bytes()+b'tampered')
  with self.assertRaises(HubError):reader.verify(d['id'])
 def test_reader_whitelist_preview_cas_confirmation_one_launch_without_shell(self):
  if not Path('/usr/bin/evince').is_file():self.skipTest('Installed reader unavailable')
  d=self.add()['document'];reader=ReaderService(self.settings.database)
  with self.assertRaises(ValidationError):Profile(name='inject',executable='/bin/sh')
  p=reader.profile(Profile(name='Evince',executable='/usr/bin/evince'));preview=reader.preview(Preview(document_id=d['id'],profile_id=p['id']))
  self.assertEqual(preview['arguments'][:2],['/usr/bin/evince','--'])
  process=Mock();process.wait.return_value=0
  with patch('backend.app.modules.library.documents.reader.subprocess.Popen',return_value=process) as spawn:
   result=reader.confirm(preview['id']);again=reader.confirm(preview['id']);self.assertEqual(result['id'],again['id']);self.assertEqual(spawn.call_count,1);self.assertFalse(spawn.call_args.kwargs['shell'])
  other=reader.preview(Preview(document_id=d['id'],profile_id=p['id']));reader.profile(Profile(name='Changed',executable='/usr/bin/evince',expected_version=1),p['id'])
  with self.assertRaises(HubError):reader.confirm(other['id'])

class LibraryAPI(AsyncAPITestCase):
 async def asyncSetUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.settings=Settings(data_root=self.root/'hub',legacy_database=self.root/'legacy',reminders_enabled=False)
  initialize_empty(self.settings.data_root);upgrade(self.settings);self.app=create_app(self.settings);self.client=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://127.0.0.1:8765')
  r=await self.client.post('/api/v1/session/exchange',json={'token':self.app.state.auth.bootstrap},headers={'Origin':'http://127.0.0.1:8765'})
  self.headers={'Origin':'http://127.0.0.1:8765','X-CSRF-Token':r.json()['csrf_token'],'Idempotency-Key':key()}
 async def asyncTearDown(self):await self.client.aclose();self.tmp.cleanup()
 async def test_real_stream_validation_idempotency_and_incremental_limit(self):
  r=await self.client.post('/api/v1/documents/import?name=test.pdf',content=pdf(),headers=self.headers);self.assertEqual(r.status_code,200,r.text)
  repeated=await self.client.post('/api/v1/documents/import?name=test.pdf',content=pdf(),headers=self.headers);self.assertEqual(repeated.json()['document']['id'],r.json()['document']['id'])
  invalid=await self.client.post('/api/v1/documents/import?name=x.pdf',content=b'not pdf',headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(invalid.status_code,422)
  with patch('backend.app.modules.library.documents.service.MAX_PDF',50):
   async def chunks():yield b'%PDF-'+b'x'*20;yield b'x'*30
   too_large=await self.client.post('/api/v1/documents/import?name=x.pdf',content=chunks(),headers={**self.headers,'Idempotency-Key':key()});self.assertEqual(too_large.status_code,413)
  no_csrf=await self.client.post('/api/v1/documents/import?name=x.pdf',content=pdf(),headers={'Origin':'http://127.0.0.1:8765'});self.assertEqual(no_csrf.status_code,403)
  self.client.cookies.clear();self.assertEqual((await self.client.get('/api/v1/documents')).status_code,401)
 async def test_stream_disconnect_retains_interrupted_intent_and_no_partial_document(self):
  async def broken():yield b'%PDF-1.4\n';raise RuntimeError('simulated disconnect')
  with self.assertRaises(Exception):await self.client.post('/api/v1/documents/import?name=broken.pdf',content=broken(),headers=self.headers)
  with connection(self.settings.database) as c:
   self.assertEqual(c.exec_driver_sql('SELECT count(*) FROM documents').scalar_one(),0)
   self.assertEqual(c.exec_driver_sql('SELECT status FROM document_imports').scalar_one(),'interrupted')
 async def test_metadata_retry_uses_current_facts_and_same_request_key(self):
  r=(await self.client.post('/api/v1/documents/import?name=test.pdf',content=pdf(),headers=self.headers)).json()['document']
  data={'title':'修改元数据','expected_version':r['version'],'authors':'Author'};headers={**self.headers,'Idempotency-Key':key()}
  first=await self.client.put('/api/v1/documents/'+r['id'],json=data,headers=headers);again=await self.client.put('/api/v1/documents/'+r['id'],json=data,headers=headers)
  self.assertEqual(first.status_code,200,first.text);self.assertEqual(again.json()['version'],first.json()['version'])

 async def test_lifespan_runs_single_workers_and_core_survives_extractor_failure(self):
  async with self.app.router.lifespan_context(self.app):
   await asyncio.sleep(.12)
   self.assertIn(self.app.state.search_worker.state,('running','starting'))
   first=await self.client.post('/api/v1/catalog/book',json={'title':'离线无 AI 书籍'},headers=self.headers)
   self.assertEqual(first.status_code,200,first.text)
   from backend.app.modules.library.documents.worker import ExtractionWorker
   standby=ExtractionWorker(self.settings);await standby.start();self.assertEqual(standby.state,'standby');await standby.stop()
  self.assertIsNone(self.app.state.search_worker.fd);self.assertIsNone(self.app.state.extraction_worker.fd)
