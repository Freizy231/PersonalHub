"""FastAPI 应用装配。

工厂模式 create_app() 的好处：
- 测试可以创建独立实例，不共享全局状态。
- 后续按环境（开发/测试）注入不同配置时，只需要新增参数。

M1 只装配：CORS 白名单、request_id 中间件、统一错误处理、健康检查路由。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health
from app.core.config import Settings, get_settings
from app.core.errors import install_exception_handlers
from app.core.logging import configure_logging
from app.core.middleware import RequestIDMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    configure_logging()
    settings = settings or get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="0.0.1",
        # 文档只挂在带版本前缀的路径下，且仅在回环地址可达的环境中提供。
        docs_url=f"{settings.api_prefix}/docs",
        openapi_url=f"{settings.api_prefix}/openapi.json",
        redoc_url=None,
    )

    app.state.settings = settings

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.allowed_origins),
        allow_credentials=False,  # M1 无需跨源 Cookie；开启前必须先有 CSRF 方案
        allow_methods=["GET"],  # 当前只有只读接口，按需收紧
        allow_headers=["Content-Type"],
        expose_headers=["X-Request-ID"],
    )
    app.add_middleware(RequestIDMiddleware)

    install_exception_handlers(app)

    app.include_router(health.router, prefix=settings.api_prefix)

    return app


app = create_app()
