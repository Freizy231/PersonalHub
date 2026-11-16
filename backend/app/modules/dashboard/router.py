import asyncio
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Request
from ...core.security import require_session
from .service import DashboardService
from .schemas import PreferencesWrite

router = APIRouter(prefix='/api/v1/dashboard/preferences', dependencies=[Depends(require_session)])

@router.get('')
async def get(request: Request):
    return await asyncio.to_thread(DashboardService(request.app.state.settings.database).get)

@router.put('')
async def save(payload: PreferencesWrite, request: Request, idempotency_key: UUID = Header()):
    return await asyncio.to_thread(DashboardService(request.app.state.settings.database).save, payload, str(idempotency_key))
