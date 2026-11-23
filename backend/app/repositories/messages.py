"""消息数据访问。

所有方法都先由调用方确认会话属于当前工作空间（见 api/messages.py），
这里不重复做权限判断，但也不提供"按 id 取任意消息"的入口，
避免绕过会话归属检查。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Message


class MessageRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def append(self, conversation_id: str, content: str, role: str = "user") -> Message:
        # role 由服务端决定；调用方传入的只能是受控值。
        message = Message(conversation_id=conversation_id, role=role, content=content)
        self._session.add(message)
        self._session.commit()
        return message

    def list_for_conversation(
        self, conversation_id: str, limit: int = 100, offset: int = 0
    ) -> list[Message]:
        """按时间正序返回：对话必须按发生顺序阅读。"""
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.asc(), Message.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt))

    def count_for_conversation(self, conversation_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return int(self._session.scalar(stmt) or 0)
