import asyncio
from datetime import date
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse

from ....core.security import require_session
from .schemas import TaskCreate, TaskPatch, TaskVersion
from .service import TaskService

router = APIRouter(prefix='/api/v1/tasks', dependencies=[Depends(require_session)])


def service(request: Request):
    return TaskService(request.app.state.settings.database)


@router.get('')
async def list_tasks(request: Request, scope: Literal['all','today','upcoming','overdue','completed','trash'] = 'all',
                     on_date: date | None = None, tag_id: UUID | None = None, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
    return await asyncio.to_thread(service(request).list, scope, on_date.isoformat() if on_date else None, limit, offset, str(tag_id) if tag_id else None)


@router.get('/export')
async def export_tasks(request: Request):
    result = await asyncio.to_thread(service(request).export)
    return JSONResponse(result, headers={'Content-Disposition': 'attachment; filename="personal-hub-tasks.json"'})


@router.get('/{resource_id}')
async def get_task(resource_id: UUID, request: Request):
    return await asyncio.to_thread(service(request).get, str(resource_id))


@router.post('')
async def create_task(payload: TaskCreate, request: Request, idempotency_key: UUID = Header()):
    result, replay = await asyncio.to_thread(service(request).create, payload, str(idempotency_key))
    return JSONResponse(result, status_code=200 if replay else 201)


@router.patch('/{resource_id}')
async def change_task(resource_id: UUID, payload: TaskPatch, request: Request):
    return await asyncio.to_thread(service(request).change, str(resource_id), payload)


@router.post('/{resource_id}/trash')
async def trash_task(resource_id: UUID, payload: TaskVersion, request: Request):
    return await asyncio.to_thread(service(request).trash, str(resource_id), payload.expected_version)


@router.post('/{resource_id}/restore')
async def restore_task(resource_id: UUID, payload: TaskVersion, request: Request):
    return await asyncio.to_thread(service(request).trash, str(resource_id), payload.expected_version, restore=True)
