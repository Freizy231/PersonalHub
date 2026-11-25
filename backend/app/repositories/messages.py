"""消息数据访问。

所有方法都先由调用方确认会话属于当前工作空间（见 api/messages.py），
这里不重复做权限判断，但也不提供"按 id 取任意消息"的入口，
避免绕过会话归属检查。
"""

from __future__ import annotations

from sqlalchemy import func, select, tuple_
from sqlalchemy.orm import Session

from app.models import Message


def _before(anchor: Message):  # type: ignore[no-untyped-def]
    """ "排在 anchor 之前"的条件，用 (created_at, id) 行值比较。

    为什么不能只比 created_at：同一毫秒内的两条消息时间可能完全相同，
    只比时间会让游标卡在同一时刻反复取到同一条（或漏掉一条）。
    加上 id 作为次序键，与 ORDER BY 完全同构，翻页不会重不会漏。

    SQLite 支持行值比较 `(a, b) < (c, d)`，SQLAlchemy 的 tuple_ 会译成它。
    """
    return tuple_(Message.created_at, Message.id) < (anchor.created_at, anchor.id)


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

    def count_before(self, conversation_id: str, message_id: str) -> int:
        """某条消息**之前**（不含它自己）有多少条。

        与 `count_for_conversation` 配合判断"还有没有更早的"。
        按 (created_at, id) 严格小于来数，与 `list_before` 的排序键一致——
        两处只要有一处用了别的比较方式，边界就会出现"说有更多、点了却是空"。
        """
        anchor = self._session.get(Message, message_id)
        if anchor is None or anchor.conversation_id != conversation_id:
            return 0
        stmt = (
            select(func.count())
            .select_from(Message)
            .where(
                Message.conversation_id == conversation_id,
                _before(anchor),
            )
        )
        return int(self._session.scalar(stmt) or 0)

    def list_before(self, conversation_id: str, message_id: str, limit: int = 100) -> list[Message]:
        """取某条消息**之前**的最近 N 条，按时间正序返回。

        为什么用游标（`before`）而不是 offset：
        offset 从尾部往回数，一旦有新消息写入（用户发下一条、后台补存助手回复），
        同一个 offset 就指向了别的位置——用户点"加载更早"会看到重复或跳过。
        游标锚定在"我当前看到的最早一条"，新消息写多少都不影响它。

        `before` 不存在或不属于该会话时返回空列表（调用方先已校验会话归属）。
        """
        anchor = self._session.get(Message, message_id)
        if anchor is None or anchor.conversation_id != conversation_id:
            return []
        stmt = (
            select(Message)
            .where(
                Message.conversation_id == conversation_id,
                _before(anchor),
            )
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
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
