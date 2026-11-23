"""统一错误响应结构（AGENT.md §7.1）。

约束：
- 错误体必须包含 machine-readable 的 code 和 user-safe 的 message。
- 绝不能把异常堆栈、密钥或内部文件路径返回给前端。
- 每个响应带 request_id，便于用户报障时与日志对齐。
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

REQUEST_ID_HEADER = "X-Request-ID"

logger = logging.getLogger(__name__)


def error_body(code: str, message: str, request_id: str, details: Any = None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
            "request_id": request_id,
        }
    }
    if details is not None:
        body["error"]["details"] = details
    return body


def _request_id(request: Request) -> str:
    # 由 RequestIDMiddleware 注入；缺失时返回占位符而不是抛错。
    return getattr(request.state, "request_id", "unknown")


def _http_status_code(exc: StarletteHTTPException) -> tuple[str, str]:
    """把 HTTP 状态码映射为稳定的机器码。

    对外只暴露通用、安全的描述，不透传内部细节。
    """
    mapping = {
        400: ("BAD_REQUEST", "请求格式不正确。"),
        401: ("UNAUTHORIZED", "未认证或凭证无效。"),
        403: ("FORBIDDEN", "没有权限执行该操作。"),
        404: ("NOT_FOUND", "请求的资源不存在。"),
        405: ("METHOD_NOT_ALLOWED", "该资源不支持此请求方法。"),
        409: ("CONFLICT", "操作与当前状态冲突。"),
        413: ("PAYLOAD_TOO_LARGE", "请求内容过大。"),
        415: ("UNSUPPORTED_MEDIA_TYPE", "不支持的请求内容类型。"),
        429: ("RATE_LIMITED", "请求过于频繁，请稍后再试。"),
    }
    return mapping.get(exc.status_code, ("HTTP_ERROR", "请求处理失败。"))


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code, message = _http_status_code(exc)
        # 404 等常见错误保持默认提示，其余状态码使用上面的安全文案。
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message, _request_id(request)),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # 字段级错误对用户有用，且不含内部路径；只保留定位信息。
        details = [
            {
                "location": ".".join(str(part) for part in err.get("loc", ())),
                "type": err.get("type", "invalid"),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_body(
                "VALIDATION_ERROR", "输入参数校验失败。", _request_id(request), details
            ),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # 不暴露异常正文；真实堆栈只写入服务端结构化日志。
        request_id = _request_id(request)
        logger.exception(
            "未处理异常",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
            },
        )
        # 500 由最外层 ServerErrorMiddleware 生成，RequestIDMiddleware 无法回写响应头，
        # 因此这里显式带上 X-Request-ID，保证前端能凭它定位日志。
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "服务器内部错误，请稍后重试。", request_id),
            headers={REQUEST_ID_HEADER: request_id},
        )
