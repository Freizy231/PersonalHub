"""审批接口（AGENT.md §7.1）。

    GET  /api/v1/approvals                    列出审批（默认只看待处理）
    GET  /api/v1/approvals/{id}               获取单条
    POST /api/v1/approvals/{id}/decide        批准或拒绝

安全：
- 所有操作限定当前主体，越权一律 404。
- 决策只能从 PENDING 发生（Approval Manager 保证）。
- 决策成功后唤醒对话中等待的协程；超时后才到的决策会被忽略。
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.api.deps import SessionDep
from app.models.approval import Approval, ApprovalStatus
from app.policy import approval as approval_manager
from app.policy.waiter import get_waiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/approvals", tags=["approvals"])

PRINCIPAL_ID = "local-user"

# 等待用户决定的时长。与 Approval Manager 的 TTL 一致，
# 避免"审批还没过期，但对话已经不等了"的困惑。
WAIT_TIMEOUT_SECONDS = approval_manager.DEFAULT_TTL.total_seconds()


class ApprovalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    tool_name: str
    arguments_preview: str
    status: str
    conversation_id: str | None
    expires_at: str
    decided_at: str | None
    consumed: int


class ApprovalListOut(BaseModel):
    items: list[ApprovalOut]
    total: int


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "reject"]
    # 勾选"本次会话内不再询问"
    remember_for_session: bool = False
    # 由前端提供，用于绑定会话级授权（刷新页面即换新值，从而失效）
    session_token: str | None = None


def _to_out(approval: Approval) -> ApprovalOut:
    expires = approval.expires_at
    expires_str = expires.isoformat() if expires.tzinfo else expires.isoformat() + "Z"
    decided = approval.decided_at
    decided_str = None
    if decided is not None:
        decided_str = decided.isoformat() if decided.tzinfo else decided.isoformat() + "Z"
    return ApprovalOut(
        id=approval.id,
        tool_name=approval.tool_name,
        arguments_preview=approval.arguments_preview,
        status=approval.status,
        conversation_id=approval.conversation_id,
        expires_at=expires_str,
        decided_at=decided_str,
        consumed=approval.consumed,
    )


@router.get("", response_model=ApprovalListOut, summary="列出审批")
async def list_approvals(
    session: SessionDep,
    status_filter: Annotated[ApprovalStatus | None, Query(alias="status")] = None,
) -> ApprovalListOut:
    # 顺手把过期的 PENDING 标记为 EXPIRED，不依赖后台定时任务。
    approval_manager.expire_stale(session)

    items = approval_manager.list_for_principal(session, PRINCIPAL_ID, status=status_filter)
    return ApprovalListOut(items=[_to_out(a) for a in items], total=len(items))


@router.get("/{approval_id}", response_model=ApprovalOut, summary="获取审批")
async def get_approval(approval_id: str, session: SessionDep) -> ApprovalOut:
    approval = approval_manager.get_for_principal(session, approval_id, PRINCIPAL_ID)
    if approval is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return _to_out(approval)


@router.post("/{approval_id}/decide", response_model=ApprovalOut, summary="做出决定")
async def decide_approval(
    approval_id: str,
    payload: DecisionIn,
    request_session: SessionDep,
    request: Request,
) -> ApprovalOut:
    approved = payload.decision == "approve"

    try:
        approval = approval_manager.decide(
            request_session,
            approval_id,
            approved=approved,
            principal_id=PRINCIPAL_ID,
            source="session_grant" if payload.remember_for_session else "interactive",
        )
    except approval_manager.ApprovalError as exc:
        code = exc.code
        http_status = (
            status.HTTP_404_NOT_FOUND if code == "APPROVAL_NOT_FOUND" else status.HTTP_409_CONFLICT
        )
        raise HTTPException(status_code=http_status) from exc

    # 唤醒对话中等待的协程。若无人在等（超时后才到），忽略即可。
    waiter = get_waiter()
    delivered = await waiter.resolve(approval_id, approved)

    if payload.remember_for_session and approved and payload.session_token:
        import time

        from app.policy.grants import SESSION_GRANT_TTL_SECONDS

        request.app.state.session_grants.grant(
            payload.session_token,
            approval.tool_name,
            time.time() + SESSION_GRANT_TTL_SECONDS,
        )

    if not delivered:
        # 不在错误响应里加入细节，只记日志。
        logging.getLogger(__name__).info(
            "审批决定晚于对话等待结束", extra={"approval_id": approval_id}
        )

    return _to_out(approval)
