import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from ...core.security import require_session
from .service import TagService
from .schemas import TagAssignment, TagRename

router = APIRouter(prefix='/api/v1', dependencies=[Depends(require_session)])


def service(request): return TagService(request.app.state.settings.database)


@router.get('/tags')
async def listing(request: Request, q: str = Query('', max_length=256), limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
    return await asyncio.to_thread(service(request).list, q, limit, offset)


@router.get('/resources/{rid}/tags')
async def get(rid: UUID, request: Request):
    return await asyncio.to_thread(service(request).get, str(rid))


@router.put('/resources/{rid}/tags')
async def assign(rid: UUID, payload: TagAssignment, request: Request, idempotency_key: UUID = Header()):
    return await asyncio.to_thread(service(request).assign, str(rid), payload, str(idempotency_key))


@router.put('/tags/{tid}')
async def rename(tid: UUID, payload: TagRename, request: Request, idempotency_key: UUID = Header()):
    return await asyncio.to_thread(service(request).rename, str(tid), payload, str(idempotency_key))
