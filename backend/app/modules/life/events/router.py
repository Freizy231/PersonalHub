import asyncio
from datetime import date
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from ....core.security import require_session
from ..tasks.schemas import TaskVersion
from .schemas import EventCreate, EventEdit
from .service import EventService

router = APIRouter(prefix='/api/v1/events',dependencies=[Depends(require_session)])
def service(request): return EventService(request.app.state.settings.database)

@router.get('')
async def listing(request: Request,start_date: date,end_date: date,timezone: str=Query('Asia/Shanghai',max_length=100),
    scope: Literal['calendar','trash']='calendar',limit: int=Query(100,ge=1,le=100),offset: int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).list,start_date.isoformat(),end_date.isoformat(),timezone,scope,limit,offset)

@router.get('/export')
async def export(request: Request):
    result = await asyncio.to_thread(service(request).export)
    return JSONResponse(result,headers={'Content-Disposition':'attachment; filename="personal-hub-events.json"'})

@router.get('/{rid}')
async def get(rid: UUID,request: Request): return await asyncio.to_thread(service(request).get,str(rid))

@router.post('')
async def create(payload: EventCreate,request: Request,idempotency_key: UUID=Header()):
    result,replayed = await asyncio.to_thread(service(request).create,payload,str(idempotency_key))
    return JSONResponse(result,status_code=200 if replayed else 201)

@router.put('/{rid}')
async def edit(rid: UUID,payload: EventEdit,request: Request): return await asyncio.to_thread(service(request).change,str(rid),payload)

@router.post('/{rid}/trash')
async def trash(rid: UUID,payload: TaskVersion,request: Request):
    return await asyncio.to_thread(service(request).trash,str(rid),payload.expected_version)

@router.post('/{rid}/restore')
async def restore(rid: UUID,payload: TaskVersion,request: Request):
    return await asyncio.to_thread(service(request).trash,str(rid),payload.expected_version,True)
