"""FastAPI 依赖装配。

把数据库引擎、Session 和 Repository 的创建集中在这里，
路由函数只声明自己需要什么，不关心如何构造（AGENT.md §9.1）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, status
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.errors import PublicMessageError
from app.repositories.conversations import ConversationRepository
from app.repositories.memory import MemoryRepository
from app.repositories.messages import MessageRepository


def get_session(request: Request) -> Iterator[Session]:
    """每个请求一个 Session。

    引擎与 Session 工厂在 create_app() 中挂到 app.state，
    便于测试注入临时数据库而不触碰真实数据目录。

    数据库未迁移时把底层 Sqlalchemy OperationalError 转成明确的 503，
    而不是让用户看到无意义的"服务器内部错误"。
    """
    session_factory = request.app.state.session_factory
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except OperationalError as exc:
        session.rollback()
        if "no such table" in str(exc.orig).lower():
            raise PublicMessageError(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                code="DATABASE_NOT_MIGRATED",
                message="数据库还没有初始化表结构。请先在后端目录执行：uv run alembic upgrade head",
            ) from exc
        raise
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_conversation_repository(session: SessionDep) -> ConversationRepository:
    return ConversationRepository(session)


def get_message_repository(session: SessionDep) -> MessageRepository:
    return MessageRepository(session)


ConversationRepoDep = Annotated[ConversationRepository, Depends(get_conversation_repository)]
MessageRepoDep = Annotated[MessageRepository, Depends(get_message_repository)]


def get_memory_repository(session: SessionDep) -> MemoryRepository:
    return MemoryRepository(session)


MemoryRepoDep = Annotated[MemoryRepository, Depends(get_memory_repository)]
