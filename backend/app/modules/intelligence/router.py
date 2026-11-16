import asyncio
from uuid import UUID
from typing import Literal
from fastapi import APIRouter,Depends,Header,Query,Request
from ...core.security import require_session
from .schemas import ScheduleWrite, SourceWrite,ItemWrite,SubscriptionWrite
from .service import IntelligenceService
router=APIRouter(prefix='/api/v1/intelligence',dependencies=[Depends(require_session)])
def service(r):return IntelligenceService(r.app.state.settings)
@router.get('/sources')
async def sources(request:Request):return await asyncio.to_thread(service(request).sources)
@router.post('/sources')
async def source(p:SourceWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).source_save,p,str(idempotency_key))
@router.put('/sources/{rid}')
async def source_edit(rid:UUID,p:SourceWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).source_save,p,str(idempotency_key),str(rid))
@router.post('/sources/{rid}/collect')
async def collect(rid:UUID,request:Request,idempotency_key:UUID=Header()):return await service(request).collect(str(rid),str(idempotency_key))
@router.post('/legacy/import')
async def legacy(request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).import_legacy,str(idempotency_key))
@router.get('/items')
async def listing(request:Request,q:str=Query('',max_length=200),source:str=Query('',max_length=100),domain:str=Query('',max_length=80),state:Literal['','new','later','read']='',favorite:bool=False,subscribed:bool=False,limit:int=Query(50,ge=1,le=100),offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).list,q,source,domain,state,favorite,subscribed,limit,offset)
@router.get('/items/{rid}')
async def detail(rid:UUID,request:Request):return await asyncio.to_thread(service(request).detail,str(rid))
@router.put('/items/{rid}')
async def item(rid:UUID,p:ItemWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).item_save,str(rid),p,str(idempotency_key))
@router.get('/subscriptions')
async def subscriptions(request:Request):return await asyncio.to_thread(service(request).subscriptions)
@router.post('/subscriptions')
async def subscription(p:SubscriptionWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).subscription_save,p,str(idempotency_key))
@router.put('/subscriptions/{rid}')
async def subscription_edit(rid:UUID,p:SubscriptionWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).subscription_save,p,str(idempotency_key),str(rid))
@router.get('/runs')
async def runs(request:Request):return await asyncio.to_thread(service(request).runs)
@router.get('/weekly')
async def weekly(request:Request):return await asyncio.to_thread(service(request).weekly)


@router.get('/schedule')
async def schedule(request:Request):return await asyncio.to_thread(service(request).schedule)


@router.put('/schedule')
async def schedule_save(p:ScheduleWrite,request:Request,idempotency_key:UUID=Header()):return await asyncio.to_thread(service(request).schedule_save,p,str(idempotency_key))
