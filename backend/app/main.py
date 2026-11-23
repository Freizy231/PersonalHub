"""FastAPI 应用装配。

工厂模式 create_app() 的好处：
- 测试可以创建独立实例，不共享全局状态。
- 测试可注入临时数据目录，绝不触碰真实用户数据。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import chat, conversations, health, messages
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware
from app.storage.database import build_engine, build_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()

    engine = build_engine(settings.db_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # 启动时不做任何建表：Schema 只由 Alembic 迁移建立（AGENT.md §9.4）。
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="0.0.1",
        # 文档只挂在带版本前缀的路径下，且仅在回环地址可达的环境中提供。
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = build_session_factory(engine)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,  # 无跨源 Cookie；开启前必须先有 CSRF 方案
        # 只放行实际使用的方法，不用通配符扩大攻击面。
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)

    install_exception_handlers(app)

    app.include_router(health.router, prefix=settings.api_prefix)
    app.include_router(conversations.router, prefix=settings.api_prefix)
    app.include_router(messages.router, prefix=settings.api_prefix)
    app.include_router(chat.router, prefix=settings.api_prefix)

    return app


# 刻意不在模块级实例化应用：create_app() 会打开数据库并创建数据目录，
# 若在导入时执行，任何 `import app.main`（包括测试收集）都会触碰真实用户目录。
# 启动时使用：uvicorn app.main:create_app --factory
