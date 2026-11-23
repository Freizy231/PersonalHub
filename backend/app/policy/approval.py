"""Approval Manager（AGENT.md §4.11）。

管理"暂停 → 用户决定 → 凭票执行"的完整生命周期。

安全实现要点（M6 验收逐条对应）：
1. 批准绑定工具名 + 参数指纹 + 主体 + 工作空间。
   执行前重新计算指纹，不一致即作废 —— 批准 A 却执行 B 不可能。
2. 批准带**过期时间**。过期后即使状态是 APPROVED 也不可用。
3. 批准**一次性消费**。执行后立即标记已用，重放同一审批会被拒绝。
4. 过期惰性处理：读取时发现已过期即标记 EXPIRED，不依赖后台定时任务。

本模块只做"票据管理"，不执行工具。执行仍由 Tool Broker 完成。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.approval import Approval, ApprovalStatus
from app.policy import arguments_fingerprint

logger = logging.getLogger(__name__)

# 审批有效期。太长会让"很久以前批准过的"一直有效，太短会让用户来不及处理。
DEFAULT_TTL = timedelta(minutes=10)

# 参数摘要的长度上限：足够用户辨认，又不至于把整段内容塞进界面。
MAX_PREVIEW = 400


class ApprovalError(RuntimeError):
    """审批相关的业务错误。message 面向用户，必须安全。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ApprovalTicket:
    """执行所需的凭据。只有持有它的调用方才能执行对应动作。"""

    approval_id: str
    tool_name: str
    fingerprint: str
    context_token: str  # 占位：M6 用 approval_id 作为短期执行上下文标识


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime) -> datetime:
    """SQLite 不保存时区，读回的是 naive datetime。统一按 UTC 解释。"""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def build_preview(tool_name: str, arguments: dict[str, Any]) -> str:
    """生成给用户看的参数摘要。

    只展示参数内容本身（用户需要看清"要做什么"），
    不写入日志（§7.4：日志不记录完整敏感内容）。
    """
    try:
        text = json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = "<无法序列化的参数>"
    preview = f"{tool_name} {text}"
    if len(preview) > MAX_PREVIEW:
        preview = preview[:MAX_PREVIEW] + "…"
    return preview


def create_pending(
    session: Session,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    principal_id: str,
    workspace_id: str,
    conversation_id: str | None,
    ttl: timedelta = DEFAULT_TTL,
) -> Approval:
    """创建待审批记录，状态 PENDING。"""
    approval = Approval(
        tool_name=tool_name,
        arguments_fingerprint=arguments_fingerprint(tool_name, arguments),
        arguments_preview=build_preview(tool_name, arguments),
        status=ApprovalStatus.PENDING.value,
        principal_id=principal_id,
        workspace_id=workspace_id,
        conversation_id=conversation_id,
        expires_at=_now() + ttl,
    )
    session.add(approval)
    session.commit()
    return approval


def decide(
    session: Session,
    approval_id: str,
    *,
    approved: bool,
    principal_id: str,
    source: str = "interactive",
) -> Approval:
    """用户做出决定。只允许从 PENDING 转移，杜绝重复决定。"""
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise ApprovalError("审批不存在。", code="APPROVAL_NOT_FOUND")

    if approval.principal_id != principal_id:
        # 不区分"不存在"与"不是你的"，避免探测他人审批。
        raise ApprovalError("审批不存在。", code="APPROVAL_NOT_FOUND")

    if _as_utc(approval.expires_at) <= _now():
        approval.status = ApprovalStatus.EXPIRED.value
        session.commit()
        raise ApprovalError("审批已过期，请重新发起。", code="APPROVAL_EXPIRED")

    if approval.status != ApprovalStatus.PENDING.value:
        raise ApprovalError("该审批已被处理。", code="APPROVAL_ALREADY_DECIDED")

    approval.status = ApprovalStatus.APPROVED.value if approved else ApprovalStatus.DENIED.value
    approval.decided_at = _now()
    approval.decision_source = source
    session.commit()
    return approval


def consume_ticket(
    session: Session,
    approval_id: str,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    principal_id: str,
    workspace_id: str,
) -> ApprovalTicket:
    """校验并消费一次审批，返回可用于执行的票据。

    这是"批准 A 却执行 B"的最后一道防线：把当前请求与审批记录逐项比对。
    任何一项不符都拒绝，且**不消费**审批（用户可能还需要它处理真正的请求）。
    """
    approval = session.get(Approval, approval_id)
    if approval is None:
        raise ApprovalError("审批不存在。", code="APPROVAL_NOT_FOUND")

    if approval.principal_id != principal_id or approval.workspace_id != workspace_id:
        raise ApprovalError("审批与当前主体或工作空间不符。", code="APPROVAL_MISMATCH")

    if _as_utc(approval.expires_at) <= _now():
        approval.status = ApprovalStatus.EXPIRED.value
        session.commit()
        raise ApprovalError("审批已过期，请重新发起。", code="APPROVAL_EXPIRED")

    if approval.status != ApprovalStatus.APPROVED.value:
        raise ApprovalError("审批尚未通过。", code="APPROVAL_NOT_APPROVED")

    if approval.consumed:
        # 一次性使用：防止同一审批被重放执行多次。
        raise ApprovalError("该审批已被使用。", code="APPROVAL_ALREADY_CONSUMED")

    if approval.tool_name != tool_name:
        raise ApprovalError("审批对应的工具不一致。", code="APPROVAL_MISMATCH")

    expected = arguments_fingerprint(tool_name, arguments)
    if approval.arguments_fingerprint != expected:
        # 关键：参数变了，原批准无效（§M6 验收要求）。
        raise ApprovalError("审批对应的参数不一致，请重新确认。", code="APPROVAL_MISMATCH")

    approval.consumed = 1
    session.commit()
    return ApprovalTicket(
        approval_id=approval.id,
        tool_name=approval.tool_name,
        fingerprint=approval.arguments_fingerprint,
        context_token=approval.id,
    )


def expire_stale(session: Session) -> int:
    """把已过期的 PENDING 标记为 EXPIRED。返回处理条数。

    在读取审批列表时顺带调用，避免依赖定时任务。
    """
    stmt = select(Approval).where(
        Approval.status == ApprovalStatus.PENDING.value,
        Approval.expires_at <= _now(),
    )
    stale = list(session.scalars(stmt))
    for approval in stale:
        approval.status = ApprovalStatus.EXPIRED.value
    if stale:
        session.commit()
    return len(stale)


def get_for_principal(session: Session, approval_id: str, principal_id: str) -> Approval | None:
    approval = session.get(Approval, approval_id)
    if approval is None or approval.principal_id != principal_id:
        return None
    return approval


def list_for_principal(
    session: Session,
    principal_id: str,
    *,
    status: ApprovalStatus | None = None,
    limit: int = 50,
) -> list[Approval]:
    """列出某主体的审批，按创建时间倒序。"""
    stmt = select(Approval).where(Approval.principal_id == principal_id)
    if status is not None:
        stmt = stmt.where(Approval.status == status.value)
    stmt = stmt.order_by(Approval.created_at.desc()).limit(limit)
    return list(session.scalars(stmt))
