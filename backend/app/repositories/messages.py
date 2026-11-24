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

    def list_latest(self, conversation_id: str, limit: int = 100, offset: int = 0) -> list[Message]:
        """返回**最近**的若干条，但仍按时间正序排列。

        为什么需要它（实测缺陷）：`list_for_conversation` 先取最早的 N 条，
        长会话里模型因此永远看不到最近的对话，表现为"AI 突然失忆"。
        取尾部必须倒序 LIMIT 再反转，不能只改 offset。

        offset 从最新一条往回数（0 = 最后 limit 条）。
        """
        stmt = (
            select(Message)
            .where(Message.conversation_id == conversation_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
            .offset(offset)
        )
        newest_first = list(self._session.scalars(stmt))
        newest_first.reverse()
        return newest_first

    def tool_invocations_for(self, conversation_id: str) -> dict[str, list[dict[str, object]]]:
        """按 message_id 分组返回工具调用记录（§7.4 可理解摘要）。

        只返回元数据，不含参数与结果正文。
        """
        from app.models.tool_invocation import ToolInvocation

        stmt = (
            select(ToolInvocation)
            .where(ToolInvocation.conversation_id == conversation_id)
            .order_by(ToolInvocation.created_at.asc())
        )
        grouped: dict[str, list[dict[str, object]]] = {}
        for row in self._session.scalars(stmt):
            if not row.message_id:
                continue
            grouped.setdefault(row.message_id, []).append(
                {
                    "tool": row.tool_name,
                    "status": row.status,
                    "error_code": row.error_code,
                }
            )
        return grouped

    def count_for_conversation(self, conversation_id: str) -> int:
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(Message.conversation_id == conversation_id)
        )
        return int(self._session.scalar(stmt) or 0)
