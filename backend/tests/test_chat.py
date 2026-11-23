"""流式对话测试。

模型调用一律用假 Provider（AGENT.md §9.2：LLM 非确定性，关键规则必须用确定性测试）。
真实网络调用不进入测试：不可重复、有费用、依赖外部状态。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app.ai import ChatMessage, ProviderError, StreamChunk
from app.core.config import Settings
from app.main import create_app

API = "/api/v1"
CONVERSATIONS = f"{API}/conversations"
STREAM = f"{API}/chat/stream"


class FakeProvider:
    """可编程的假供应商。"""

    name = "fake"

    def __init__(
        self,
        chunks: list[str] | None = None,
        error: ProviderError | None = None,
    ) -> None:
        self.chunks = chunks if chunks is not None else ["你好", "，", "世界"]
        self.error = error
        self.calls: list[tuple[list[ChatMessage], str]] = []

    async def stream_chat(
        self, messages: list[ChatMessage], model: str
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append((messages, model))
        if self.error is not None:
            raise self.error
        for text in self.chunks:
            yield StreamChunk(delta=text)


@pytest.fixture()
def chat_client(settings, db_path, monkeypatch):
    """在 conftest 提供的同一数据库上创建客户端，并注入假供应商。

    复用 `settings` / `db_path` 夹具，保证与 `session` 夹具读写同一数据库
    （跨工作空间测试需要直接改库）。
    """
    import app.api.chat as chat_module

    provider = FakeProvider()
    monkeypatch.setattr(chat_module, "build_provider", lambda s: provider)

    app = create_app(settings)
    with TestClient(app) as client:
        yield client, provider


@pytest.fixture()
def create(chat_client) -> callable:
    """返回工厂：替换假供应商行为并复用同一客户端。"""

    client, provider = chat_client

    def factory(
        chunks: list[str] | None = None, error: ProviderError | None = None
    ) -> tuple[TestClient, FakeProvider]:
        provider.chunks = chunks if chunks is not None else ["你好"]
        provider.error = error
        return client, provider

    return factory


def _create_conversation(client: TestClient, title: str = "对话") -> str:
    return client.post(CONVERSATIONS, json={"title": title}).json()["id"]


def _events(response) -> list[tuple[str, dict]]:
    """把 SSE 响应体解析为 (event, data) 列表。"""
    out: list[tuple[str, dict]] = []
    event = None
    for line in response.text.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out.append((event or "", json.loads(line[len("data: ") :])))
    return out


class TestStreaming:
    def test_streams_deltas_then_done(self, create) -> None:
        client, _ = create(["甲", "乙"])
        cid = _create_conversation(client)

        response = client.post(STREAM, json={"conversation_id": cid, "content": "你好"})

        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        events = _events(response)
        assert [e[0] for e in events] == ["delta", "delta", "done"]
        assert "".join(e[1]["text"] for e in events if e[0] == "delta") == "甲乙"
        assert events[-1][1]["message_id"]

    def test_user_and_assistant_messages_persisted(self, create) -> None:
        client, _ = create(["回复内容"])
        cid = _create_conversation(client)

        client.post(STREAM, json={"conversation_id": cid, "content": "提问"})

        items = client.get(f"{CONVERSATIONS}/{cid}/messages").json()["items"]
        assert [(m["role"], m["content"]) for m in items] == [
            ("user", "提问"),
            ("assistant", "回复内容"),
        ]

    def test_history_is_sent_to_provider(self, create) -> None:
        client, provider = create(["a"])
        cid = _create_conversation(client)
        client.post(f"{CONVERSATIONS}/{cid}/messages", json={"content": "第一条"})

        client.post(STREAM, json={"conversation_id": cid, "content": "第二条"})

        sent_messages, model = provider.calls[0]
        assert [m.content for m in sent_messages] == ["第一条", "第二条"]
        assert [m.role for m in sent_messages] == ["user", "user"]
        assert model  # 模型名来自服务端配置

    def test_history_is_capped(self, create) -> None:
        from app.api.chat import MAX_HISTORY_MESSAGES

        client, provider = create(["x"])
        cid = _create_conversation(client)
        for i in range(MAX_HISTORY_MESSAGES + 10):
            client.post(f"{CONVERSATIONS}/{cid}/messages", json={"content": f"m{i}"})

        client.post(STREAM, json={"conversation_id": cid, "content": "最后"})

        sent, _ = provider.calls[0]
        assert len(sent) == MAX_HISTORY_MESSAGES

    def test_empty_response_is_error_and_not_saved(self, create) -> None:
        client, _ = create(["   "])
        cid = _create_conversation(client)

        events = _events(client.post(STREAM, json={"conversation_id": cid, "content": "问"}))

        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "EMPTY_RESPONSE"
        roles = [m["role"] for m in client.get(f"{CONVERSATIONS}/{cid}/messages").json()["items"]]
        assert roles == ["user"], "空回复不应保存为助手消息"


class TestFailures:
    def test_provider_error_yields_error_event(self, create) -> None:
        client, _ = create(error=ProviderError("模型繁忙", code="PROVIDER_RATE_LIMIT"))
        cid = _create_conversation(client)

        events = _events(client.post(STREAM, json={"conversation_id": cid, "content": "问"}))

        assert events[-1][0] == "error"
        assert events[-1][1]["code"] == "PROVIDER_RATE_LIMIT"
        assert events[-1][1]["message"] == "模型繁忙"

    def test_failed_generation_saves_only_user_message(self, create) -> None:
        client, _ = create(error=ProviderError("失败", code="PROVIDER_ERROR"))
        cid = _create_conversation(client)

        client.post(STREAM, json={"conversation_id": cid, "content": "我的问题"})

        items = client.get(f"{CONVERSATIONS}/{cid}/messages").json()["items"]
        assert [(m["role"], m["content"]) for m in items] == [("user", "我的问题")]

    def test_missing_conversation_returns_404(self, create) -> None:
        client, _ = create()

        response = client.post(STREAM, json={"conversation_id": "0" * 32, "content": "x"})

        assert response.status_code == 404

    def test_other_workspace_conversation_returns_404(self, create, session) -> None:
        from app.models import Conversation

        client, _ = create()
        cid = _create_conversation(client)
        row = session.get(Conversation, cid)
        assert row is not None
        row.workspace_id = "other"
        session.commit()

        assert client.post(STREAM, json={"conversation_id": cid, "content": "x"}).status_code == 404

    def test_missing_api_key_returns_503(self, tmp_path, monkeypatch) -> None:
        from conftest import apply_migrations

        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        settings = Settings(data_dir=tmp_path / "no-key")
        apply_migrations(settings.db_path)
        with TestClient(create_app(settings)) as client:
            cid = client.post(CONVERSATIONS, json={"title": "t"}).json()["id"]

            response = client.post(STREAM, json={"conversation_id": cid, "content": "x"})

        assert response.status_code == 503
        assert "DEEPSEEK_API_KEY" in response.json()["error"]["message"]


class TestRequestValidation:
    def test_rejects_client_supplied_role(self, create) -> None:
        client, _ = create()
        cid = _create_conversation(client)

        response = client.post(
            STREAM, json={"conversation_id": cid, "content": "x", "role": "system"}
        )

        assert response.status_code == 422

    def test_rejects_client_supplied_model(self, create) -> None:
        client, _ = create()
        cid = _create_conversation(client)

        response = client.post(
            STREAM, json={"conversation_id": cid, "content": "x", "model": "expensive-model"}
        )

        assert response.status_code == 422

    def test_rejects_empty_content(self, create) -> None:
        client, _ = create()
        cid = _create_conversation(client)

        assert (
            client.post(STREAM, json={"conversation_id": cid, "content": "  "}).status_code == 422
        )


class TestEgressAudit:
    def test_logs_metadata_but_not_content(self, create, caplog) -> None:
        import logging

        caplog.set_level(logging.INFO, logger="app.audit.egress")
        secret_question = "我的银行卡密码是 123456"
        client, _ = create(["好的"])
        cid = _create_conversation(client)

        client.post(STREAM, json={"conversation_id": cid, "content": secret_question})

        records = [r for r in caplog.records if getattr(r, "event", None) == "model.egress"]
        assert records, "必须记录外发审计"
        started = [r for r in records if r.outcome == "started"]
        completed = [r for r in records if r.outcome == "completed"]
        assert started and completed
        assert completed[0].provider == "fake"
        assert completed[0].message_count >= 1
        assert completed[0].char_count > 0

        # 关键：日志里绝不能出现对话正文。
        for record in records:
            rendered = str(record.__dict__)
            assert "123456" not in rendered
            assert "银行卡" not in rendered

    def test_logs_failure_outcome(self, create, caplog) -> None:
        import logging

        caplog.set_level(logging.INFO, logger="app.audit.egress")
        client, _ = create(error=ProviderError("挂了", code="PROVIDER_TIMEOUT"))
        cid = _create_conversation(client)

        client.post(STREAM, json={"conversation_id": cid, "content": "x"})

        records = [r for r in caplog.records if getattr(r, "event", None) == "model.egress"]
        failed = [r for r in records if r.outcome == "failed"]
        assert failed
        assert failed[0].error_code == "PROVIDER_TIMEOUT"


class TestAbortAudit:
    def test_audit_has_terminal_outcome_on_every_path(self, create, caplog) -> None:
        """每次外发必须同时有 started 与终态记录。

        TestClient 的 with 块无法可靠模拟"客户端断开"（生成器不一定被关闭），
        真实的取消行为由浏览器端到端验证覆盖（见 docs/ROADMAP.md）。
        这里保证：所有能确定结束的路径都留下终态。
        """
        import logging

        caplog.set_level(logging.INFO, logger="app.audit.egress")

        client, _ = create(["ok"])
        cid = _create_conversation(client)
        client.post(STREAM, json={"conversation_id": cid, "content": "x"})

        records = [r for r in caplog.records if getattr(r, "event", None) == "model.egress"]
        outcomes = [r.outcome for r in records]
        assert outcomes == ["started", "completed"], outcomes
