"""SQLAlchemy ORM 模型（AGENT.md §5.1）。

当前只建立 M2 需要的 conversations / messages。其余实体随里程碑逐步添加，
不在 M2 提前创建未使用的表。
"""

from __future__ import annotations

from app.models.base import Base
from app.models.conversation import Conversation
from app.models.message import Message

__all__ = ["Base", "Conversation", "Message"]
