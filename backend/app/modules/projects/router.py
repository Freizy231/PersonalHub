import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from ...core.security import require_session
from .service import ProjectService
from .schemas import (ProjectWrite, Version, MilestoneWrite, MilestoneVersion,
    TaskLink, ProjectTaskCreate, EntryLink, EntryCreate)

router = APIRouter(prefix='/api/v1/projects', dependencies=[Depends(require_session)])


def service(request):
    return ProjectService(request.app.state.settings.database)


@router.get('')
async def listing(request:Request, q:str=Query('',max_length=256), status:Literal['','planned','active','paused','completed','archived']='',
                  trash:bool=False, tag_id:UUID|None=None, limit:int=Query(30,ge=1,le=100), offset:int=Query(0,ge=0,le=1000000), recent:bool=False):
    return await asyncio.to_thread(service(request).list,q,status,trash,str(tag_id) if tag_id else None,limit,offset,recent)


@router.post('')
async def create(payload:ProjectWrite, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).save,payload,str(idempotency_key))


@router.get('/{pid}')
async def get(pid:UUID, request:Request):
    return await asyncio.to_thread(service(request).get,str(pid))


@router.put('/{pid}')
async def save(pid:UUID, payload:ProjectWrite, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).save,payload,str(idempotency_key),str(pid))


@router.post('/{pid}/opened')
async def opened(pid:UUID, request:Request):
    return await asyncio.to_thread(service(request).opened,str(pid))


@router.post('/{pid}/directory-check')
async def directory_check(pid:UUID, request:Request):
    return await asyncio.to_thread(service(request).directory_check,str(pid))


@router.post('/{pid}/lifecycle/{action}')
async def lifecycle(pid:UUID, action:Literal['trash','restore'], payload:Version, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).trash,str(pid),payload,str(idempotency_key),action=='restore')


@router.get('/{pid}/milestones')
async def milestones(pid:UUID, request:Request, trash:bool=False):
    return await asyncio.to_thread(service(request).milestones,str(pid),trash)


@router.post('/{pid}/milestones')
async def milestone_create(pid:UUID, payload:MilestoneWrite, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).milestone_save,str(pid),payload,str(idempotency_key))


@router.put('/{pid}/milestones/{mid}')
async def milestone_save(pid:UUID, mid:UUID, payload:MilestoneWrite, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).milestone_save,str(pid),payload,str(idempotency_key),str(mid))


@router.post('/{pid}/milestones/{mid}/{action}')
async def milestone_trash(pid:UUID, mid:UUID, action:Literal['trash','restore'], payload:MilestoneVersion, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).milestone_trash,str(pid),str(mid),payload,str(idempotency_key),action=='restore')


@router.get('/{pid}/tasks')
async def task_list(pid:UUID, request:Request, limit:int=Query(30,ge=1,le=100), offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).task_list,str(pid),limit,offset)


@router.post('/{pid}/tasks')
async def task_create(pid:UUID, payload:ProjectTaskCreate, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).task_create,str(pid),payload,str(idempotency_key))


@router.post('/{pid}/tasks/attach')
async def task_attach(pid:UUID, payload:TaskLink, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).task_attach,str(pid),payload,str(idempotency_key))


@router.get('/{pid}/entries')
async def entry_list(pid:UUID, request:Request, limit:int=Query(30,ge=1,le=100), offset:int=Query(0,ge=0,le=1000000)):
    return await asyncio.to_thread(service(request).entries,str(pid),limit,offset)


@router.post('/{pid}/entries')
async def entry_create(pid:UUID, payload:EntryCreate, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).entry_create,str(pid),payload,str(idempotency_key))


@router.post('/{pid}/entries/attach')
async def entry_attach(pid:UUID, payload:EntryLink, request:Request, idempotency_key:UUID=Header()):
    return await asyncio.to_thread(service(request).entry_attach,str(pid),payload,str(idempotency_key))


@router.post('/{pid}/links/{eid}/remove')
async def unlink(pid:UUID, eid:UUID, payload:Version, request:Request):
    return await asyncio.to_thread(service(request).unlink,str(pid),str(eid),payload.expected_version)
