import asyncio
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query,Request
from fastapi.responses import Response
from pydantic import BaseModel,ConfigDict,Field,field_validator
from ....core.security import require_session
from ...knowledge.router import Version
from .service import DocumentService
router=APIRouter(prefix='/api/v1/documents',dependencies=[Depends(require_session)])
class DocumentWrite(Version):
 title:str=Field(min_length=1,max_length=500)
 document_type:Literal['paper','book','report','other']='paper'
 authors:str=Field(default='',max_length=2000)
 year:int|None=Field(default=None,ge=1,le=9999,strict=True)
 doi:str=Field(default='',max_length=500)
 source_url:str=Field(default='',max_length=2048)
 abstract:str=Field(default='',max_length=10000)
 category:str=Field(default='',max_length=100)
 read_state:Literal['unread','reading','completed','paused']='unread'
 current_page:int=Field(default=0,ge=0,strict=True)
 @field_validator('title')
 @classmethod
 def title_valid(cls,v):
  if not v.strip():raise ValueError('empty title')
  return v.strip()
class BookmarkWrite(Version):
 page:int=Field(ge=1,strict=True)
 label:str=Field(default='',max_length=500)
 remove:bool=False
def service(r):return DocumentService(r.app.state.settings.database)
@router.get('')
async def listing(request:Request,trash:bool=False,state:str=Query('',max_length=30),q:str=Query('',max_length=256),limit:int=Query(30,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000),tag_id:UUID|None=None,category:str=Query('',max_length=100)):
 return await asyncio.to_thread(service(request).list,trash,state,q,limit,offset,str(tag_id) if tag_id else None,category)
@router.get('/jobs')
async def jobs(request:Request):return await asyncio.to_thread(service(request).jobs)
@router.post('/import')
async def upload(request:Request,name:str=Query(...,min_length=1,max_length=255),idempotency_key:UUID=Header()):return await service(request).upload(request,name,str(idempotency_key))
@router.get('/{rid}')
async def get(rid:UUID,request:Request):return await asyncio.to_thread(service(request).get,str(rid))
@router.get('/{rid}/original')
async def original(rid:UUID,request:Request):
 data,fmt=await asyncio.to_thread(service(request).original,str(rid))
 if fmt=='epub':return Response(data,media_type='application/epub+zip',headers={'Content-Disposition':'attachment; filename="document.epub"'})
 return Response(data,media_type='application/pdf',headers={'Content-Disposition':'attachment; filename="document.pdf"'})
@router.put('/{rid}')
async def patch(rid:UUID,payload:DocumentWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).patch,str(rid),payload,str(idempotency_key))
@router.put('/{rid}/bookmark')
async def bookmark(rid:UUID,payload:BookmarkWrite,request:Request):return await asyncio.to_thread(service(request).bookmark,str(rid),payload)
@router.post('/{rid}/{action}')
async def action(rid:UUID,action:Literal['trash','restore','retry','cancel'],payload:Version,request:Request):
 if action in ('trash','restore'):return await asyncio.to_thread(service(request).trash,str(rid),payload.expected_version,action=='restore')
 return await asyncio.to_thread(service(request).job_action,str(rid),payload.expected_version,action=='cancel')
