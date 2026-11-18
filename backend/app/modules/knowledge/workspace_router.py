"""AI workspace routes: capability registry, conversations, plans, runs, grants, templates.

All of these sit behind the ordinary local session. A route that only reads state is a GET;
nothing here starts a run as a side effect of being viewed.
"""
import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse

from ...core.capabilities import CapabilityError
from ...core.database import HubError
from ...core.security import require_session
from .executor import Executor
from .workspace import WorkspaceService
from .workspace_schemas import (AdoptWrite, ConversationStart, GrantWrite, MessageWrite,
                                PlanDraft, PreferencesWrite, ReviewRequest, RunTemplate,
                                ScheduleRequest, TemplateToggle, TemplateWrite)

router = APIRouter(prefix='/api/v1/ai', dependencies=[Depends(require_session)])


def service(r: Request) -> WorkspaceService:
    return WorkspaceService(r.app.state.settings)


def executor(r: Request) -> Executor:
    return Executor(r.app.state.settings)


def _refusal(error: CapabilityError):
    # A refused capability is an ordinary client error with a stable code, not a 500.
    return JSONResponse({'code': error.code, 'message': error.code, 'details': {}}, status_code=error.status)


@router.get('/capabilities')
async def capabilities(request: Request):
    return {'items': list(await asyncio.to_thread(executor(request).specs).values())}


@router.get('/conversations/{cid}')
async def conversation(cid: UUID, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(service(request).conversation, str(cid), session.csrf_token)


@router.post('/conversations')
async def start(payload: ConversationStart, request: Request, session=Depends(require_session)):
    scope = str(payload.scope_id) if payload.scope_id else None
    return await asyncio.to_thread(service(request).start_conversation, scope, payload.title,
                                   session.csrf_token)


@router.post('/conversations/{cid}/messages')
async def message(cid: UUID, payload: MessageWrite, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(service(request).append_message, str(cid), 'user', payload.body,
                                   payload.source_ids)


@router.post('/conversations/{cid}/plan')
async def draft_plan(cid: UUID, payload: PlanDraft, request: Request, session=Depends(require_session)):
    """Ask the model for a candidate plan and store it as a draft.

    The model reply is validated by `planner`; an unparseable or out-of-scope reply is refused
    here and no plan row is written.
    """
    from .planner import PLAN_INSTRUCTIONS

    workspace = service(request)
    scope = str(payload.scope_id) if payload.scope_id else None
    # Drafting a plan writes no business data, so it does not need the local-write grant.
    # The grant is checked again when the plan is adopted, which is the moment it matters.
    gateway = request.app.state.model_gateway
    if not gateway.available():
        raise HubError('AI_DISABLED', 409)
    signature = await asyncio.to_thread(gateway.signature)
    if not workspace.outbound_allowed(signature['endpoint'], signature['model'], signature['version']):
        raise HubError('AI_OUTBOUND_NOT_GRANTED', 403)
    sources = await asyncio.to_thread(workspace.sources_for, scope, payload.intent)
    try:
        reply = await gateway.complete([
            {'role': 'system', 'content': PLAN_INSTRUCTIONS},
            {'role': 'user', 'content': _prompt(payload.question, payload.intent, sources)},
        ])
        await asyncio.to_thread(workspace.append_message, str(cid), 'user', payload.question,
                                [s['id'] for s in sources], 'user', sources)
        plan = await asyncio.to_thread(workspace.draft_plan, reply, str(cid), scope, signature['model'])
        await asyncio.to_thread(workspace.append_message, str(cid), 'assistant', reply, (), 'model')
    except CapabilityError as error:
        return _refusal(error)
    return plan


def _prompt(question, intent, sources):
    lines = ['用户的问题：', question]
    if intent:
        lines += ['', '用户的补充说明：', intent]
    lines += ['', '授权资料（只能引用这些）：']
    if not sources:
        lines.append('（没有可用的授权资料；不要编造资料内容，只按通用学习常识起草。）')
    for index, source in enumerate(sources, 1):
        lines.append(f'[{index}] {source["kind"]} · {source["title"]}')
        lines.append(source['text'][:3000])
    return '\n'.join(lines)


@router.post('/conversations/{cid}/clear')
async def clear(cid: UUID, request: Request):
    return await asyncio.to_thread(service(request).clear_conversation, str(cid))


@router.get('/plans/{pid}')
async def plan(pid: UUID, request: Request):
    return await asyncio.to_thread(service(request).get_plan, str(pid))


@router.post('/plans/{pid}/adopt')
async def adopt(pid: UUID, payload: AdoptWrite, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(service(request).adopt, str(pid), session.csrf_token)


@router.get('/runs/{rid}')
async def run(rid: UUID, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(executor(request).status, str(rid), session.csrf_token)


@router.post('/runs/{rid}/cancel')
async def cancel(rid: UUID, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(executor(request).cancel, str(rid), session.csrf_token)


@router.get('/grants')
async def grants(request: Request):
    return {'items': await asyncio.to_thread(service(request).grants)}


@router.put('/grants')
async def set_grant(payload: GrantWrite, request: Request, session=Depends(require_session)):
    workspace = service(request)
    from .gateway import ModelGateway
    gateway = ModelGateway(request.app.state.assistant)
    signature = await asyncio.to_thread(gateway.signature)
    endpoint = payload.endpoint or signature['endpoint']
    model = payload.model or signature['model']
    return await asyncio.to_thread(workspace.set_grant, payload.kind,
                                   str(payload.scope_id) if payload.scope_id else None,
                                   payload.enabled, payload.days, endpoint, model, signature['version'])


@router.get('/preferences')
async def preferences(request: Request):
    return await asyncio.to_thread(service(request).preferences)


@router.put('/preferences')
async def save_preferences(payload: PreferencesWrite, request: Request):
    return await asyncio.to_thread(service(request).save_preferences, payload)


@router.get('/templates')
async def templates(request: Request):
    return {'items': await asyncio.to_thread(service(request).templates)}


@router.post('/templates')
async def create_template(payload: TemplateWrite, request: Request):
    return await asyncio.to_thread(service(request).save_template, payload)


@router.put('/templates/{tid}')
async def update_template(tid: UUID, payload: TemplateWrite, request: Request):
    return await asyncio.to_thread(service(request).save_template, payload, str(tid))


@router.post('/templates/{tid}/state')
async def toggle_template(tid: UUID, payload: TemplateToggle, request: Request):
    return await asyncio.to_thread(service(request).set_template_enabled, str(tid), payload.enabled)


@router.post('/templates/{tid}/run')
async def run_template(tid: UUID, payload: RunTemplate, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(service(request).run_template, str(tid), session.csrf_token,
                                   str(payload.scope_id))


@router.post('/review')
async def review(payload: ReviewRequest, request: Request, session=Depends(require_session)):
    return await asyncio.to_thread(service(request).review, str(payload.scope_id), session.csrf_token)


@router.post('/schedule')
async def schedule(payload: ScheduleRequest, request: Request):
    return await asyncio.to_thread(service(request).schedule, str(payload.scope_id), payload.days)
