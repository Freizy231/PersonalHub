"""PDF-only executable whitelist, expiring preview and explicit one-shot confirmation."""
import os,subprocess
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import uuid4,UUID
from typing import Literal
from fastapi import APIRouter,Depends,Request
from pydantic import BaseModel,ConfigDict,Field
import asyncio
from ....core.database import connection,HubError,utc_now
from ....core.security import require_session
from ...knowledge.service import rows,visible
from .service import DocumentService
ALLOWED=('/usr/bin/evince','/usr/bin/okular','/usr/bin/mupdf','/usr/bin/zathura')
class Profile(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 name:str=Field(min_length=1,max_length=100)
 executable:Literal['/usr/bin/evince','/usr/bin/okular','/usr/bin/mupdf','/usr/bin/zathura']
 enabled:bool=True
 expected_version:int=Field(default=0,ge=0,strict=True)
class Preview(BaseModel):
 model_config=ConfigDict(extra='forbid')
 document_id:UUID
 profile_id:UUID
class Confirm(BaseModel):
 model_config=ConfigDict(extra='forbid')
 confirmed:Literal[True]
class ReaderService(DocumentService):
 def profiles(self):
  with connection(self.database) as c:
   return {'items':rows(c,'SELECT * FROM external_app_profiles ORDER BY name,id'),'allowed':[{'executable':p,'available':Path(p).is_file()} for p in ALLOWED]}
 def profile(self,payload,pid=None):
  exe=Path(payload.executable)
  if not exe.is_file() or exe.is_symlink() or not os.access(exe,os.X_OK) or exe.stat().st_mode&0o022:raise HubError('READER_UNAVAILABLE',422)
  with connection(self.database,write=True) as c:
   now=utc_now()
   if pid:
    current=c.exec_driver_sql('SELECT version FROM external_app_profiles WHERE id=?',(pid,)).first()
    if not current:raise HubError('READER_NOT_FOUND',404)
    if current[0]!=payload.expected_version:raise HubError('VERSION_CONFLICT')
    c.exec_driver_sql('UPDATE external_app_profiles SET name=?,executable=?,enabled=?,version=version+1,updated_at=? WHERE id=?',(payload.name,payload.executable,int(payload.enabled),now,pid))
   else:
    pid=str(uuid4());c.exec_driver_sql('INSERT INTO external_app_profiles VALUES(?,?,?,?,1,?,?)',(pid,payload.name,payload.executable,int(payload.enabled),now,now))
   return rows(c,'SELECT * FROM external_app_profiles WHERE id=?',(pid,))[0]
 def verify(self,rid):
  # Reads and hashes the original (up to the upload limit). Done in a read-only transaction, so a
  # slow disk can never hold the write lock and starve other writers.
  with connection(self.database) as c:
   path=c.exec_driver_sql('SELECT b.relative_path,b.sha256 FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,)).first()
  if path:self.files.read(*path)
 def arguments(self,c,rid,pid):
  r=visible(c,rid)
  if r['kind']!='document':raise HubError('DOCUMENT_NOT_FOUND',404)
  p=rows(c,'SELECT * FROM external_app_profiles WHERE id=? AND enabled=1',(pid,))
  if not p:raise HubError('READER_NOT_FOUND',404)
  p=p[0];path=c.exec_driver_sql('SELECT b.relative_path,b.sha256 FROM documents d JOIN blobs b ON b.id=d.blob_id WHERE d.resource_id=?',(rid,)).first()
  exe=Path(p['executable'])
  if str(exe) not in ALLOWED or not exe.is_file() or exe.is_symlink() or exe.stat().st_mode&0o022:raise HubError('READER_UNAVAILABLE')
  return r,p,[str(exe),'--',str(self.root/path[0])]
 def preview(self,payload):
  self.verify(str(payload.document_id))
  with connection(self.database,write=True) as c:
   r,p,args=self.arguments(c,str(payload.document_id),str(payload.profile_id));pid=str(uuid4());now=utc_now()
   expires=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(timespec='microseconds').replace('+00:00','Z')
   c.exec_driver_sql('INSERT INTO launch_previews(id,document_id,profile_id,profile_version,document_version,expires_at,created_at) VALUES(?,?,?,?,?,?,?)',(pid,r['id'],p['id'],p['version'],r['version'],expires,now))
   return {'id':pid,'title':r['title'],'arguments':args,'expires_at':expires,'warning':'将由本机阅读器打开 PDF。Hub 不会获取阅读进度。'}
 def confirm(self,pid):
  with connection(self.database) as c:
   known=rows(c,'SELECT document_id FROM launch_previews WHERE id=?',(pid,))
  if not known:raise HubError('READER_PREVIEW_NOT_FOUND',404)
  self.verify(known[0]['document_id'])
  with connection(self.database,write=True) as c:
   prev=rows(c,'SELECT * FROM launch_previews WHERE id=?',(pid,))
   if not prev:raise HubError('READER_PREVIEW_NOT_FOUND',404)
   prev=prev[0];r,p,args=self.arguments(c,prev['document_id'],prev['profile_id'])
   prior=rows(c,'SELECT * FROM launch_history WHERE preview_id=?',(pid,))
   if prior:return {**prior[0],'status':'uncertain' if prior[0]['status']=='starting' else prior[0]['status']}
   if prev['expires_at']<utc_now() or p['version']!=prev['profile_version'] or r['version']!=prev['document_version']:raise HubError('READER_PREVIEW_EXPIRED')
   hid=str(uuid4());c.exec_driver_sql("INSERT INTO launch_history(id,preview_id,document_id,profile_id,status,created_at) VALUES(?,?,?,?,'starting',?)",(hid,pid,r['id'],p['id'],utc_now()))
   c.exec_driver_sql('UPDATE launch_previews SET confirmed_at=? WHERE id=?',(utc_now(),pid))
  status='started';code=None
  try:
   env={k:v for k,v in os.environ.items() if k in ('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG','HOME')};env['PATH']='/usr/bin:/bin'
   process=subprocess.Popen(args,shell=False,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,env=env,start_new_session=True)
   try:
    rc=process.wait(timeout=.2)
    if rc:status='failed';code='READER_EXITED'
   except subprocess.TimeoutExpired:
    import threading
    threading.Thread(target=process.wait,daemon=True).start()
  except OSError:status='failed';code='READER_START_FAILED'
  with connection(self.database,write=True) as c:
   c.exec_driver_sql('UPDATE launch_history SET status=?,error_code=? WHERE id=?',(status,code,hid))
   return rows(c,'SELECT * FROM launch_history WHERE id=?',(hid,))[0]
 def history(self):
  with connection(self.database) as c:return {'items':rows(c,"SELECT h.id,h.status,h.error_code,h.created_at,r.title FROM launch_history h JOIN resources r ON r.id=h.document_id WHERE r.privacy_level='personal' AND r.deleted_at IS NULL ORDER BY h.created_at DESC LIMIT 100")}
router=APIRouter(prefix='/api/v1/readers',dependencies=[Depends(require_session)])
def service(r):return ReaderService(r.app.state.settings.database)
@router.get('')
async def profiles(request:Request):return await asyncio.to_thread(service(request).profiles)
@router.post('')
async def profile(payload:Profile,request:Request):return await asyncio.to_thread(service(request).profile,payload)
@router.put('/{pid}')
async def update(pid:UUID,payload:Profile,request:Request):return await asyncio.to_thread(service(request).profile,payload,str(pid))
@router.get('/history')
async def history(request:Request):return await asyncio.to_thread(service(request).history)
@router.post('/preview')
async def preview(payload:Preview,request:Request):return await asyncio.to_thread(service(request).preview,payload)
@router.post('/confirm/{pid}')
async def confirm(pid:UUID,payload:Confirm,request:Request):return await asyncio.to_thread(service(request).confirm,str(pid))
