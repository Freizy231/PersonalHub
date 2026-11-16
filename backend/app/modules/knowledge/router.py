import asyncio
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query,Request
from pydantic import BaseModel,ConfigDict,Field
from ...core.security import require_session
from .service import KnowledgeService
from .purge import PurgeService
from .stalled import StalledService
router=APIRouter(prefix='/api/v1',dependencies=[Depends(require_session)])
class Version(BaseModel):
    model_config=ConfigDict(extra='forbid')
    expected_version:int=Field(ge=1,strict=True)
class Relation(Version):
    target_id:UUID
    relation_type:Literal['related','material','practice','output']='related'
class CollectionWrite(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    title:str=Field(min_length=1,max_length=300)
    description:str=Field(default='',max_length=10000)
    expected_version:int=Field(default=0,ge=0,strict=True)
    members:list[UUID]=Field(default_factory=list,max_length=1000)
def service(r):return KnowledgeService(r.app.state.settings.database)
@router.get('/resources')
async def catalog(request:Request,q:str=Query('',max_length=256),kind:str=Query('',max_length=40),limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).catalog,q,kind,limit,offset)
@router.get('/resources/{rid}')
async def resource(rid:UUID,request:Request):return await asyncio.to_thread(service(request).get,str(rid))
@router.get('/resources/{rid}/context')
async def context(rid:UUID,request:Request):return await asyncio.to_thread(service(request).context,str(rid))
@router.post('/resources/{rid}/relations')
async def relate(rid:UUID,payload:Relation,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).relate,str(rid),payload,str(idempotency_key))
@router.post('/resources/{rid}/relations/{eid}/remove')
async def unlink(rid:UUID,eid:UUID,payload:Version,request:Request):return await asyncio.to_thread(service(request).unlink,str(rid),str(eid),payload.expected_version)
@router.get('/collections')
async def collections(request:Request,trash:bool=False,limit:int=Query(100,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):return await asyncio.to_thread(service(request).collections,trash,limit,offset)
@router.get('/collections/{rid}')
async def collection(rid:UUID,request:Request):return await asyncio.to_thread(service(request).collection,str(rid))
@router.post('/collections')
async def create(payload:CollectionWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save_collection,payload,str(idempotency_key))
@router.put('/collections/{rid}')
async def save(rid:UUID,payload:CollectionWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).save_collection,payload,str(idempotency_key),str(rid))
@router.post('/collections/{rid}/{action}')
async def trash(rid:UUID,action:Literal['trash','restore'],payload:Version,request:Request):return await asyncio.to_thread(service(request).trash_collection,str(rid),payload.expected_version,action=='restore')
from .search import SearchService
@router.get('/search')
async def search(request:Request,q:str=Query('',max_length=256),kind:str=Query('',max_length=40),limit:int=Query(20,ge=1,le=100),offset:int=Query(0,ge=0,le=10000)):
    return await asyncio.to_thread(SearchService(request.app.state.settings.database).query,q,kind,limit,offset)
@router.post('/search/rebuild')
async def rebuild(request:Request):return await asyncio.to_thread(SearchService(request.app.state.settings.database).rebuild)


def purge_service(request):
    settings = request.app.state.settings
    return PurgeService(settings.database, settings.data_root)


@router.get('/trash/{rid}/purge-preview')
async def purge_preview(rid: UUID, request: Request):
    return await asyncio.to_thread(purge_service(request).preview, str(rid))


@router.post('/trash/{rid}/purge')
async def purge(rid: UUID, payload: Version, request: Request, idempotency_key: UUID = Header()):
    return await asyncio.to_thread(purge_service(request).purge, str(rid), payload.expected_version)


@router.get('/stalled')
async def stalled(request: Request, threshold_days: int | None = Query(None, ge=0, le=3650),
                  limit: int = Query(200, ge=1, le=500)):
    return await asyncio.to_thread(StalledService(request.app.state.settings.database).report,
                                   threshold_days, limit)
