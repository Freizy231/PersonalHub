import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from ....core.security import require_session
from ..tasks.schemas import TaskVersion
from .schemas import ReminderCreate
from .service import ReminderService

router=APIRouter(prefix='/api/v1/reminders',dependencies=[Depends(require_session)])
def service(request):return ReminderService(request.app.state.settings.database)

@router.get('')
async def listing(request: Request,scope: Literal['inbox','pending','history']='inbox',resource_id: UUID|None=None,
    limit: int=Query(50,ge=1,le=100),offset: int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).list,scope,str(resource_id) if resource_id else None,limit,offset)

@router.get('/export')
async def export(request: Request):
    result=await asyncio.to_thread(service(request).export)
    return JSONResponse(result,headers={'Content-Disposition':'attachment; filename="personal-hub-reminders.json"'})

@router.post('')
async def create(payload: ReminderCreate,request: Request,idempotency_key: UUID=Header()):
    result,replayed=await asyncio.to_thread(service(request).create,payload,str(idempotency_key))
    return JSONResponse(result,status_code=200 if replayed else 201)

@router.post('/{rid}/cancel')
async def cancel(rid: UUID,payload: TaskVersion,request: Request):
    return await asyncio.to_thread(service(request).change,str(rid),payload.expected_version,'cancel')

@router.post('/{rid}/ack')
async def ack(rid: UUID,payload: TaskVersion,request: Request):
    return await asyncio.to_thread(service(request).change,str(rid),payload.expected_version,'ack')
