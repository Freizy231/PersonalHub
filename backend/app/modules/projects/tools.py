"""Fixed desktop adapters. No user-supplied command, prompt, executable or shell."""
from contextlib import contextmanager
from datetime import datetime,timedelta,timezone
from pathlib import Path
from typing import Literal
from uuid import UUID,uuid4
import asyncio
import hashlib
import json
import os
import stat
import subprocess
import threading
from fastapi import APIRouter,Depends,Header,Query,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator
from ...core.database import connection,HubError,utc_now
from ...core.operations import record,replay
from ...core.security import require_session
from ..knowledge.service import rows,visible
from .service import ProjectService
from .schemas import validate_local_directory

ADAPTERS={'terminal':'GNOME Terminal','vscode':'VS Code','codex':'Codex（终端交互）'}
# Installation discovery never executes a program or reads app credentials/configs.
# Tests substitute this fixed server-owned catalog, never an API argument.
CANDIDATES={
    'terminal':[('/usr/bin/gnome-terminal','/usr/bin/bash')],
    'vscode':[('/usr/bin/code',),('/usr/local/bin/code',),('/usr/bin/snap','/snap/code/current/usr/share/code/code')],
    'codex':[('/usr/bin/gnome-terminal',str(Path.home()/'.local/bin/codex')),
             ('/usr/bin/gnome-terminal','/usr/local/bin/codex'),('/usr/bin/gnome-terminal','/usr/bin/codex')],
}
WARNING='只打开你信任的项目。外部应用可能读取项目及自身配置；Hub不会自动提交任务，也不提供恶意代码执行沙箱。'

