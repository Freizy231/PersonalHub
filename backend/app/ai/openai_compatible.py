"""OpenAI Chat Completions 兼容适配器。

DeepSeek 官方、各类中转站（API 网关）以及自建服务大多提供与
OpenAI Chat Completions 一致或相近的接口，因此用同一个适配器覆盖，
不针对某一家写死（AGENT.md §4.6 Adapter 模式）。

只用 httpx 直接实现，不引入官方 SDK —— 少一个依赖，也避免业务层被 SDK
类型渗透。

地址配置：PH_PROVIDER_BASE_URL
    官方 DeepSeek : https://api.deepseek.com
    带版本前缀    : https://example.com/v1
最终请求地址为 f"{base_url}/chat/completions"。
"""

from __future__ import annotations

import ipaddress
import json
from collections.abc import AsyncIterator
from urllib.parse import urlparse

import httpx

from app.ai import ChatMessage, ProviderError, StreamChunk, ToolCallDelta, ToolSpec

# 连接超时必须短：本地工具连不上就该快速失败，而不是让界面转圈 30 秒。
# 读取超时较长：模型生成首包可能慢。整体上限由调用方控制。
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 120.0

_LOCAL_HOSTNAMES = frozenset({"localhost", "localhost.localdomain"})


def _is_local_host(host: str) -> bool:
    """判断目标是否为本机/局域网地址。

    实测教训：用户可能把模型部署在局域网内（192.168.x.x、10.x.x.x），
    这类地址绝不能走 HTTP 代理 —— 要么代理服务器不认识，要么把请求
    发到了本不该发的地方。只写死 127.0.0.1 是不够的。
    """
    if not host:
        return False
    if host in _LOCAL_HOSTNAMES:
        return True
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        # 普通域名，按公网处理。
        return False
    return addr.is_loopback or addr.is_private or addr.is_link_local


def _resolve_proxy(base_url: str) -> str | None:
    """决定是否走系统代理。

    两个真实问题（都来自实测）：
    1. 很多环境设置 `ALL_PROXY=socks://...`，httpx 不支持 socks scheme 会直接抛
       ValueError 导致整个请求崩溃。这里只接受 http/https 代理。
    2. 访问本机/局域网地址（本地模型、局域网中转站、测试替身）绝不能走代理。
    """
    host = urlparse(base_url).hostname or ""
    if _is_local_host(host):
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


class OpenAICompatibleProvider:
    """面向任意 OpenAI Chat Completions 兼容服务的适配器。"""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str,
        name: str = "openai-compatible",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self.name = name
        # 仅在测试中注入 MockTransport；生产为 None，使用默认网络栈。
        self._transport = transport

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }

    async def stream_chat(
        self, messages: list[ChatMessage], model: str, tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[StreamChunk]:
        payload: dict[str, object] = {
            "model": model,
            "messages": [_encode_message(m) for m in messages],
            "stream": True,
        }
        if tools:
            # 只声明能力，不授予权限；是否真正执行由 Tool Broker 决定。
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description,
                        "parameters": t.parameters,
                    },
                }
                for t in tools
            ]
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
                        raise await _error_from_response(response, self._base_url)

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


