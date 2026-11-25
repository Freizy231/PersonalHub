"""迭代 4 第 5 项：用假 Provider 跑通「对话 → 审批 → 我的任务」的完整链路。

为什么要这条测试：迭代 1 的验收场景（在对话里让 AI 记一件事，确认后出现在
「我的任务」）此前只有**分段**覆盖——Broker 层有 `test_task_provenance`，
任务接口层有 `test_user_tasks`，但没有一条测试把两端接起来。分段都过关
并不等于整条链路能走通：中间任何一环（SSE 事件、审批等待器、票据消费、
`created_via_approval_id` 落库）断掉，用户看到的都是"确认了但任务没出现"。

**零外发**：供应商是进程内的脚本，不访问网络。真实模型链路仍需要凭证，
见 `docs/ROADMAP.md` 的「未验证」一节。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from app.ai import ChatMessage, StreamChunk, ToolCallDelta
from app.core.config import Settings
from app.main import create_app

API = "/api/v1"


class ApprovalThenTextProvider:
    """第一轮请求 `create_task`，并在拿到工具结果后给出收尾文本。

    `tools` 只在对话路径被传入；脚本忽略它（不据此改变行为），
    保持与真实适配器一致的签名。
    """

    name = "fake-approval"

    def __init__(self) -> None:
        self.tool_calls: list[list[ChatMessage]] = []

    async def stream_chat(
        self, messages: list[ChatMessage], model: str, tools: object = None
    ) -> AsyncIterator[StreamChunk]:
        del tools
        has_tool_result = any(message.role == "tool" for message in messages)
        if not has_tool_result:
            self.tool_calls.append(list(messages))
            yield StreamChunk(
                tool_call_delta=ToolCallDelta(
                    index=0,
                    id="call_task_1",
                    name="create_task",
                    # 参数里带 description，验证它一路到达落库字段。
                    arguments_fragment=(
                        '{"title": "给番茄钟加休息提醒", "description": "每 25 分钟提醒一次"}'
                    ),
                )
            )
            yield StreamChunk(finish_reason="tool_calls")
            return
        yield StreamChunk(delta="好的，已经把「给番茄钟加休息提醒」记到你的任务里。")


def _sse_events(raw: str) -> list[tuple[str, dict]]:
    out: list[tuple[str, dict]] = []
    event = ""
    for line in raw.splitlines():
        if line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("data: "):
            out.append((event, json.loads(line[len("data: ") :])))
    return out


class TestApprovalToTaskChain:
    @pytest.mark.asyncio
    async def test_approved_tool_call_creates_a_visible_user_task(
        self, settings: Settings, db_path, monkeypatch
    ) -> None:
        import app.api.chat as chat_module
        from app.repositories.conversations import ConversationRepository

        provider = ApprovalThenTextProvider()
        monkeypatch.setattr(chat_module, "build_provider", lambda _s: provider)

        app = create_app(settings)
        with app.state.session_factory() as session:
            conversation_id = ConversationRepository(session).create("记一件事").id

        # 走真实 SSE 生成器：审批等待需要事件循环与两个并发请求会合，
        # TestClient 无法在响应读完前发起决策，因此这里直接驱动 ASGI。
        from types import SimpleNamespace

        from app.api.chat import stream_chat
        from app.repositories.messages import MessageRepository
        from app.schemas.chat import ChatStreamRequest

        stream_session = app.state.session_factory()
        try:
            response = await stream_chat(
                ChatStreamRequest(conversation_id=conversation_id, content="帮我记一个任务"),
                SimpleNamespace(
                    app=app, headers={}, state=SimpleNamespace(request_id="test-chain")
                ),
                ConversationRepository(stream_session),
                MessageRepository(stream_session),
            )

            approval: dict | None = None
            chunks: list[str] = []

            async def read_until_approval() -> None:
                nonlocal approval
                async for frame in response.body_iterator:
                    text = frame if isinstance(frame, str) else frame.decode()
                    for name, data in _sse_events(text):
                        if name == "approval_required":
                            approval = data
                        elif name == "delta":
                            chunks.append(str(data.get("text", "")))
                        elif name == "done":
                            return
                    if approval is not None:
                        return

            await asyncio.wait_for(read_until_approval(), timeout=10)

            assert approval is not None, "对话没有请求审批"
            assert approval["tool"] == "create_task"
            # 影响说明一路从工具声明传到界面（迭代 2 第 2 项）。
            assert "我的任务" in approval["impact"]
            # 摘要必须是「标签 + 值」，不是 JSON（D-012）。
            summary = {item["label"]: item["value"] for item in approval["summary"]}
            assert "给番茄钟加休息提醒" in " ".join(summary.values())

            # 批准之前，任务**不该**存在：写操作必须等用户决定。
            with app.state.session_factory() as check:
                from app.models.task import Task

                assert check.query(Task).count() == 0

            # 用户点「批准」——与真实前端调用同一个接口。
            from app.api.approvals import DecisionIn, decide_approval
            from app.models.approval import Approval

            with app.state.session_factory() as decide_session:
                row = decide_session.query(Approval).one()
                approval_id = row.id
                await decide_approval(
                    approval_id,
                    DecisionIn(decision="approve"),
                    decide_session,
                    SimpleNamespace(app=app, state=SimpleNamespace(session_token=None)),
                )

            # 继续读完 SSE，确认对话恢复并给出收尾文本。
            async def finish() -> None:
                async for frame in response.body_iterator:
                    text = frame if isinstance(frame, str) else frame.decode()
                    for _name, data in _sse_events(text):
                        if isinstance(data, dict) and data.get("text"):
                            chunks.append(str(data["text"]))

            await asyncio.wait_for(finish(), timeout=10)
        finally:
            stream_session.close()

        assert any("记到你的任务里" in chunk for chunk in chunks), chunks

        # 最后一步：任务真的出现在「我的任务」里，且能追溯到这次审批。
        from fastapi.testclient import TestClient

        with TestClient(app) as client:
            body = client.get(f"{API}/user-tasks").json()

        titles = [item["title"] for item in body["items"]]
        assert titles == ["给番茄钟加休息提醒"], titles
        task = body["items"][0]
        assert task["description"] == "每 25 分钟提醒一次"
        assert task["status"] == "OPEN"
        assert task["created_via_approval_id"] == approval_id

    @pytest.mark.asyncio
    async def test_rejected_tool_call_leaves_no_task(
        self, settings: Settings, db_path, monkeypatch
    ) -> None:
        """拒绝也必须走完整链路：用户说"不要"，就不能有任务凭空出现。"""
        import app.api.chat as chat_module
        from app.repositories.conversations import ConversationRepository

        provider = ApprovalThenTextProvider()
        monkeypatch.setattr(chat_module, "build_provider", lambda _s: provider)

        app = create_app(settings)
        with app.state.session_factory() as session:
            conversation_id = ConversationRepository(session).create("别记").id

        from types import SimpleNamespace

        from app.api.chat import stream_chat
        from app.repositories.messages import MessageRepository
        from app.schemas.chat import ChatStreamRequest

        stream_session = app.state.session_factory()
        approval_id: str | None = None
        try:
            response = await stream_chat(
                ChatStreamRequest(conversation_id=conversation_id, content="帮我记一个任务"),
                SimpleNamespace(app=app, headers={}, state=SimpleNamespace(request_id="t2")),
                ConversationRepository(stream_session),
                MessageRepository(stream_session),
            )

            async def read_until_approval() -> None:
                nonlocal approval_id
                async for frame in response.body_iterator:
                    text = frame if isinstance(frame, str) else frame.decode()
                    for name, data in _sse_events(text):
                        if name == "approval_required":
                            approval_id = data["approval_id"]
                    if approval_id is not None:
                        return

            await asyncio.wait_for(read_until_approval(), timeout=10)
            assert approval_id is not None

            from app.api.approvals import DecisionIn, decide_approval
            from app.models.approval import Approval

            with app.state.session_factory() as decide_session:
                row = decide_session.query(Approval).one()
                await decide_approval(
                    row.id,
                    DecisionIn(decision="reject"),
                    decide_session,
                    SimpleNamespace(app=app, state=SimpleNamespace(session_token=None)),
                )

            async def drain() -> None:
                async for _frame in response.body_iterator:
                    pass

            await asyncio.wait_for(drain(), timeout=10)
        finally:
            stream_session.close()

        from app.models.task import Task

        with app.state.session_factory() as check:
            assert check.query(Task).count() == 0
