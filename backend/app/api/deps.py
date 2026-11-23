"""FastAPI 依赖装配。

把数据库引擎、Session 和 Repository 的创建集中在这里，
路由函数只声明自己需要什么，不关心如何构造（AGENT.md §9.1）。
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.repositories.conversations import ConversationRepository


def get_session(request: Request) -> Iterator[Session]:
    """每个请求一个 Session。

    引擎与 Session 工厂在 create_app() 中挂到 app.state，
    便于测试注入临时数据库而不触碰真实数据目录。
    """
    session_factory = request.app.state.session_factory
    session: Session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


def get_conversation_repository(session: SessionDep) -> ConversationRepository:
    return ConversationRepository(session)


ConversationRepoDep = Annotated[ConversationRepository, Depends(get_conversation_repository)]
