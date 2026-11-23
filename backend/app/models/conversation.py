"""会话模型。

一个 conversation 代表一段连续对话。消息级联删除：删除会话时其消息一并删除，
避免留下孤儿记录（AGENT.md §5.2）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin

if TYPE_CHECKING:
    from app.models.message import Message

# 标题长度上限。用户输入必须在 Schema 与数据库两侧同时受限。
MAX_TITLE_LENGTH = 200

# M2 阶段固定工作空间标识。workspaces 表与多工作空间治理在 M6 建立，
# 这里先保证每条数据都带明确的 scope，不留下无归属数据。
DEFAULT_WORKSPACE_ID = "default"


class Conversation(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "conversations"

    title: Mapped[str] = mapped_column(String(MAX_TITLE_LENGTH), nullable=False)
    workspace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_WORKSPACE_ID
    )
    # 主体归属：MVP 只有本机用户，后续接入 Principal 后改为外键。
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="Message.created_at",
    )

    __table_args__ = (
        # 列表按工作空间筛选并按更新时间倒序，建立复合索引。
        Index("ix_conversations_workspace_updated", "workspace_id", "updated_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<Conversation id={self.id!r} title={self.title!r}>"
