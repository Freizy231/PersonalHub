import asyncio,hashlib,os
from pathlib import Path
from uuid import uuid4
from ....core.database import connection,HubError,utc_now
from ....core.note_files import file_lock
from ....core.operations import activity,record,replay
from ...knowledge.service import visible,versioned,bump,create_resource,rows
from .storage import BlobFiles,MAX_PDF,MAX_EPUB

FORMATS={'pdf':('application/pdf','%PDF-'),'epub':('application/epub+zip','PK\x03\x04')}


def detect_format(prefix,name):
 """A format is decided by file magic, never by the client-supplied name."""
 if prefix.startswith(b'%PDF-'):return 'pdf'
 if prefix.startswith(b'PK\x03\x04'):
  if name.lower().endswith('.epub'):return 'epub'
  return 'epub'
 return None


class DocumentService:
 def __init__(self,database):self.database=database;self.root=Path(database).parent.parent;self.files=BlobFiles(self.root)
 def _get(self,con,rid,trash=False):
  r=visible(con,rid,trash=trash)
  if r['kind']!='document':raise HubError('DOCUMENT_NOT_FOUND',404)
  r.update(rows(con,'SELECT d.*,b.sha256,b.size,b.media_type FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,))[0]);r.pop('resource_id',None)
  r['format']='epub' if r.pop('media_type','application/pdf')=='application/epub+zip' else 'pdf'
  # Storage paths are internal, not client input.
  r.pop('extracted_text_path',None);r.pop('text_sha256',None)
  r['bookmarks']=rows(con,'SELECT id,page,label,created_at FROM document_bookmarks WHERE document_id=? ORDER BY page',(rid,))
  from ...tags.service import tag_map
  r['tags']=tag_map(con,[rid])[rid]
  return r
 def get(self,rid):
  with connection(self.database) as con:return self._get(con,rid)
 def list(self,trash=False,state='',q='',limit=30,offset=0,tag_id=None,category=''):
  with connection(self.database) as con:
   clauses="r.privacy_level='personal' AND r.kind='document' AND r.deleted_at IS "+('NOT NULL' if trash else 'NULL')+" AND (?='' OR d.read_state=?) AND instr(lower(r.title),lower(?))>0"
   clauses+=" AND (?='' OR d.category=?) AND (? IS NULL OR EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=r.id AND rt.tag_id=?))"
   args=(state,state,q,category,category,tag_id,tag_id);total=con.exec_driver_sql('SELECT count(*) FROM resources r JOIN documents d ON d.resource_id=r.id WHERE '+clauses,args).scalar_one()
   found=rows(con,'SELECT r.id FROM resources r JOIN documents d ON d.resource_id=r.id WHERE '+clauses+' ORDER BY r.updated_at DESC,r.id LIMIT ? OFFSET ?',args+(limit,offset))
   return {'items':[self._get(con,r['id'],trash) for r in found],'total':total,'limit':limit,'offset':offset}
 def begin(self,iid,extension='pdf'):
  now=utc_now();relative='library/staging/'+iid+'.'+extension
  with connection(self.database,write=True) as con:
   con.exec_driver_sql("INSERT INTO document_imports(id,staging_path,status,created_at,updated_at) VALUES(?,?,'receiving',?,?)",(iid,relative,now,now))
  return relative,self.files.create(relative)
 def fail(self,iid,code):
  with connection(self.database,write=True) as con:con.exec_driver_sql("UPDATE document_imports SET status='interrupted',error_code=?,updated_at=? WHERE id=? AND status<>'committed'",(code,utc_now(),iid))
 async def upload(self,request,name,key):
  if not name or len(name)>255 or any(ord(c)<32 for c in name) or '/' in name or '\\' in name:raise HubError('PDF_FILENAME_INVALID',422)
  length=request.headers.get('content-length')
  # Both formats share the same 100 MiB budget; the name only picks which constant applies.
  epub_name=name.lower().endswith('.epub')
  limit=MAX_EPUB if epub_name else MAX_PDF
  if length and (not length.isdecimal() or int(length)>limit):raise HubError('PDF_TOO_LARGE',413)
  # The extension is provisional; the magic bytes decide the real format.
  iid=str(uuid4());relative,fd=await asyncio.to_thread(self.begin,iid,'epub' if epub_name else 'pdf');size=0;digest=hashlib.sha256();prefix=b''
  try:
   async for chunk in request.stream():
    size+=len(chunk)
    if size>limit:raise HubError('PDF_TOO_LARGE',413)
    if len(prefix)<8:prefix=(prefix+chunk)[:8]
    digest.update(chunk)
    # write() can return a partial count.
    def write_all(data):
     view=memoryview(data)
     while view:view=view[os.write(fd,view):]
    await asyncio.to_thread(write_all,chunk)
   await asyncio.to_thread(os.fsync,fd)
   media=detect_format(prefix,name)
   if media is None:raise HubError('PDF_SIGNATURE_INVALID',422)
  except BaseException as error:
   os.close(fd)
   await asyncio.shield(asyncio.to_thread(self.fail,iid,error.code if isinstance(error,HubError) else 'PDF_UPLOAD_INTERRUPTED'))
   raise
  os.close(fd)
  try:
   result=await asyncio.to_thread(self.commit,iid,relative,size,digest.hexdigest(),name,key,media)
   try:await asyncio.to_thread(self.files.discard_committed_staging,relative,digest.hexdigest())
   except (HubError,OSError):pass # Committed original is safe; retain recovery staging on cleanup failure.
   return result
  except BaseException as error:
   await asyncio.shield(asyncio.to_thread(self.fail,iid,error.code if isinstance(error,HubError) else 'PDF_IMPORT_FAILED'));raise
 def commit(self,iid,staging,size,digest,name,key,media='pdf'):
  extension='epub' if media=='epub' else 'pdf'
  final='library/blobs/'+digest[:2]+'/'+digest+'.'+extension;data={'sha256':digest,'name':name,'size':size,'format':media}
  with file_lock(self.root):
   with connection(self.database,write=True) as con:
    prior=replay(con,'document.import',key,data)
    if prior:
     result=self._get(con,prior['resource_id'],True)
     con.exec_driver_sql("UPDATE document_imports SET status='committed',document_id=?,size=?,content_sha256=?,updated_at=? WHERE id=?",(result['id'],size,digest,utc_now(),iid))
     return {'document':result,'duplicate':True}
    existing=con.exec_driver_sql('SELECT d.resource_id FROM blobs b JOIN documents d ON d.blob_id=b.id WHERE b.sha256=?',(digest,)).first()
    if existing:
     # Hidden identity is never returned, even via the dedupe side channel.
     try:result=self._get(con,existing[0],True)
     except HubError:raise HubError('PDF_IMPORT_UNAVAILABLE',409) from None
     if result['deleted_at']:raise HubError('PDF_DUPLICATE_IN_TRASH',409)
     self.files.read(final,digest);record(con,'document.import',key,data,{'resource_id':result['id']})
     con.exec_driver_sql("UPDATE document_imports SET status='committed',document_id=?,size=?,content_sha256=?,updated_at=? WHERE id=?",(result['id'],size,digest,utc_now(),iid))
     return {'document':result,'duplicate':True}
    con.exec_driver_sql("UPDATE document_imports SET status='prepared',final_path=?,content_sha256=?,size=?,updated_at=? WHERE id=?",(final,digest,size,utc_now(),iid))
   self.files.publish(staging,final,digest)
   with connection(self.database,write=True) as con:
    stem=name[:-5] if name.lower().endswith('.epub') else name[:-4] if name.lower().endswith('.pdf') else name
    rid=create_resource(con,'document',stem);bid=str(uuid4());now=utc_now()
    con.exec_driver_sql('INSERT INTO blobs VALUES(?,?,?,?,?,?)',(bid,digest,size,final,FORMATS[media][0],now))
    con.exec_driver_sql('INSERT INTO documents(resource_id,blob_id,original_name) VALUES(?,?,?)',(rid,bid,name))
    con.exec_driver_sql("INSERT INTO extraction_jobs(id,document_id,state,created_at,updated_at) VALUES(?,?,'queued',?,?)",(str(uuid4()),rid,now,now))
    con.exec_driver_sql("UPDATE document_imports SET status='committed',document_id=?,updated_at=? WHERE id=?",(rid,now,iid))
    activity(con,rid,'imported',['blob']);record(con,'document.import',key,data,{'resource_id':rid});return {'document':self._get(con,rid),'duplicate':False}
 def patch(self,rid,payload,key=None):
  data=payload.model_dump(exclude={'expected_version'},mode='json')
  with connection(self.database,write=True) as con:
   current=self._get(con,rid)
   original=payload.model_dump(mode='json')
   if key and replay(con,'document.edit:'+rid,key,original) is not None:return current
   versioned(con,rid,payload.expected_version)
   if data['current_page']>0 and (current['page_count'] is None or data['current_page']>current['page_count']):raise HubError('PDF_PAGE_INVALID',422)
   title=data.pop('title');con.exec_driver_sql('UPDATE resources SET title=? WHERE id=?',(title,rid))
   con.exec_driver_sql('UPDATE documents SET '+','.join(k+'=?' for k in data)+' WHERE resource_id=?',tuple(data.values())+(rid,));bump(con,rid)
   activity(con,rid,'updated',data.keys())
   if key:record(con,'document.edit:'+rid,key,original,{'resource_id':rid})
   return self._get(con,rid)
 def bookmark(self,rid,payload):
  with connection(self.database,write=True) as con:
   versioned(con,rid,payload.expected_version);d=self._get(con,rid)
   if d['page_count'] is None or payload.page>d['page_count']:raise HubError('PDF_PAGE_INVALID',422)
   if payload.remove:con.exec_driver_sql('DELETE FROM document_bookmarks WHERE document_id=? AND page=?',(rid,payload.page))
   else:con.exec_driver_sql('INSERT INTO document_bookmarks(id,document_id,page,label,created_at) VALUES(?,?,?,?,?) ON CONFLICT(document_id,page) DO UPDATE SET label=excluded.label',(str(uuid4()),rid,payload.page,payload.label,utc_now()))
   bump(con,rid);activity(con,rid,'bookmark',['page']);return self._get(con,rid)
 def trash(self,rid,version,restore=False):
  with connection(self.database,write=True) as con:
   r=versioned(con,rid,version,trash=True)
   if r['kind']!='document' or bool(r['deleted_at'])!=restore:raise HubError('DOCUMENT_STATE_CONFLICT')
   con.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?',(None if restore else utc_now(),rid));bump(con,rid)
   if not restore:
    con.exec_driver_sql("UPDATE extraction_jobs SET cancel_requested=1 WHERE document_id=? AND state IN('queued','running')",(rid,))
   activity(con,rid,'restored' if restore else 'trashed',['deleted_at']);return self._get(con,rid,True)
 def original(self,rid):
  with connection(self.database) as con:
   d=self._get(con,rid)
   row=con.exec_driver_sql('SELECT b.relative_path,b.sha256 FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,)).first()
   return self.files.read(*row),d['format']
 def jobs(self):
  with connection(self.database) as con:return {'items':rows(con,"SELECT j.id,j.document_id,j.state,j.attempts,j.error_code,j.page_count,j.cancel_requested,j.updated_at,r.title FROM extraction_jobs j JOIN resources r ON r.id=j.document_id WHERE r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY j.created_at DESC LIMIT 100")}
 def job_action(self,rid,version,cancel=False):
  with connection(self.database,write=True) as con:
   versioned(con,rid,version);self._get(con,rid)
   active=con.exec_driver_sql("SELECT id,state FROM extraction_jobs WHERE document_id=? AND state IN('queued','running')",(rid,)).first()
   if cancel:
    if active:con.exec_driver_sql('UPDATE extraction_jobs SET cancel_requested=1,updated_at=? WHERE id=?',(utc_now(),active[0]))
   else:
    if active:raise HubError('PDF_JOB_ACTIVE')
    now=utc_now();con.exec_driver_sql("INSERT INTO extraction_jobs(id,document_id,state,created_at,updated_at) VALUES(?,?,'queued',?,?)",(str(uuid4()),rid,now,now))
    con.exec_driver_sql("UPDATE documents SET extraction_status='queued' WHERE resource_id=?",(rid,));bump(con,rid)
   return self._get(con,rid)
