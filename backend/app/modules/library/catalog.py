"""File-independent books, manual web bookmarks and courses with concrete schemas."""
import asyncio
from uuid import UUID
from typing import Literal
from urllib.parse import urlsplit
from fastapi import APIRouter,Depends,Header,Query,Request
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from ...core.database import connection,HubError,utc_now
from ...core.operations import activity,record,replay
from ...core.security import require_session
from ..knowledge.service import visible,versioned,bump,create_resource,rows
from ..knowledge.router import Version

class CatalogBase(BaseModel):
 model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
 title:str=Field(min_length=1,max_length=500)
 expected_version:int=Field(default=0,ge=0,strict=True)
class Book(CatalogBase):
 authors:str=Field(default='',max_length=2000)
 isbn:str=Field(default='',max_length=50)
 publisher:str=Field(default='',max_length=500)
 year:int|None=Field(default=None,ge=1,le=9999,strict=True)
 category:str=Field(default='',max_length=100)
 read_state:Literal['unread','reading','completed','paused']='unread'
 total_pages:int|None=Field(default=None,ge=1,le=1000000,strict=True)
 current_page:int=Field(default=0,ge=0,strict=True)
 progress_percent:int=Field(default=0,ge=0,le=100,strict=True)
 rating:int|None=Field(default=None,ge=1,le=5,strict=True)
 description:str=Field(default='',max_length=10000)
 @model_validator(mode='after')
 def pages(self):
  if self.current_page and (self.total_pages is None or self.current_page>self.total_pages):raise ValueError('invalid page')
  if self.total_pages:self.progress_percent=min(100,self.current_page*100//self.total_pages)
  return self

def safe_url(value,empty=False):
 if not value and empty:return value
 try:
  p=urlsplit(value);port=p.port
  if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or any(ord(c)<33 for c in value) or '\\' in value:raise ValueError('unsafe url')
 except ValueError:raise ValueError('unsafe url') from None
 return value
class WebBookmark(CatalogBase):
 url:str=Field(min_length=1,max_length=2048)
 summary:str=Field(default='',max_length=10000)
 read_state:Literal['unread','reading','completed','paused']='unread'
 category:str=Field(default='',max_length=100)
 _url=field_validator('url')(safe_url)
class Course(CatalogBase):
 provider:str=Field(default='',max_length=500)
 url:str=Field(default='',max_length=2048)
 description:str=Field(default='',max_length=10000)
 status:Literal['planned','active','completed','paused']='planned'
 total_units:int=Field(default=1,ge=1,le=1000000,strict=True)
 completed_units:int=Field(default=0,ge=0,strict=True)
 @field_validator('url')
 @classmethod
 def url_valid(cls,v):return safe_url(v,True)
 @model_validator(mode='after')
 def units(self):
  if self.completed_units>self.total_units:raise ValueError('invalid units')
  return self
SCHEMAS={'book':Book,'bookmark':WebBookmark,'course':Course}
TABLES={'book':'books','bookmark':'web_bookmarks','course':'courses'}
class CatalogService:
 def __init__(self,database):self.database=database
 def _get(self,c,kind,rid,trash=False):
  r=visible(c,rid,trash=trash)
  if r['kind']!=kind:raise HubError('RESOURCE_NOT_FOUND',404)
  r.update(rows(c,'SELECT * FROM '+TABLES[kind]+' WHERE resource_id=?',(rid,))[0]);r.pop('resource_id',None)
  from ..tags.service import tag_map
  r['tags']=tag_map(c,[rid])[rid];return r
 def get(self,kind,rid):
  with connection(self.database) as c:return self._get(c,kind,rid)
 def list(self,kind,trash=False,q='',limit=30,offset=0,tag_id=None,category=''):
  with connection(self.database) as c:
   where="kind=? AND privacy_level='personal' AND deleted_at IS "+('NOT NULL' if trash else 'NULL')+' AND instr(lower(title),lower(?))>0'
   where+=" AND (? IS NULL OR EXISTS(SELECT 1 FROM resource_tags rt WHERE rt.resource_id=resources.id AND rt.tag_id=?))"
   params=(kind,q,tag_id,tag_id)
   if category:
    if kind=='course':return {'items':[],'total':0,'limit':limit,'offset':offset}
    where+=' AND EXISTS(SELECT 1 FROM '+TABLES[kind]+' c WHERE c.resource_id=resources.id AND c.category=?)';params+=(category,)
   total=c.exec_driver_sql('SELECT count(*) FROM resources WHERE '+where,params).scalar_one()
   found=rows(c,'SELECT id FROM resources WHERE '+where+' ORDER BY updated_at DESC,id LIMIT ? OFFSET ?',params+(limit,offset))
   return {'items':[self._get(c,kind,r['id'],trash) for r in found],'total':total,'limit':limit,'offset':offset}
 def save(self,kind,payload,key,rid=None):
  data=payload.model_dump(mode='json');operation='catalog.save:'+kind+':'+str(rid)
  with connection(self.database,write=True) as c:
   if rid:self._get(c,kind,rid)
   prior=replay(c,operation,key,data)
   if prior:return self._get(c,kind,prior['resource_id'])
   fields={k:v for k,v in data.items() if k not in ('title','expected_version')};table=TABLES[kind]
   if rid:
    versioned(c,rid,payload.expected_version);c.exec_driver_sql('UPDATE resources SET title=? WHERE id=?',(payload.title,rid));bump(c,rid)
    c.exec_driver_sql(f'UPDATE {table} SET '+','.join(k+'=?' for k in fields)+' WHERE resource_id=?',tuple(fields.values())+(rid,))
   else:
    rid=create_resource(c,kind,payload.title)
    c.exec_driver_sql(f'INSERT INTO {table}(resource_id,'+','.join(fields)+') VALUES('+','.join('?' for _ in range(len(fields)+1))+')',(rid,*fields.values()))
   activity(c,rid,'saved',fields.keys());record(c,operation,key,data,{'resource_id':rid});return self._get(c,kind,rid)
 def trash(self,kind,rid,version,restore=False):
  with connection(self.database,write=True) as c:
   current=self._get(c,kind,rid,True);versioned(c,rid,version,trash=True)
   if bool(current['deleted_at'])!=restore:raise HubError('RESOURCE_STATE_CONFLICT')
   c.exec_driver_sql('UPDATE resources SET deleted_at=? WHERE id=?',(None if restore else utc_now(),rid));bump(c,rid);activity(c,rid,'restored' if restore else 'trashed',['deleted_at'])
   return self._get(c,kind,rid,True)
router=APIRouter(prefix='/api/v1/catalog',dependencies=[Depends(require_session)])
Kind=Literal['book','bookmark','course']
def service(r):return CatalogService(r.app.state.settings.database)
@router.get('/{kind}')
async def listing(kind:Kind,request:Request,trash:bool=False,q:str=Query('',max_length=256),limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000),tag_id:UUID|None=None,category:str=Query('',max_length=100)):
 return await asyncio.to_thread(service(request).list,kind,trash,q,limit,offset,str(tag_id) if tag_id else None,category)
@router.get('/{kind}/{rid}')
async def get(kind:Kind,rid:UUID,request:Request):return await asyncio.to_thread(service(request).get,kind,str(rid))
# Separate concrete DTOs keep API validation unambiguous.
@router.post('/book')
async def book(payload:Book,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'book',payload,str(idempotency_key))
@router.put('/book/{rid}')
async def book_update(rid:UUID,payload:Book,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'book',payload,str(idempotency_key),str(rid))
@router.post('/bookmark')
async def bookmark(payload:WebBookmark,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'bookmark',payload,str(idempotency_key))
@router.put('/bookmark/{rid}')
async def bookmark_update(rid:UUID,payload:WebBookmark,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'bookmark',payload,str(idempotency_key),str(rid))
@router.post('/course')
async def course(payload:Course,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'course',payload,str(idempotency_key))
@router.put('/course/{rid}')
async def course_update(rid:UUID,payload:Course,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save,'course',payload,str(idempotency_key),str(rid))
@router.post('/{kind}/{rid}/{action}')
async def trash(kind:Kind,rid:UUID,action:Literal['trash','restore'],payload:Version,request:Request):return await asyncio.to_thread(service(request).trash,kind,str(rid),payload.expected_version,action=='restore')
