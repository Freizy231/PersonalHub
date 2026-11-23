"""消息模型。

角色限制为 user / assistant / system / tool 四类（AGENT.md 后续 Agent 阶段需要 tool）。
删除策略：随所属会话级联删除（数据库层 ON DELETE CASCADE）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.conversation import Conversation

VALID_ROLES = ("user", "assistant", "system", "tool")

MAX_CONTENT_LENGTH = 100_000


class Message(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "messages"

    conversation_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)

    conversation: Mapped[Conversation] = relationship(back_populates="messages")

    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant', 'system', 'tool')",
            name="role_allowed",
        ),
        Index("ix_messages_conversation_created", "conversation_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Message id={self.id!r} role={self.role!r}>"
