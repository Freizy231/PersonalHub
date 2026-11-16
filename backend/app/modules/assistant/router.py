import asyncio
from uuid import UUID
from fastapi import APIRouter,Depends,Header,Request
from ...core.security import require_session
from .schemas import ConfigWrite,PreviewWrite,ConfirmWrite
router=APIRouter(prefix='/api/v1/assistant',dependencies=[Depends(require_session)])
def service(r):return r.app.state.assistant
@router.get('/config')
async def config(request:Request):return await asyncio.to_thread(service(request).config)
@router.put('/config')
async def configure(p:ConfigWrite,request:Request):return await asyncio.to_thread(service(request).configure,p)
@router.post('/preview')
async def preview(p:PreviewWrite,request:Request,session=Depends(require_session)):
    return await asyncio.to_thread(service(request).preview,p,session.csrf_token)
@router.post('/confirm')
async def confirm(p:ConfirmWrite,request:Request,idempotency_key:UUID=Header(),session=Depends(require_session)):
    return await service(request).confirm(p,str(idempotency_key),session.csrf_token)
@router.get('/runs/{rid}')
async def result(rid:UUID,request:Request,session=Depends(require_session)):return await service(request).result(str(rid),session.csrf_token)
@router.post('/runs/{rid}/cancel')
async def cancel(rid:UUID,request:Request,session=Depends(require_session)):return await service(request).cancel(str(rid),session.csrf_token)
