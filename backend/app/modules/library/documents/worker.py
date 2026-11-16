"""Single local persistent extraction queue; restart retries interrupted running jobs."""
import asyncio,fcntl,os,threading,time
from pathlib import Path
from uuid import uuid4
from ....core.database import connection,HubError,utc_now
from ....core.note_files import file_lock
from ...knowledge.service import rows,visible,bump
from ...knowledge.search import SearchService
from .storage import BlobFiles
from .extractor import extract
from .epub import extract as extract_epub

class ExtractionWorker:
 def __init__(self,settings):self.settings=settings;self.task=None;self.fd=None;self.state='disabled';self.stop_event=threading.Event()
 async def start(self):
  self.stop_event.clear()
  try:
   self.fd=os.open(self.settings.data_root/'config'/'pdf-worker.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
   if os.fstat(self.fd).st_mode&0o077:raise OSError('private lock required')
   fcntl.flock(self.fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
  except BlockingIOError:
   os.close(self.fd);self.fd=None;self.state='standby';return
  except OSError:
   if self.fd is not None:os.close(self.fd);self.fd=None
   self.state='degraded';return
  self.task=asyncio.create_task(self.loop())
 async def stop(self):
  self.stop_event.set()
  if self.task:
   await self.task;self.task=None
  if self.fd is not None:os.close(self.fd);self.fd=None
 def recover(self):
  with connection(self.settings.database,write=True) as c:
   # A job that was already running 3 times and never finished is what is hanging the worker: fail it, do not re-queue it again.
   c.exec_driver_sql("UPDATE extraction_jobs SET state='failed',error_code='EXTRACTION_REPEATED_RESTART',updated_at=? WHERE state='running' AND attempts>=3",(utc_now(),))
   c.exec_driver_sql("UPDATE documents SET extraction_status='failed' WHERE resource_id IN(SELECT document_id FROM extraction_jobs WHERE state='failed' AND error_code='EXTRACTION_REPEATED_RESTART') AND extraction_status IN('running','queued')")
   c.exec_driver_sql("UPDATE extraction_jobs SET state='queued',error_code='PDF_RESTARTED',updated_at=? WHERE state='running'",(utc_now(),))
   c.exec_driver_sql("UPDATE documents SET extraction_status='queued' WHERE resource_id IN(SELECT document_id FROM extraction_jobs WHERE state='queued')")
   c.exec_driver_sql("UPDATE document_imports SET status='interrupted',error_code='PDF_UPLOAD_INTERRUPTED',updated_at=? WHERE status IN('receiving','prepared')",(utc_now(),))
 def cancelled(self,jid,rid):
  if self.stop_event.is_set():return True
  with connection(self.settings.database) as c:
   row=c.exec_driver_sql('SELECT cancel_requested FROM extraction_jobs WHERE id=?',(jid,)).first()
   r=c.exec_driver_sql("SELECT 1 FROM resources WHERE id=? AND deleted_at IS NULL AND privacy_level='personal'",(rid,)).first()
   return not row or bool(row[0]) or not r
 def once(self):
  local={}
  with connection(self.settings.database,write=True) as c:
   job=c.exec_driver_sql("SELECT id,document_id,cancel_requested FROM extraction_jobs WHERE state='queued' ORDER BY created_at,id LIMIT 1").first()
   if not job:return False
   jid,rid,cancel=job
   if cancel:
    c.exec_driver_sql("UPDATE extraction_jobs SET state='cancelled',updated_at=? WHERE id=?",(utc_now(),jid));c.exec_driver_sql("UPDATE documents SET extraction_status='cancelled' WHERE resource_id=?",(rid,));bump(c,rid);return True
   c.exec_driver_sql("UPDATE extraction_jobs SET state='running',attempts=attempts+1,updated_at=? WHERE id=?",(utc_now(),jid))
   c.exec_driver_sql("UPDATE documents SET extraction_status='running' WHERE resource_id=?",(rid,))
   source=c.exec_driver_sql('SELECT b.relative_path,b.sha256,b.media_type FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,)).first()
   local['epub']=source[2]=='application/epub+zip'
  result=None;state='failed';code=None;path=None;digest=None;store=BlobFiles(self.settings.data_root)
  try:
   if self.cancelled(jid,rid):raise HubError('PDF_CANCELLED')
   # Copy verified bytes to a private transient path, so parsing never follows an arbitrary original path.
   data=store.read(source[0],source[1])
   epub=local['epub']
   import tempfile
   with tempfile.TemporaryDirectory(prefix='hub-doc-') as temp:
    original=Path(temp)/('input.epub' if epub else 'input.pdf')
    fd=os.open(original,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(data)
    del data
    if epub:
     # EPUB parsing is in-process but bounded by the parser's own size limits.
     if self.cancelled(jid,rid):raise HubError('PDF_CANCELLED')
     result=extract_epub(original)
    else:
     result=extract(original,lambda:self.cancelled(jid,rid))
   state='ready' if result['text'].strip() else 'no_text'
   if result['text']:
    path='library/text/'+rid+'/'+str(uuid4())+'.txt'
    # A note save or snapshot may hold the lock for an instant; wait briefly instead of failing a parsed file.
    for attempt in range(20):
     try:
      with file_lock(self.settings.data_root):digest=store.write_text(path,result['text'])
      break
     except HubError as busy:
      if busy.code!='NOTE_FILES_BUSY' or attempt==19:raise
      time.sleep(.25)
  except HubError as error:
   code=error.code;state='cancelled' if code in ('PDF_CANCELLED','EPUB_CANCELLED') else 'password_required' if code in ('PDF_PASSWORD_REQUIRED','EPUB_ENCRYPTED') else 'failed'
  except OSError:code='PDF_IO_ERROR'
  except Exception:
   # Anything unexpected must still end the job; otherwise it stays 'running' and is re-queued on every restart.
   code='EXTRACTION_FAILED';result=None
  with connection(self.settings.database,write=True) as c:
   # A late cancel never publishes text or metadata as success.
   cancelled=c.exec_driver_sql('SELECT cancel_requested FROM extraction_jobs WHERE id=?',(jid,)).scalar_one()
   r=c.exec_driver_sql("SELECT 1 FROM resources WHERE id=? AND privacy_level='personal' AND deleted_at IS NULL",(rid,)).first()
   if cancelled or not r:state='cancelled';code='PDF_CANCELLED'
   c.exec_driver_sql('UPDATE extraction_jobs SET state=?,error_code=?,page_count=?,updated_at=? WHERE id=?',(state,code,result['page_count'] if result else None,utc_now(),jid))
   c.exec_driver_sql('UPDATE documents SET extraction_status=? WHERE resource_id=?',(state,rid))
   if result and state in ('ready','no_text'):
    c.exec_driver_sql('UPDATE documents SET page_count=?,extracted_text_path=?,text_sha256=?,extractor=?,metadata_title=?,metadata_author=?,metadata_subject=?,metadata_created=? WHERE resource_id=?',
      (result['page_count'],path,digest,'epub-builtin-v1' if epub else 'poppler-system-v1',result['title'],result['author'],result['subject'],result['created'],rid))
   bump(c,rid)
  SearchService(self.settings.database).refresh()
  return True
 async def loop(self):
  recovered=False
  while not self.stop_event.is_set():
   try:
    if not recovered:await asyncio.to_thread(self.recover);recovered=True
    self.state='running';worked=await asyncio.to_thread(self.once)
   except Exception:self.state='degraded';worked=False
   if not worked:
    for _ in range(20):
     if self.stop_event.is_set():break
     await asyncio.sleep(.1)
