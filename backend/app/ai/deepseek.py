"""DeepSeek 供应商适配器。

接口与 OpenAI Chat Completions 兼容，使用 SSE 流式输出。
只用 httpx 直接实现，不引入官方 SDK —— 少一个依赖，也避免业务层被 SDK
类型渗透（AGENT.md §4.6 Adapter 模式）。

参考：https://api-docs.deepseek.com/
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

from app.ai import ChatMessage, ProviderError, StreamChunk

# 连接超时必须短：本地工具连不上就该快速失败，而不是让界面转圈 30 秒。
# 读取超时较长：模型生成首包可能慢。整体上限由调用方控制。
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 120.0

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _resolve_proxy(base_url: str) -> str | None:
    """决定是否走系统代理。

    两个真实问题（都来自实测）：
    1. 很多环境设置 `ALL_PROXY=socks://...`，httpx 不支持 socks scheme 会直接抛
       ValueError 导致整个请求崩溃。这里只接受 http/https 代理。
    2. 访问本机回环地址（例如本地模型或测试替身）绝不能走代理，
       否则请求会被送到代理服务器甚至泄漏出去。
    """
    host = urlparse(base_url).hostname or ""
    if host in _LOOPBACK_HOSTS:
        return None

    import os

    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        if value.startswith(("http://", "https://")):
            return value
        # socks 之类的 scheme 不被 httpx 支持，忽略而不是崩溃。
        continue
    return None


class DeepSeekProvider:
    name = "deepseek"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = "https://api.deepseek.com",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        # 仅在测试中注入 MockTransport；生产为 None，使用默认网络栈。
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    async def stream_chat(
        self, messages: list[ChatMessage], model: str
    ) -> AsyncIterator[StreamChunk]:
        payload = {
            "model": model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
        }
        timeout = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)

        # 测试注入 transport 时不能同时设 proxy（httpx 会忽略 transport 转而直连）。
        proxy = None if self._transport is not None else _resolve_proxy(self._base_url)

        async with httpx.AsyncClient(
            timeout=timeout,
            transport=self._transport,
            # 代理策略由 _resolve_proxy 决定：httpx 自己读环境变量时遇到
            # socks:// 会直接抛 ValueError（实测崩溃），因此关闭自动读取。
            proxy=proxy,
            trust_env=False,
        ) as client:
            try:
                async with client.stream(
                    "POST",
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                ) as response:
                    if response.status_code != 200:
                        raise await _error_from_response(response)

                    async for line in response.aiter_lines():
                        chunk = _parse_sse_line(line)
                        if chunk is not None:
                            yield chunk
            except httpx.TimeoutException as exc:
                # 错误信息不得包含请求头（内含 Key）。
                raise ProviderError("模型响应超时，请稍后重试。", code="PROVIDER_TIMEOUT") from exc
            except httpx.HTTPError as exc:
                raise ProviderError(
                    "无法连接到模型服务，请检查网络。", code="PROVIDER_UNREACHABLE"
                ) from exc


async def _error_from_response(response: httpx.Response) -> ProviderError:
    """把供应商错误转换为安全、可读的错误对象。

    供应商原始响应体可能包含请求细节；只用于服务端日志，
    真正抛出的 message 必须是固定文案。
    """
    status = response.status_code
    try:
        body = (await response.aread()).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - 极端情况
        body = "<unreadable>"

    if status == 401:
        return _logged_error(
            "模型服务鉴权失败，请检查后端配置的 API Key。", "PROVIDER_AUTH", status, body
        )
    if status == 402:
        return _logged_error("模型账户余额不足。", "PROVIDER_QUOTA", status, body)
    if status == 429:
        return _logged_error("请求过于频繁，请稍后重试。", "PROVIDER_RATE_LIMIT", status, body)
    if status >= 500:
        return _logged_error(
            "模型服务暂时不可用，请稍后重试。", "PROVIDER_UNAVAILABLE", status, body
        )
    return _logged_error("模型服务返回了错误。", "PROVIDER_BAD_REQUEST", status, body)


def _logged_error(message: str, code: str, status: int, body: str) -> ProviderError:
    import logging

    logging.getLogger(__name__).warning(
        "供应商返回错误",
        extra={"provider_status": status, "provider_body": body[:500]},
    )
    return ProviderError(message, code=code)


def _parse_sse_line(line: str) -> StreamChunk | None:
    """解析一行 SSE 数据。

    格式：`data: {json}`，结束标记为 `data: [DONE]`。
    供应商偶尔会在同一行附带注释或空行，一律跳过。
    """
    if not line or not line.startswith("data:"):
        return None
    data = line[len("data:") :].strip()
    if not data or data == "[DONE]":
        return None

    try:
        event = json.loads(data)
    except json.JSONDecodeError:
        # 供应商可能发送心跳或非标准行，忽略而不是中断整个流。
        return None

    choices = event.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    if not isinstance(content, str) or content == "":
        return None
    return StreamChunk(delta=content)