async def _error_from_response(response: httpx.Response, base_url: str) -> ProviderError:
    """把供应商错误转换为安全、可读的错误对象。

    供应商原始响应体可能包含请求细节；只用于服务端日志，
    真正抛出的 message 必须是固定文案。

    实测教训：用户曾把中转站的 Key 发到了官方地址，得到 401 却以为是 Key 错了。
    401 既可能是"Key 无效"，也可能是"Key 不属于这个地址"，因此提示必须同时
    指向这两种可能，并带上实际请求的地址（地址不是秘密，方便用户核对）。
    """
    status = response.status_code
    try:
        body = (await response.aread()).decode("utf-8", errors="replace")
    except Exception:  # pragma: no cover - 极端情况
        body = "<unreadable>"

    if status == 401:
        return _logged_error(
            f"模型服务鉴权失败（{base_url}）。请检查 API Key 是否有效，"
            "以及该 Key 是否属于这个接口地址——不同供应商/中转站的 Key 通常不能混用。",
            "PROVIDER_AUTH",
            status,
            body,
        )
    if status == 402:
        return _logged_error("模型账户余额不足。", "PROVIDER_QUOTA", status, body)
    if status == 403:
        return _logged_error(
            "模型服务拒绝访问，可能是该 Key 无权使用此模型。", "PROVIDER_FORBIDDEN", status, body
        )
    if status == 404:
        return _logged_error(
            f"接口地址不存在（{base_url}）。请检查 PH_PROVIDER_BASE_URL 是否正确，"
            "例如中转站通常需要带 /v1 前缀。",
            "PROVIDER_NOT_FOUND",
            status,
            body,
        )
    if status == 429:
        return _logged_error("请求过于频繁，请稍后重试。", "PROVIDER_RATE_LIMIT", status, body)
    if status >= 500:
        return _logged_error(
            "模型服务暂时不可用，请稍后重试。", "PROVIDER_UNAVAILABLE", status, body
        )
    return _logged_error(
        f"模型服务返回了错误（HTTP {status}）。", "PROVIDER_BAD_REQUEST", status, body
    )


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

    choice = choices[0]
    if not isinstance(choice, dict):
        return None

    delta = choice.get("delta") or {}
    if not isinstance(delta, dict):
        return None

    # 工具调用分片与文本 delta 可能出现在同一条事件里，也可能各自单独出现。
    tool_delta = _parse_tool_call_delta(delta)
    content = delta.get("content")

    if isinstance(content, str) and content != "":
        return StreamChunk(delta=content, tool_call_delta=tool_delta)

    if tool_delta is not None:
        return StreamChunk(tool_call_delta=tool_delta)

    finish = choice.get("finish_reason")
    if isinstance(finish, str):
        # 结束原因需要让调用方知道（tool_calls 表示该执行工具了）。
        return StreamChunk(finish_reason=finish)

    return None


def _encode_message(message: ChatMessage) -> dict[str, object]:
    """把内部消息编码为 OpenAI 协议格式。

    容易出错的细节：
    - assistant 带工具调用时，content 可以是 None（协议允许），但很多实现
      不接受空字符串。这里按有无工具调用分别处理。
    - tool 角色的消息必须带 tool_call_id，否则供应商会拒绝整个请求。
    """
    if message.role == "assistant" and message.tool_calls:
        return {
            "role": "assistant",
            "content": message.content or None,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": call.arguments},
                }
                for call in message.tool_calls
            ],
        }

    if message.role == "tool":
        return {
            "role": "tool",
            "tool_call_id": message.tool_call_id,
            "content": message.content,
        }

    return {"role": message.role, "content": message.content}


def _parse_tool_call_delta(delta: dict) -> ToolCallDelta | None:
    """解析工具调用分片。

    协议细节（实测容易写错的地方）：
    - 只有第一片带 id 和 function.name，后续分片只带 arguments 片段。
    - index 是稳定标识，必须按它归并，不能用出现顺序。
    - arguments 可能被切成任意长度的字符串（甚至切在多字节字符中间）。
    """
    calls = delta.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        return None

    first = calls[0]
    if not isinstance(first, dict):
        return None

    index = first.get("index")
    if not isinstance(index, int):
        index = 0

    function = first.get("function")
    name = None
    fragment = ""
    if isinstance(function, dict):
        if isinstance(function.get("name"), str):
            name = function["name"]
        if isinstance(function.get("arguments"), str):
            fragment = function["arguments"]

    call_id = first.get("id") if isinstance(first.get("id"), str) else None

    return ToolCallDelta(index=index, id=call_id, name=name, arguments_fragment=fragment)
