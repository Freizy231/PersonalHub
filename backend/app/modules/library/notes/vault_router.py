"""Dedicated private Note scope. Never routed through ordinary SQLite Notes."""
import asyncio
from typing import Literal
from uuid import UUID
from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator, field_validator
from ....core.security import require_session
from ...life.tasks.schemas import TaskVersion
from .schemas import NoteCreate

from ....core.database import revision, SCHEMA_REVISION, HubError

async def require_hub_ready(request: Request):
    current = await asyncio.to_thread(revision, request.app.state.settings.database)
    if current != SCHEMA_REVISION: raise HubError('DATABASE_UPGRADE_REQUIRED', 503)

router = APIRouter(prefix='/api/v1/vault', dependencies=[Depends(require_session), Depends(require_hub_ready)])


class Password(BaseModel):
    model_config = ConfigDict(extra='forbid')
    password: str = Field(min_length=12, max_length=256)

    @field_validator('password', 'confirmation', 'old_password', check_fields=False)
    @classmethod
    def valid_utf8(cls, value):
        value.encode('utf-8')
        return value


class Setup(Password):
    confirmation: str = Field(min_length=12, max_length=256)
    acknowledge_no_recovery: Literal[True]

    @model_validator(mode='after')
    def matches(self):
        if self.password != self.confirmation: raise ValueError('password mismatch')
        return self


class ChangePassword(Setup):
    old_password: str = Field(min_length=12, max_length=256)


class PrivateNote(NoteCreate):
    note_kind: Literal['private_journal', 'treehole'] = 'private_journal'


class PrivateEdit(PrivateNote):
    expected_version: int = Field(ge=1, strict=True)


def context(request):
    session = request.app.state.auth.require(request)
    return request.app.state.vault, session


@router.get('/status')
async def status(request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.status, session.csrf_token)


@router.post('/setup')
async def setup(payload: Setup, request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.open, session.csrf_token, payload.password, create=True, session_deadline=session.expires_at)


@router.post('/unlock')
async def unlock(payload: Password, request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.open, session.csrf_token, payload.password, session_deadline=session.expires_at)


@router.post('/lock')
async def lock(request: Request):
    store, session = context(request)
    await asyncio.to_thread(store.lock, session.csrf_token)
    return {'status': 'locked'}


@router.post('/touch')
async def touch(request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.touch, session.csrf_token)


@router.post('/password')
async def password(payload: ChangePassword, request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.change_password, session.csrf_token, payload.old_password, payload.password)


@router.get('/notes')
async def listing(request: Request, trash: bool = False, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0, le=1000000)):
    store, session = context(request)
    return await asyncio.to_thread(store.list, session.csrf_token, trash, limit, offset)


@router.get('/notes/{rid}')
async def get(rid: UUID, request: Request):
    store, session = context(request)
    return await asyncio.to_thread(store.get, session.csrf_token, str(rid))


async def write(request, payload, key, rid=None, action='save'):
    store, session = context(request)
    return await asyncio.to_thread(store.save, session.csrf_token, payload.model_dump(mode='json'), str(key), str(rid) if rid else None, action)


@router.post('/notes')
async def create(payload: PrivateNote, request: Request, idempotency_key: UUID = Header()):
    return await write(request, payload, idempotency_key)


@router.put('/notes/{rid}')
async def edit(rid: UUID, payload: PrivateEdit, request: Request, idempotency_key: UUID = Header()):
    return await write(request, payload, idempotency_key, rid)


@router.post('/notes/{rid}/{action}')
async def trash(rid: UUID, action: Literal['trash', 'restore'], payload: TaskVersion, request: Request, idempotency_key: UUID = Header()):
    return await write(request, payload, idempotency_key, rid, action)
