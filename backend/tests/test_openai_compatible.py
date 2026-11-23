"""DeepSeek 适配器测试。

所有 HTTP 交互用 httpx.MockTransport 模拟，不发起真实网络请求。
重点覆盖：SSE 解析、错误映射、以及"错误信息里不能出现 API Key"。
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.ai import ChatMessage, ProviderError
from app.ai.openai_compatible import OpenAICompatibleProvider, _parse_sse_line

FAKE_KEY = "sk-test-SECRET-DO-NOT-LEAK-12345"


def _sse_body(chunks: list[str]) -> bytes:
    lines = []
    for text in chunks:
        payload = {"choices": [{"delta": {"content": text}}]}
        lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    lines.append("data: [DONE]")
    return ("\n\n".join(lines) + "\n\n").encode()


async def _collect(provider: OpenAICompatibleProvider, messages, model="deepseek-chat") -> str:
    parts = []
    async for chunk in provider.stream_chat(messages, model):
        parts.append(chunk.delta)
    return "".join(parts)


def _provider_with(handler) -> OpenAICompatibleProvider:
    """使用 MockTransport 注入，避免任何真实网络请求。"""
    return OpenAICompatibleProvider(
        FAKE_KEY,
        base_url="https://api.example.test",
        transport=httpx.MockTransport(handler),
    )


class TestSseParsing:
    def test_parses_delta(self) -> None:
        line = 'data: {"choices":[{"delta":{"content":"你好"}}]}'
        chunk = _parse_sse_line(line)

        assert chunk is not None
        assert chunk.delta == "你好"

    @pytest.mark.parametrize(
        "line",
        [
            "",
            "event: ping",
            ": heartbeat",
            "data: [DONE]",
            "data: ",
            "data: not-json",
            'data: {"choices":[]}',
            'data: {"choices":[{"delta":{}}]}',
            'data: {"choices":[{"delta":{"content":""}}]}',
            'data: {"choices":[{"delta":{"content":null}}]}',
        ],
    )
    def test_ignores_non_content_lines(self, line: str) -> None:
        assert _parse_sse_line(line) is None


class TestStreaming:
    @pytest.mark.asyncio
    async def test_streams_all_chunks(self) -> None:
        seen: dict[str, object] = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["auth"] = request.headers.get("authorization")
            seen["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                content=_sse_body(["甲", "乙", "丙"]),
                headers={"content-type": "text/event-stream"},
            )

        provider = _provider_with(handler)
        result = await _collect(provider, [ChatMessage(role="user", content="hi")], "deepseek-chat")

        assert result == "甲乙丙"
        assert seen["url"] == "https://api.example.test/chat/completions"
        assert seen["auth"] == f"Bearer {FAKE_KEY}"
        body = seen["body"]
        assert isinstance(body, dict)
        assert body["stream"] is True
        assert body["model"] == "deepseek-chat"
        assert body["messages"] == [{"role": "user", "content": "hi"}]

    @pytest.mark.asyncio
    async def test_handles_multibyte_split_across_lines(self) -> None:
        """中文与 emoji 在 SSE 中可能被拆开，解析必须基于完整行。"""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=_sse_body(["😀", "完成"]),
                headers={"content-type": "text/event-stream"},
            )

        result = await _collect(_provider_with(handler), [ChatMessage("user", "x")])

        assert result == "😀完成"


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("status", "expected_code"),
        [
            (401, "PROVIDER_AUTH"),
            (402, "PROVIDER_QUOTA"),
            (429, "PROVIDER_RATE_LIMIT"),
            (500, "PROVIDER_UNAVAILABLE"),
            (400, "PROVIDER_BAD_REQUEST"),
        ],
    )
    @pytest.mark.asyncio
    async def test_error_status_mapped(self, status: int, expected_code: str) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status, json={"error": {"message": "raw provider text"}})

        with pytest.raises(ProviderError) as excinfo:
            await _collect(_provider_with(handler), [ChatMessage("user", "x")])

        assert excinfo.value.code == expected_code
        # 供应商原始文案不得进入面向用户的错误信息。
        assert "raw provider text" not in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_api_key_never_appears_in_errors(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("boom", request=request)

        with pytest.raises(ProviderError) as excinfo:
            await _collect(_provider_with(handler), [ChatMessage("user", "x")])

        assert FAKE_KEY not in str(excinfo.value)
        assert "SECRET-DO-NOT-LEAK" not in str(excinfo.value)
        assert excinfo.value.code == "PROVIDER_UNREACHABLE"

    @pytest.mark.asyncio
    async def test_timeout_mapped(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(ProviderError) as excinfo:
            await _collect(_provider_with(handler), [ChatMessage("user", "x")])

        assert excinfo.value.code == "PROVIDER_TIMEOUT"


class TestProxyHandling:
    """实测发现的崩溃：环境变量 ALL_PROXY=socks://... 会让 httpx 抛 ValueError。"""

    def test_loopback_never_uses_proxy(self, monkeypatch) -> None:
        from app.ai.openai_compatible import _resolve_proxy

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        assert _resolve_proxy("http://127.0.0.1:8899") is None
        assert _resolve_proxy("http://localhost:8899") is None

    def test_socks_proxy_is_ignored_not_crashing(self, monkeypatch) -> None:
        from app.ai.openai_compatible import _resolve_proxy

        monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7897/")
        monkeypatch.delenv("HTTPS_PROXY", raising=False)
        monkeypatch.delenv("https_proxy", raising=False)
        # 不应抛异常，返回 None 表示直连。
        assert _resolve_proxy("https://api.deepseek.com") is None

    def test_http_proxy_is_used_for_remote_host(self, monkeypatch) -> None:
        from app.ai.openai_compatible import _resolve_proxy

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        assert _resolve_proxy("https://api.deepseek.com") == "http://127.0.0.1:7897"

    @pytest.mark.asyncio
    async def test_streaming_works_with_socks_env_set(self, monkeypatch) -> None:
        """回归：本机有 socks 代理时，整个请求曾直接崩溃。"""
        monkeypatch.setenv("ALL_PROXY", "socks://127.0.0.1:7897/")
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:7897")

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200, content=_sse_body(["ok"]), headers={"content-type": "text/event-stream"}
            )

        result = await _collect(_provider_with(handler), [ChatMessage("user", "x")])

        assert result == "ok"


