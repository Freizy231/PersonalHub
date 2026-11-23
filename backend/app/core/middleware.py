"""请求标识中间件。

目的（AGENT.md §7.1、§7.4）：
- 每个请求都有可追踪的 request_id，用于把前端错误提示与服务端日志关联起来。
- 只接受格式受限的外部 request_id，避免日志注入和超长值。
"""

from __future__ import annotations

import re
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-ID"

# 只允许 8~64 位的字母、数字、连字符。不符合要求的一律重新生成。
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{8,64}$")


def new_request_id() -> str:
    return uuid.uuid4().hex


class RequestIDMiddleware(BaseHTTPMiddleware):
    """给每个请求分配 request_id，并回写到响应头。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else new_request_id()

        # 放在 state 上，供错误处理器和日志读取。
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
