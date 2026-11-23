"""工具调用记录模型（M5）。

为什么要入库（AGENT.md §7.4 "提供可理解的执行摘要"）：
工具调用只存在于一次 SSE 流中，用户刷新页面或稍后回看时就完全丢失了。
把"AI 调用了什么、结果如何"随助手消息一起保存，才能事后审计。

只记录**元数据**：工具名、状态、耗时、错误码。
不记录参数与返回值——它们可能包含用户数据，且正文已经在消息里。
"""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class ToolInvocation(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tool_invocations"

    conversation_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    # 关联到本次回合产生的助手消息，便于按消息展示。
    message_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("messages.id", ondelete="SET NULL"), nullable=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (Index("ix_tool_invocations_conversation", "conversation_id", "created_at"),)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<ToolInvocation {self.tool_name!r} {self.status!r}>"