class Strict(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
class ToolProfile(Strict):
    name:str=Field(min_length=1,max_length=100)
    adapter:Literal['terminal','vscode','codex']
    installation_id:str=Field(min_length=64,max_length=64,pattern='^[a-f0-9]{64}$')
    expected_version:int=Field(default=0,ge=0,strict=True)
class ToolState(Strict):
    expected_version:int=Field(ge=1,strict=True)
    enabled:bool=Field(strict=True)
class ToolPreview(Strict):
    project_id:UUID
    profile_id:UUID
    expected_project_version:int=Field(ge=1,strict=True)
class ToolConfirm(Strict):
    confirmed:bool=Field(strict=True)
    @field_validator('confirmed')
    @classmethod
    def explicit_true(cls,value):
        if value is not True:raise ValueError('explicit confirmation required')
        return value


def encoded(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False)

def secure_mode(path,info):
    # Ubuntu snap squashfs can report 0775 but is mounted read-only.
    # Sandboxed read-only system mounts can also remap root to nobody.
    readonly=bool(os.statvfs(path).f_flag & os.ST_RDONLY)
    system_uid=os.stat('/').st_uid
    if info.st_uid not in (0,system_uid,os.geteuid()) and not readonly:raise HubError('TOOL_UNSAFE_PATH',422)
    if info.st_mode&0o022 and not readonly:
        if not (stat.S_ISDIR(info.st_mode) and info.st_mode&stat.S_ISVTX and info.st_uid in (0,system_uid)):
            raise HubError('TOOL_UNSAFE_PATH',422)

@contextmanager
def directory_identity(path):
    try:validate_local_directory(path)
    except (ValueError,TypeError):raise HubError('PROJECT_DIRECTORY_REQUIRED',422) from None
    if not path:raise HubError('PROJECT_DIRECTORY_REQUIRED',422)
    descriptor=None
    try:
        descriptor=os.open('/',os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        chain=[];current=Path('/')
        for part in Path(path).parts[1:]:
            child=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=descriptor)
            os.close(descriptor);descriptor=child;current=current/part
            info=os.fstat(descriptor);secure_mode(current,info)
            chain.append([info.st_dev,info.st_ino])
        yield descriptor,chain
    except OSError:raise HubError('PROJECT_DIRECTORY_UNAVAILABLE',422) from None
    finally:
        if descriptor is not None:os.close(descriptor)


def installation(adapter,paths):
    facts=[]
    for source in paths:
        try:
            target=Path(source).resolve(strict=True)
            # Prefix validation rejects writable/foreign program directories.
            with directory_identity(str(target.parent)):pass
            info=target.stat();secure_mode(target,info)
            if not stat.S_ISREG(info.st_mode) or not os.access(target,os.X_OK):raise HubError('TOOL_UNAVAILABLE',422)
        except (OSError,RuntimeError):raise HubError('TOOL_UNAVAILABLE',422) from None
        facts.append({'source':source,'resolved':str(target),'dev':info.st_dev,'ino':info.st_ino,
            'size':info.st_size,'mtime_ns':info.st_mtime_ns,'ctime_ns':info.st_ctime_ns,'mode':info.st_mode,'uid':info.st_uid,'gid':info.st_gid})
    return {'adapter':adapter,'programs':facts}


def installations():
    found=[]
    for adapter,candidates in CANDIDATES.items():
        for paths in candidates:
            try:identity=installation(adapter,paths)
            except HubError:continue
            iid=hashlib.sha256(encoded(identity).encode()).hexdigest()
            found.append({'installation_id':iid,'adapter':adapter,'label':ADAPTERS[adapter],
                'executables':[p['source'] for p in identity['programs']],'identity':identity})
    return found


def environment():
    names=('DISPLAY','WAYLAND_DISPLAY','XAUTHORITY','XDG_RUNTIME_DIR','DBUS_SESSION_BUS_ADDRESS','LANG','LC_ALL','HOME','XDG_DATA_DIRS','XDG_CURRENT_DESKTOP')
    env={k:v for k,v in os.environ.items() if k in names}
    env['PATH']='/usr/local/bin:/usr/bin:/bin:/snap/bin'
    return env

class ProjectToolService(ProjectService):
    def _profile(self,c,pid):
        found=rows(c,'SELECT * FROM project_tool_profiles WHERE id=?',(pid,))
        if not found:raise HubError('TOOL_PROFILE_NOT_FOUND',404)
        return found[0]

    @staticmethod
    def public(profile):return {k:v for k,v in profile.items() if k!='identity_json'}

    def profiles(self):
        catalog=installations();ids={i['installation_id'] for i in catalog}
        with connection(self.database) as c:
            found=rows(c,'SELECT * FROM project_tool_profiles ORDER BY name,id')
        return {'items':[{**self.public(p),'available':p['installation_id'] in ids} for p in found],
                'installations':[{k:v for k,v in i.items() if k!='identity'} for i in catalog],
                'adapters':[{'id':k,'label':v,'available':any(i['adapter']==k for i in catalog)} for k,v in ADAPTERS.items()]}

    def profile_save(self,payload,key,pid=None):
        data=payload.model_dump(mode='json');operation='project.tool.profile:'+str(pid)
        with connection(self.database) as c:
            prior=replay(c,operation,key,data)
            if prior:return self.public(self._profile(c,prior['profile_id']))
        chosen=next((i for i in installations() if i['adapter']==payload.adapter and i['installation_id']==payload.installation_id),None)
        if not chosen:raise HubError('TOOL_INSTALLATION_CHANGED',409)
        with connection(self.database,write=True) as c:
            prior=replay(c,operation,key,data)
            if prior:return self.public(self._profile(c,prior['profile_id']))
            now=utc_now()
            if pid:
                current=self._profile(c,pid)
                if current['version']!=payload.expected_version:raise HubError('VERSION_CONFLICT')
                if current['adapter']!=payload.adapter:raise HubError('TOOL_ADAPTER_IMMUTABLE',422)
                c.exec_driver_sql('UPDATE project_tool_profiles SET name=?,installation_id=?,identity_json=?,version=version+1,updated_at=? WHERE id=?',
                    (payload.name,payload.installation_id,encoded(chosen['identity']),now,pid))
            else:
                if c.exec_driver_sql('SELECT 1 FROM project_tool_profiles WHERE adapter=?',(payload.adapter,)).first():raise HubError('TOOL_PROFILE_EXISTS')
                pid=str(uuid4())
                c.exec_driver_sql('INSERT INTO project_tool_profiles VALUES(?,?,?,?,?,1,1,?,?)',
                    (pid,payload.name,payload.adapter,payload.installation_id,encoded(chosen['identity']),now,now))
            record(c,operation,key,data,{'profile_id':pid})
            return self.public(self._profile(c,pid))

    def profile_state(self,pid,payload,key):
        data=payload.model_dump(mode='json');operation='project.tool.state:'+pid
        with connection(self.database,write=True) as c:
            current=self._profile(c,pid)
            if replay(c,operation,key,data) is None:
                if current['version']!=payload.expected_version:raise HubError('VERSION_CONFLICT')
                c.exec_driver_sql('UPDATE project_tool_profiles SET enabled=?,version=version+1,updated_at=? WHERE id=?',(int(payload.enabled),utc_now(),pid))
                record(c,operation,key,data,{'profile_id':pid})
            return self.public(self._profile(c,pid))

    def _plan(self,r,p):
        if not p['enabled']:raise HubError('TOOL_PROFILE_DISABLED')
        identity=json.loads(p['identity_json']);programs=identity['programs']
        sources=tuple(item['source'] for item in programs)
        if sources not in CANDIDATES[p['adapter']]:raise HubError('TOOL_INSTALLATION_CHANGED')
        if installation(p['adapter'],sources)!=identity:raise HubError('TOOL_INSTALLATION_CHANGED')
        path=r['local_directory']
        with directory_identity(path) as (_,chain):pass
        exe=programs[0]['resolved']
        if p['adapter']=='terminal':args=[exe,'--window','--working-directory='+path,'--',programs[1]['resolved'],'--noprofile','--norc']
        elif p['adapter']=='codex':args=[exe,'--window','--working-directory='+path,'--',programs[1]['resolved'],'--cd',path,'--sandbox','read-only','--ask-for-approval','on-request','--no-daemon']
        elif programs[0]['source']=='/usr/bin/snap':args=[exe,'run','code','--new-window','--disable-extensions','--',path]
        else:args=[programs[0]['source'],'--new-window','--disable-extensions','--',path]
        return {'arguments':args,'directory':path,'directory_chain':chain,'installation':identity}

    def _preview_result(self,prev,r,p):
        plan=json.loads(prev['plan_json'])
        return {'id':prev['id'],'project_id':r['id'],'title':r['title'],'profile_name':p['name'],
            'adapter':p['adapter'],'arguments':plan['arguments'],'directory':plan['directory'],
            'expires_at':prev['expires_at'],'warning':WARNING}

    def preview(self,payload,key):
        data=payload.model_dump(mode='json');operation='project.tool.preview'
        # Slow filesystem checks stay outside the write transaction.
        with connection(self.database) as c:
            r=self._editable(c,str(payload.project_id));p=self._profile(c,str(payload.profile_id))
            prior=replay(c,operation,key,data)
            if prior:
                prev=rows(c,'SELECT * FROM project_tool_previews WHERE id=?',(prior['preview_id'],))[0]
                return self._preview_result(prev,r,p)
            if r['version']!=payload.expected_project_version:raise HubError('VERSION_CONFLICT')
        plan=self._plan(r,p)
        with connection(self.database,write=True) as c:
            current=self._editable(c,r['id']);profile=self._profile(c,p['id'])
            prior=replay(c,operation,key,data)
            if prior:
                prev=rows(c,'SELECT * FROM project_tool_previews WHERE id=?',(prior['preview_id'],))[0]
                return self._preview_result(prev,current,profile)
            if current['version']!=r['version'] or profile['version']!=p['version']:raise HubError('VERSION_CONFLICT')
            now=utc_now();pid=str(uuid4());expires=(datetime.now(timezone.utc)+timedelta(seconds=60)).isoformat(timespec='microseconds').replace('+00:00','Z')
            c.exec_driver_sql('INSERT INTO project_tool_previews(id,project_id,profile_id,project_version,profile_version,plan_json,expires_at,created_at) VALUES(?,?,?,?,?,?,?,?)',
                (pid,r['id'],p['id'],r['version'],p['version'],encoded(plan),expires,now))
            record(c,operation,key,data,{'preview_id':pid})
            prev=rows(c,'SELECT * FROM project_tool_previews WHERE id=?',(pid,))[0]
        return self._preview_result(prev,r,p)

    def _history_item(self,c,h):
        r=self._get(c,h['project_id']);p=self._profile(c,h['profile_id'])
        return {**h,'status':'uncertain' if h['status']=='starting' else h['status'],'title':r['title'],'profile_name':p['name'],'adapter':p['adapter']}

    def confirm(self,pid):
        with connection(self.database) as c:
            prevs=rows(c,'SELECT * FROM project_tool_previews WHERE id=?',(pid,))
            if not prevs:raise HubError('TOOL_PREVIEW_NOT_FOUND',404)
            prev=prevs[0];self._get(c,prev['project_id'])
            prior=rows(c,'SELECT * FROM project_tool_history WHERE preview_id=?',(pid,))
            if prior:return self._history_item(c,prior[0])
            r=self._editable(c,prev['project_id']);p=self._profile(c,prev['profile_id'])
        if utc_now()>prev['expires_at'] or r['version']!=prev['project_version'] or p['version']!=prev['profile_version']:raise HubError('TOOL_PREVIEW_EXPIRED')
        plan=self._plan(r,p)
        if encoded(plan)!=prev['plan_json']:raise HubError('TOOL_PREVIEW_CHANGED')
        with directory_identity(plan['directory']) as (directory,chain):
            if chain!=plan['directory_chain']:raise HubError('TOOL_PREVIEW_CHANGED')
            with connection(self.database,write=True) as c:
                current=self._editable(c,r['id']);profile=self._profile(c,p['id'])
                prior=rows(c,'SELECT * FROM project_tool_history WHERE preview_id=?',(pid,))
                if prior:return self._history_item(c,prior[0])
                if utc_now()>prev['expires_at'] or current['version']!=r['version'] or profile['version']!=p['version'] or not profile['enabled']:raise HubError('TOOL_PREVIEW_EXPIRED')
                hid=str(uuid4());now=utc_now()
                c.exec_driver_sql("INSERT INTO project_tool_history VALUES(?,?,?,?,'starting',NULL,?,?)",(hid,pid,r['id'],p['id'],now,now))
                c.exec_driver_sql('UPDATE project_tool_previews SET confirmed_at=? WHERE id=?',(now,pid))
            # A durable reservation precedes ALL process side effects. Never retry
            # an existing starting row: a crash might already have launched it.
            status='started';code=None
            try:
                # Recheck program identity and path immediately before spawn.
                if encoded(self._plan(r,p))!=prev['plan_json']:raise HubError('TOOL_PREVIEW_CHANGED')
                process=subprocess.Popen(plan['arguments'],shell=False,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                    cwd='/proc/self/fd/'+str(directory),pass_fds=(directory,),env=environment(),start_new_session=True)
                try:
                    rc=process.wait(timeout=.25)
                    if rc:status='failed';code='TOOL_EXITED'
                except subprocess.TimeoutExpired:threading.Thread(target=process.wait,daemon=True).start()
            except (OSError,HubError):status='failed';code='TOOL_START_FAILED'
        with connection(self.database,write=True) as c:
            c.exec_driver_sql('UPDATE project_tool_history SET status=?,error_code=?,updated_at=? WHERE id=?',(status,code,utc_now(),hid))
        with connection(self.database) as c:
            return self._history_item(c,rows(c,'SELECT * FROM project_tool_history WHERE id=?',(hid,))[0])

    def history(self,project_id=None,limit=30,offset=0):
        with connection(self.database) as c:
            if project_id:self._get(c,project_id)
            where="r.privacy_level='personal' AND r.deleted_at IS NULL"+(' AND h.project_id=?' if project_id else '')
            args=(project_id,) if project_id else ()
            count=c.exec_driver_sql('SELECT count(*) FROM project_tool_history h JOIN resources r ON r.id=h.project_id WHERE '+where,args).scalar_one()
            found=rows(c,'SELECT h.* FROM project_tool_history h JOIN resources r ON r.id=h.project_id WHERE '+where+' ORDER BY h.created_at DESC,h.id LIMIT ? OFFSET ?',args+(limit,offset))
            return {'items':[self._history_item(c,h) for h in found],'total':count,'limit':limit,'offset':offset}

router=APIRouter(prefix='/api/v1/project-tools',dependencies=[Depends(require_session)])
def service(r):return ProjectToolService(r.app.state.settings.database)
@router.get('')
async def profiles(request:Request):return await asyncio.to_thread(service(request).profiles)
@router.post('')
async def create(payload:ToolProfile,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).profile_save,payload,str(idempotency_key))
@router.put('/{pid}')
async def update(pid:UUID,payload:ToolProfile,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).profile_save,payload,str(idempotency_key),str(pid))
@router.post('/{pid}/state')
async def state(pid:UUID,payload:ToolState,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).profile_state,str(pid),payload,str(idempotency_key))
@router.post('/preview')
async def preview(payload:ToolPreview,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).preview,payload,str(idempotency_key))
@router.post('/confirm/{pid}')
async def confirm(pid:UUID,payload:ToolConfirm,request:Request):return await asyncio.to_thread(service(request).confirm,str(pid))
@router.get('/history')
async def history(request:Request,project_id:UUID|None=None,limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).history,str(project_id) if project_id else None,limit,offset)