class TestBaseUrlHandling:
    """回归：用户把中转站 Key 发到了官方地址，得到 401 却无从判断。"""

    @pytest.mark.asyncio
    async def test_custom_base_url_with_v1_prefix(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(
                200, content=_sse_body(["ok"]), headers={"content-type": "text/event-stream"}
            )

        provider = OpenAICompatibleProvider(
            FAKE_KEY,
            base_url="https://relay.example.com/v1",
            transport=httpx.MockTransport(handler),
        )
        await _collect(provider, [ChatMessage("user", "x")])

        # 关键：/v1 前缀必须保留，不能多也不能少。
        assert seen["url"] == "https://relay.example.com/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_trailing_slash_stripped(self) -> None:
        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            return httpx.Response(200, content=_sse_body(["x"]), headers={})

        provider = OpenAICompatibleProvider(
            FAKE_KEY,
            base_url="https://relay.example.com/v1/",
            transport=httpx.MockTransport(handler),
        )
        await _collect(provider, [ChatMessage("user", "x")])

        assert seen["url"] == "https://relay.example.com/v1/chat/completions"

    @pytest.mark.asyncio
    async def test_401_message_mentions_base_url_and_key_mismatch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(401, json={"error": {"message": "invalid"}})

        provider = OpenAICompatibleProvider(
            FAKE_KEY, base_url="https://api.deepseek.com", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ProviderError) as excinfo:
            await _collect(provider, [ChatMessage("user", "x")])

        message = str(excinfo.value)
        # 必须让用户意识到：可能是 Key 不属于这个地址。
        assert "https://api.deepseek.com" in message
        assert "接口地址" in message

    @pytest.mark.asyncio
    async def test_404_message_points_to_base_url_config(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, text="Not Found")

        provider = OpenAICompatibleProvider(
            FAKE_KEY, base_url="https://relay.example.com", transport=httpx.MockTransport(handler)
        )
        with pytest.raises(ProviderError) as excinfo:
            await _collect(provider, [ChatMessage("user", "x")])

        assert excinfo.value.code == "PROVIDER_NOT_FOUND"
        assert "PH_PROVIDER_BASE_URL" in str(excinfo.value)


class TestLocalHostDetection:
    """局域网/本机地址不能走代理。"""

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1:8080",
            "http://localhost:3000/v1",
            "http://192.168.1.10:8000/v1",
            "http://10.0.0.5/v1",
            "http://172.16.3.4:9000",
        ],
    )
    def test_private_addresses_bypass_proxy(self, url: str, monkeypatch) -> None:
        from app.ai.openai_compatible import _resolve_proxy

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        assert _resolve_proxy(url) is None

    @pytest.mark.parametrize(
        "url",
        ["https://api.deepseek.com", "https://relay.example.com/v1", "https://8.8.8.8"],
    )
    def test_public_addresses_use_proxy(self, url: str, monkeypatch) -> None:
        from app.ai.openai_compatible import _resolve_proxy

        monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:7897")
        monkeypatch.delenv("ALL_PROXY", raising=False)
        assert _resolve_proxy(url) == "http://127.0.0.1:7897"
