"""会话数据访问。

Repository 只负责查询与持久化，不包含 HTTP 概念（AGENT.md §9.1：
API Handler 中不堆放数据库逻辑）。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Conversation
from app.models.base import utc_now
from app.models.conversation import DEFAULT_WORKSPACE_ID


class ConversationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def create(
        self,
        title: str,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
    ) -> Conversation:
        conversation = Conversation(title=title, workspace_id=workspace_id)
        self._session.add(conversation)
        self._session.commit()
        return conversation

    def get(self, conversation_id: str) -> Conversation | None:
        return self._session.get(Conversation, conversation_id)

    def get_in_workspace(
        self, conversation_id: str, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> Conversation | None:
        """按工作空间隔离查询。

        M2 只有一个工作空间，但查询必须带 scope，避免 M6 接入多工作空间后
        出现越权读取（AGENT.md §8.2）。
        """
        stmt = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.workspace_id == workspace_id,
        )
        return self._session.scalars(stmt).one_or_none()

    def list(
        self,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Conversation]:
        stmt = (
            select(Conversation)
            .where(Conversation.workspace_id == workspace_id)
            .order_by(Conversation.updated_at.desc(), Conversation.id.desc())
            .limit(limit)
            .offset(offset)
        )
        return list(self._session.scalars(stmt))

    def count(self, workspace_id: str = DEFAULT_WORKSPACE_ID) -> int:
        stmt = (
            select(func.count())
            .select_from(Conversation)
            .where(Conversation.workspace_id == workspace_id)
        )
        return int(self._session.scalar(stmt) or 0)

    def rename(self, conversation: Conversation, title: str) -> Conversation:
        conversation.title = title
        self._session.commit()
        return conversation

    def touch(self, conversation: Conversation) -> Conversation:
        """刷新会话的 updated_at。

        用于"追加消息后会话有新活动"的场景。显式赋值而不是依赖 ORM 的
        onupdate：本操作只改其他表，会话对象本身不会被标记为脏。
        """
        conversation.updated_at = utc_now()
        self._session.commit()
        return conversation

    def delete(self, conversation: Conversation) -> None:
        """删除会话。消息由数据库 ON DELETE CASCADE 一并删除。

        数据库连接已开启 PRAGMA foreign_keys=ON，级联真实生效。
        """
        self._session.delete(conversation)
        self._session.commit()
