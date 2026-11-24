"""流式对话测试。

模型调用一律用假 Provider（AGENT.md §9.2：LLM 非确定性，关键规则必须用确定性测试）。
真实网络调用不进入测试：不可重复、有费用、依赖外部状态。
"""

from __future__ import annotations

import asyncio
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
        self, messages: list[ChatMessage], model: str, tools=None
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


class _EndlessProvider:
    """产出两段文本后一直挂着，用来模拟"用户点了停止生成"。

    `closed` 在生成器的 finally 里置位：只有调用方真正关闭了流，
    它才会变成 True。这正是"客户端断开是否停止计费"的可观测证据。
    """

    name = "endless"

    def __init__(self) -> None:
        self.closed = False
        self.request_approval = False

    async def stream_chat(self, messages, model, tools=None):  # type: ignore[no-untyped-def]
        from app.ai import ToolCallDelta

        try:
            yield StreamChunk(delta="第一段")
            if self.request_approval:
                yield StreamChunk(
                    tool_call_delta=ToolCallDelta(
                        index=0,
                        id="call_1",
                        name="create_task",
                        arguments_fragment='{"title": "测试任务"}',
                    )
                )
                yield StreamChunk(finish_reason="tool_calls")
            await asyncio.sleep(3600)
        finally:
            self.closed = True


@pytest.fixture()
def app_with_provider(settings, db_path, monkeypatch):
    """返回 (app, provider, conversation_id)：直接驱动 SSE 生成器用。

    与 `chat_client` 的区别：不经过 TestClient，因为 TestClient 无法在
    响应读完前关闭生成器，测不了"断开即停止"。这里复用同一个数据库，
    并用真实 Alembic 迁移建表。
    """
    import app.api.chat as chat_module

    provider = _EndlessProvider()
    monkeypatch.setattr(chat_module, "build_provider", lambda s: provider)

    app = create_app(settings)
    with app.state.session_factory() as session:
        from app.repositories.conversations import ConversationRepository

        cid = ConversationRepository(session).create("断开测试").id
    return app, provider, cid


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

    def test_history_keeps_the_latest_messages(self, create) -> None:
        """长会话必须把**最近**的对话交给模型，而不是最早的。

        实测缺陷：历史原先按正序取前 N 条，超过上限后模型看不到最新内容，
        表现为"AI 突然失忆"，且很难与被问的问题联系起来。
        """
        from app.api.chat import MAX_HISTORY_MESSAGES

        client, provider = create(["x"])
        cid = _create_conversation(client)
        total = MAX_HISTORY_MESSAGES + 10
        for i in range(total):
            client.post(f"{CONVERSATIONS}/{cid}/messages", json={"content": f"m{i}"})

        client.post(STREAM, json={"conversation_id": cid, "content": "最后"})

        sent, _ = provider.calls[0]
        expected = [f"m{i}" for i in range(total - MAX_HISTORY_MESSAGES + 1, total)] + ["最后"]
        assert [m.content for m in sent] == expected
        # 最早的几条必须已被丢掉。
        assert "m0" not in [m.content for m in sent]

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


class TestDisconnectStopsGeneration:
    """客户端断开必须真正停止后台生成（§7.3 取消语义）。

    为什么不走 TestClient：它的 with 块不会在响应读完前关闭生成器，
    无法触发真实断开。这里直接拿到 StreamingResponse 的生成器，
    用 `aclose()` 触发与浏览器关闭页面时**完全相同**的 GeneratorExit。
    """

    @staticmethod
    async def _open(app, cid):  # type: ignore[no-untyped-def]
        """在**保持打开**的会话上构造流式响应，返回 (session, response)。

        会话必须在迭代期间一直打开：请求依赖的 Session 由 FastAPI 管理，
        若在测试里提前关闭，后续数据库调用会报 "Session is closed"，
        那是测试脚手架的错误，不是产品行为。
        """
        from types import SimpleNamespace

        from app.api.chat import stream_chat
        from app.repositories.conversations import ConversationRepository
        from app.repositories.messages import MessageRepository
        from app.schemas.chat import ChatStreamRequest

        session = app.state.session_factory()
        response = await stream_chat(
            ChatStreamRequest(conversation_id=cid, content="问"),
            SimpleNamespace(
                app=app, headers={}, state=SimpleNamespace(request_id="test-disconnect")
            ),
            ConversationRepository(session),
            MessageRepository(session),
        )
        return session, response

    @pytest.mark.asyncio
    async def test_disconnect_closes_provider_stream(self, app_with_provider) -> None:
        app, provider, cid = app_with_provider

        session, response = await self._open(app, cid)
        try:
            await response.body_iterator.__anext__()
            await response.body_iterator.aclose()
            # 让被取消的后台任务有机会收尾。
            await asyncio.sleep(0.3)
        finally:
            session.close()

        assert provider.closed, "断开后必须关闭供应商的生成器，否则会持续计费"

    @pytest.mark.asyncio
    async def test_disconnect_saves_no_assistant_message(self, app_with_provider) -> None:
        app, _provider, cid = app_with_provider

        session, response = await self._open(app, cid)
        try:
            await response.body_iterator.__anext__()
            await response.body_iterator.aclose()
            await asyncio.sleep(0.3)
        finally:
            session.close()

        with app.state.session_factory() as session:
            from app.repositories.messages import MessageRepository

            items = MessageRepository(session).list_for_conversation(cid)
        assert [(m.role, m.content) for m in items] == [("user", "问")]

    @pytest.mark.asyncio
    async def test_disconnect_cancels_waiting_approval(self, app_with_provider) -> None:
        """断开时仍停在 PENDING 的审批必须被作废，而不是等 10 分钟超时。"""
        app, provider, cid = app_with_provider
        provider.request_approval = True

        session, response = await self._open(app, cid)
        try:
            for _ in range(12):
                frame = await asyncio.wait_for(response.body_iterator.__anext__(), timeout=5)
                if "approval_required" in frame:
                    break
            await response.body_iterator.aclose()
            await asyncio.sleep(0.3)
        finally:
            session.close()

        from app.models.approval import Approval, ApprovalStatus

        with app.state.session_factory() as session:
            rows = session.query(Approval).all()
        assert rows, "应当创建过审批记录"
        assert all(row.status != ApprovalStatus.PENDING.value for row in rows)
        assert rows[0].decision_source == "cancelled"


class TestLoopErrorIsNotSwallowed:
    def test_loop_exception_becomes_error_event(self, create, monkeypatch) -> None:
        """Agent 循环内部异常必须被记录并告知用户。

        实测缺陷：原先循环抛异常后只有 finally，异常被完全吞掉，
        用户只看到"模型没有返回内容"，日志里没有任何线索。
        """
        import app.api.chat as chat_module

        def boom(**kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("循环内部炸了")
            yield  # pragma: no cover

        monkeypatch.setattr(chat_module, "run_agent_loop", boom)
        client, _ = create()
        cid = _create_conversation(client)

        events = _events(client.post(STREAM, json={"conversation_id": cid, "content": "问"}))

        codes = [data.get("code") for name, data in events if name == "error"]
        assert "AGENT_LOOP_ERROR" in codes
