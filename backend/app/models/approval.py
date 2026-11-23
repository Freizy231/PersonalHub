"""审批模型（AGENT.md §4.11、M6 验收）。

核心安全要求：**一次批准只对当时那一个精确动作有效**。

因此这条记录不只是"用户同意了 create_task"，而是"用户同意了
以参数指纹 X 调用 create_task"，并且带主体、工作空间与过期时间。
执行前会重新计算参数指纹，不一致即作废——批准 A 却执行 B 不可能发生。

状态：PENDING -> APPROVED / DENIED / EXPIRED / CANCELLED
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.conversation import DEFAULT_WORKSPACE_ID


class ApprovalStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"


class Approval(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "approvals"

    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    # 参数指纹：sha256(规范化 JSON)。执行时必须与请求参数重新计算的值一致。
    arguments_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # 展示给用户的参数摘要（**可能含用户数据，仅本地展示，不进日志**）。
    arguments_preview: Mapped[str] = mapped_column(Text, nullable=False, default="")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=ApprovalStatus.PENDING)

    # 主体与作用域：审批绑定到具体主体与工作空间（§8.4 授权判断维度）。
    principal_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")
    workspace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_WORKSPACE_ID
    )
    conversation_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 用户决策来源：interactive（对话内）或 session_grant（本次会话不再询问）
    decision_source: Mapped[str | None] = mapped_column(String(24), nullable=True)

    # 是否已被消费。审批是一次性的：执行后置为已用，不能重复使用。
    consumed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'APPROVED', 'DENIED', 'EXPIRED')",
            name="status_allowed",
        ),
        Index("ix_approvals_status_created", "status", "created_at"),
        Index("ix_approvals_conversation", "conversation_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Approval {self.tool_name!r} {self.status!r}>"
