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


def build_preview(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    definition=None,  # type: ignore[no-untyped-def]
) -> str:
    """生成给用户看的参数摘要。

    输出是**自然语言式的行文本**，不是 JSON（D-012）：
        标题：学习 Python
        说明：先掌握基础语法…

    为什么不再输出 `create_task {"title": "..."}`：审批卡片是给人看的，
    括号、引号和转义字符只会增加阅读负担；长描述被截断时还会切在 JSON
    中间，变成半截乱码。

    只展示参数内容本身（用户需要看清"要做什么"），
    不写入日志（§7.4：日志不记录完整敏感内容）。
    """
    if definition is not None:
        pairs = definition.summarize_arguments(arguments)
        title = definition.title
    else:
        # 没有工具定义时（例如直接调用本函数的测试/迁移脚本）退回可读的兜底：
        # 逐行 `键：值`，仍然不输出 JSON 结构。
        from app.capabilities import humanize_value

        pairs = [(key, humanize_value(value)) for key, value in arguments.items()]
        title = tool_name

    if not pairs:
        return f"{title}（无参数）"

    lines = [f"{label}：{value}" for label, value in pairs if value]
    text = "\n".join(lines) if lines else f"{title}（无参数）"
    if len(text) > MAX_PREVIEW:
        # 按行截断：宁可少显示最后一整行，也不切断句子。
        kept: list[str] = []
        used = 0
        for line in lines:
            if used + len(line) + 1 > MAX_PREVIEW - 1:
                break
            kept.append(line)
            used += len(line) + 1
        text = "\n".join(kept) + "\n…"
    return text


def create_pending(
    session: Session,
    *,
    tool_name: str,
    arguments: dict[str, Any],
    principal_id: str,
    workspace_id: str,
    conversation_id: str | None,
    ttl: timedelta = DEFAULT_TTL,
    definition=None,  # type: ignore[no-untyped-def]
) -> Approval:
    """创建待审批记录，状态 PENDING。

    definition 是工具定义；提供时会用它生成人类可读摘要与中文工具名。
    """
    approval = Approval(
        tool_name=tool_name,
        arguments_fingerprint=arguments_fingerprint(tool_name, arguments),
        arguments_preview=build_preview(tool_name, arguments, definition=definition),
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


def cancel_pending_by_ids(
    session: Session, approval_ids: list[str], *, source: str = "cancelled"
) -> int:
    """按 id 作废仍在等待的审批，返回处理条数。

    场景：用户在对话中点"停止生成"或直接关掉页面，SSE 连接断开。
    若不清理，这些审批会一直停在 PENDING，直到超时（10 分钟）才变成 EXPIRED，
    界面上表现为"待审批事项里躺着一个早已没人等的请求"。

    用 DENIED 而不是新增状态：审批被取消在语义上就是"未被批准、不会执行"。
    新增状态值需要数据库迁移，收益不足以支撑（见 D-012）。
    decision_source 记录真实原因（cancelled = 对话被中断），保留可追溯性。

    按 id 而非按会话：同一会话里可能有两轮生成同时在等审批，
    结束其中一轮不该把另一轮的请求也作废。
    """
    if not approval_ids:
        return 0
    stmt = select(Approval).where(
        Approval.id.in_(approval_ids),
        Approval.status == ApprovalStatus.PENDING.value,
    )
    return _cancel(session, list(session.scalars(stmt)), source)


def _cancel(session: Session, pending: list[Approval], source: str) -> int:
    for approval in pending:
        approval.status = ApprovalStatus.DENIED.value
        approval.decision_source = source
        approval.decided_at = _now()
    if pending:
        session.commit()
    return len(pending)


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
