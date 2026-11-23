"""Agent Loop 与 Tool Broker 测试（M5 验收）。

重点验证的安全约束（AGENT.md §4.10、§8.4）：
1. 模型发起的工具调用必须经过 Broker（不存在绕过路径）。
2. 未注册工具被拒绝。
3. 参数 Schema 校验：非法 JSON、未知字段、类型不符全部拒绝。
4. 写操作在 M5 一律拒绝（审批机制尚未就绪）。
5. 工具结果以"不可信数据"回填，工具失败不终止整轮对话。
6. 循环次数有上限，防止无限消耗。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.agent.loop import MAX_TOOL_ROUNDS, run_agent_loop
from app.ai import ChatMessage, StreamChunk, ToolCallDelta
from app.capabilities import (
    RiskLevel,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolRegistry,
    validate_arguments,
)
from app.capabilities.broker import MAX_RESULT_CHARS, ToolBroker

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def _shell_tool(**overrides) -> ToolDefinition:  # type: ignore[no-untyped-def]
    defaults = dict(
        name="echo",
        description="回显 input",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        risk=RiskLevel.READ,
        handler=lambda args, ctx: {"echo": args.get("text")},
    )
    defaults.update(overrides)
    return ToolDefinition(**defaults)  # type: ignore[arg-type]


class TestArgumentValidation:
    def test_accepts_valid_object(self) -> None:
        schema = {"properties": {"n": {"type": "integer"}}, "required": ["n"]}

        assert validate_arguments(schema, '{"n": 3}') == {"n": 3}

    def test_empty_arguments_is_empty_dict(self) -> None:
        assert validate_arguments({"properties": {}}, "") == {}

    @pytest.mark.parametrize("raw", ["{not json", "[1,2]", '"str"', "null"])
    def test_rejects_non_object_and_bad_json(self, raw: str) -> None:
        with pytest.raises(ToolExecutionError):
            validate_arguments({"properties": {}}, raw)

    def test_rejects_unknown_field(self) -> None:
        schema = {"properties": {"a": {"type": "string"}}}

        with pytest.raises(ToolExecutionError, match="不支持的参数"):
            validate_arguments(schema, '{"a": "x", "b": "y"}')

    def test_rejects_missing_required(self) -> None:
        schema = {"properties": {"a": {"type": "string"}}, "required": ["a"]}

        with pytest.raises(ToolExecutionError, match="缺少必需参数"):
            validate_arguments(schema, "{}")

    @pytest.mark.parametrize(
        ("value", "expected_type"),
        [("x", "integer"), (1, "string"), (True, "integer"), ([], "object")],
    )
    def test_rejects_type_mismatch(self, value, expected_type: str) -> None:  # type: ignore[no-untyped-def]
        schema = {"properties": {"v": {"type": expected_type}}}

        with pytest.raises(ToolExecutionError):
            validate_arguments(schema, json.dumps({"v": value}))

    def test_bool_is_not_accepted_as_integer(self) -> None:
        """布尔是 int 的子类，必须显式排除，否则 true 会被当成 1。"""
        schema = {"properties": {"v": {"type": "integer"}}}

        with pytest.raises(ToolExecutionError):
            validate_arguments(schema, '{"v": true}')


class TestBroker:
    def _broker(self, *tools: ToolDefinition) -> ToolBroker:
        registry = ToolRegistry()
        for tool in tools:
            registry.register(tool)
        return ToolBroker(registry)

    @pytest.mark.asyncio
    async def test_executes_registered_tool(self) -> None:
        result = await self._broker(_shell_tool()).execute("echo", '{"text":"hi"}', CTX)

        assert result.ok
        assert json.loads(result.content) == {"echo": "hi"}

    @pytest.mark.asyncio
    async def test_unknown_tool_denied(self) -> None:
        result = await self._broker().execute("rm_rf", "{}", CTX)

        assert result.status == "denied"
        assert result.error_code == "TOOL_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_write_tool_denied_without_approval_channel(self) -> None:
        """没有审批通道时绝不执行写操作（默认拒绝，§8.1）。

        M6 起写操作改为"需要审批"，但 Broker 未配置 resolver/session 时
        必须拒绝，而不是"没有通道所以放行"。
        """
        write_tool = _shell_tool(name="danger", risk=RiskLevel.WRITE)

        result = await self._broker(write_tool).execute("danger", '{"text":"x"}', CTX)

        assert result.status == "denied"
        assert result.error_code == "APPROVAL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_write_tool_handler_never_called(self) -> None:
        called: list[str] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            called.append("yes")
            return {}

        write_tool = _shell_tool(name="danger", risk=RiskLevel.WRITE, handler=handler)
        await self._broker(write_tool).execute("danger", "{}", CTX)

        assert called == [], "被拒绝的写操作绝不能执行 handler"

    @pytest.mark.asyncio
    async def test_invalid_arguments_returns_error_not_raise(self) -> None:
        result = await self._broker(_shell_tool()).execute("echo", "{bad json", CTX)

        assert result.status == "error"
        assert result.error_code == "INVALID_ARGUMENTS"

    @pytest.mark.asyncio
    async def test_handler_exception_is_contained(self) -> None:
        def boom(args, ctx):  # type: ignore[no-untyped-def]
            raise RuntimeError("内部数据库路径 /home/user/secret.db")

        result = await self._broker(_shell_tool(handler=boom)).execute("echo", '{"text":"x"}', CTX)

        assert result.status == "error"
        assert result.error_code == "TOOL_CRASHED"
        # 内部细节不得泄漏给模型
        assert "secret.db" not in result.content

    @pytest.mark.asyncio
    async def test_tool_execution_error_message_passes_through(self) -> None:
        def fail(args, ctx):  # type: ignore[no-untyped-def]
            raise ToolExecutionError("没有找到匹配的记忆。")

        result = await self._broker(_shell_tool(handler=fail)).execute("echo", '{"text":"x"}', CTX)

        assert result.error_code == "TOOL_ERROR"
        assert "没有找到匹配的记忆" in result.content

    @pytest.mark.asyncio
    async def test_large_output_is_truncated_but_valid_json(self) -> None:
        big = "x" * (MAX_RESULT_CHARS * 2)

        result = await self._broker(_shell_tool(handler=lambda a, c: {"data": big})).execute(
            "echo", '{"text":"x"}', CTX
        )

        parsed = json.loads(result.content)
        assert parsed["truncated"] is True
        assert len(result.content) < MAX_RESULT_CHARS * 2

    @pytest.mark.asyncio
    async def test_unserializable_output_does_not_crash(self) -> None:
        result = await self._broker(_shell_tool(handler=lambda a, c: object())).execute(
            "echo", '{"text":"x"}', CTX
        )

        # object() 会被 default=str 处理，仍然返回合法 JSON。
        json.loads(result.content)


class ScriptedProvider:
    """按脚本依次返回预设响应的假模型。

    每次调用消费脚本中的一项：字符串表示文本回复，
    列表表示一组工具调用。
    """

    name = "scripted"

    def __init__(self, script: list[object]) -> None:
        self.script = list(script)
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat(self, messages, model, tools=None):  # type: ignore[no-untyped-def]
        self.calls.append(list(messages))
        step = self.script.pop(0) if self.script else "（脚本耗尽）"
        if isinstance(step, str):
            for ch in step:
                yield StreamChunk(delta=ch)
            return
        # 工具调用：分片返回，模拟真实协议（id/name 只在第一片）
        for index, (name, args) in enumerate(step):
            yield StreamChunk(
                tool_call_delta=ToolCallDelta(index=index, id=f"call_{index}", name=name)
            )
            for i in range(0, len(args), 5):
                yield StreamChunk(
                    tool_call_delta=ToolCallDelta(index=index, arguments_fragment=args[i : i + 5])
                )
        yield StreamChunk(finish_reason="tool_calls")


async def _collect_events(provider, broker, tools=None):  # type: ignore[no-untyped-def]
    events = []
    async for event in run_agent_loop(
        provider=provider,
        model="m",
        messages=[ChatMessage("user", "hi")],
        broker=broker,
        tools=tools or [],
        context=CTX,
    ):
        events.append(event)
    return events


def _broker_with(*tools: ToolDefinition) -> ToolBroker:
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    return ToolBroker(registry)


class TestAgentLoop:
    @pytest.mark.asyncio
    async def test_plain_answer_has_no_tool_events(self) -> None:
        events = await _collect_events(ScriptedProvider(["你好"]), _broker_with(_shell_tool()))

        kinds = [e.kind for e in events]
        assert kinds[-1] == "done"
        assert "tool_start" not in kinds
        assert "".join(e.text for e in events) == "你好"

    @pytest.mark.asyncio
    async def test_tool_call_is_executed_then_answered(self) -> None:
        provider = ScriptedProvider([[("echo", '{"text":"hi"}')], "工具说 hi"])

        events = await _collect_events(provider, _broker_with(_shell_tool()))

        kinds = [e.kind for e in events]
        # 结构：先工具调用，再流式文本，最后 done（不写死 delta 条数，
        # 它取决于回复的字符数）。
        assert kinds[0] == "tool_start"
        assert kinds[1] == "tool_end"
        assert kinds[-1] == "done"
        assert set(kinds[2:-1]) == {"delta"}
        assert "".join(e.text for e in events) == "工具说 hi"
        assert events[0].payload["name"] == "echo"
        assert events[1].payload["status"] == "ok"

    @pytest.mark.asyncio
    async def test_tool_result_is_fed_back_as_untrusted(self) -> None:
        provider = ScriptedProvider([[("echo", '{"text":"hi"}')], "完成"])

        await _collect_events(provider, _broker_with(_shell_tool()))

        # 第二次调用模型时，上下文里应包含 tool 消息
        second_call = provider.calls[1]
        tool_messages = [m for m in second_call if m.role == "tool"]
        assert tool_messages, "工具结果必须回填给模型"
        body = json.loads(tool_messages[0].content)
        assert body["trust"] == "untrusted"
        assert body["source"] == "tool"
        assert tool_messages[0].tool_call_id == "call_0"

    @pytest.mark.asyncio
    async def test_assistant_tool_call_is_preserved_in_context(self) -> None:
        """供应商要求：带 tool 消息的上下文必须先有对应的 assistant.tool_calls。"""
        provider = ScriptedProvider([[("echo", '{"text":"hi"}')], "完成"])

        await _collect_events(provider, _broker_with(_shell_tool()))

        second_call = provider.calls[1]
        assistants = [m for m in second_call if m.role == "assistant"]
        assert assistants
        assert assistants[0].tool_calls[0].name == "echo"

    @pytest.mark.asyncio
    async def test_unregistered_tool_is_denied_not_executed(self) -> None:
        provider = ScriptedProvider([[("evil_tool", "{}")], "好的"])
        events = await _collect_events(provider, _broker_with(_shell_tool()))

        tool_end = [e for e in events if e.kind == "tool_end"][0]
        assert tool_end.payload["status"] == "denied"
        assert tool_end.payload["error_code"] == "TOOL_NOT_FOUND"
        # 仍会继续进行下一轮，让模型看到失败原因
        assert events[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_invalid_arguments_do_not_stop_the_loop(self) -> None:
        provider = ScriptedProvider([[("echo", "{bad")], "我改用别的方式回答"])

        events = await _collect_events(provider, _broker_with(_shell_tool()))

        tool_end = [e for e in events if e.kind == "tool_end"][0]
        assert tool_end.payload["error_code"] == "INVALID_ARGUMENTS"
        assert events[-1].kind == "done"

    @pytest.mark.asyncio
    async def test_loop_stops_at_max_rounds(self) -> None:
        """模型一直要求调用工具时必须停止，否则会无限消耗额度。"""
        infinite = [[("echo", '{"text":"x"}')]] * (MAX_TOOL_ROUNDS + 5)
        provider = ScriptedProvider(infinite)

        events = await _collect_events(provider, _broker_with(_shell_tool()))

        assert events[-1].kind == "error"
        assert events[-1].payload["code"] == "TOOL_LOOP_EXCEEDED"
        assert len(provider.calls) == MAX_TOOL_ROUNDS + 1

    @pytest.mark.asyncio
    async def test_parallel_tool_calls_in_one_round(self) -> None:
        provider = ScriptedProvider(
            [
                [("echo", '{"text":"a"}'), ("echo", '{"text":"b"}')],
                "两个都做完了",
            ]
        )

        events = await _collect_events(provider, _broker_with(_shell_tool()))

        starts = [e for e in events if e.kind == "tool_start"]
        assert len(starts) == 2
        assert [e.payload["id"] for e in starts] == ["call_0", "call_1"]

    @pytest.mark.asyncio
    async def test_provider_error_yields_error_event(self) -> None:
        from app.ai import ProviderError

        class FailingProvider:
            name = "bad"

            async def stream_chat(self, messages, model, tools=None) -> AsyncIterator[StreamChunk]:  # type: ignore[no-untyped-def]
                raise ProviderError("模型挂了", code="PROVIDER_UNAVAILABLE")
                yield  # pragma: no cover

        events = await _collect_events(FailingProvider(), _broker_with(_shell_tool()))

        assert events[-1].kind == "error"
        assert events[-1].payload["code"] == "PROVIDER_UNAVAILABLE"


class TestRegistry:
    def test_duplicate_registration_rejected(self) -> None:
        registry = ToolRegistry()
        registry.register(_shell_tool())

        with pytest.raises(ValueError, match="已注册"):
            registry.register(_shell_tool())

    def test_specs_hide_handler(self) -> None:
        registry = ToolRegistry()
        registry.register(_shell_tool())

        spec = registry.specs_for_model()[0]
        assert spec.name == "echo"
        assert not hasattr(spec, "handler"), "提供给模型的声明不能包含可执行函数"


class TestToolInvocationPersistence:
    """工具调用必须可回看（AGENT.md §7.4 执行摘要）。

    实测发现的缺陷：工具记录只存在于一次 SSE 流中，刷新页面就完全丢失。
    """

    def test_tool_invocation_is_stored_and_returned(self, client, session) -> None:
        from app.ai import StreamChunk, ToolCallDelta
        from app.models.tool_invocation import ToolInvocation

        class ToolCallingProvider:
            name = "tc"

            def __init__(self) -> None:
                self.round = 0

            async def stream_chat(self, messages, model, tools=None):  # type: ignore[no-untyped-def]
                self.round += 1
                if self.round == 1:
                    yield StreamChunk(
                        tool_call_delta=ToolCallDelta(
                            index=0, id="c1", name="search_memories", arguments_fragment="{}"
                        )
                    )
                    yield StreamChunk(finish_reason="tool_calls")
                else:
                    for ch in "查完了":
                        yield StreamChunk(delta=ch)

        import app.api.chat as chat_module

        provider = ToolCallingProvider()
        original = chat_module.build_provider
        chat_module.build_provider = lambda s: provider  # type: ignore[assignment]
        try:
            cid = client.post("/api/v1/conversations", json={"title": "t"}).json()["id"]
            client.post("/api/v1/chat/stream", json={"conversation_id": cid, "content": "查一下"})
        finally:
            chat_module.build_provider = original  # type: ignore[assignment]

        rows = session.query(ToolInvocation).filter_by(conversation_id=cid).all()
        assert len(rows) == 1
        assert rows[0].tool_name == "search_memories"
        assert rows[0].status == "ok"

        messages = client.get(f"/api/v1/conversations/{cid}/messages").json()["items"]
        assistant = [m for m in messages if m["role"] == "assistant"][0]
        assert assistant["tools"] == [
            {"tool": "search_memories", "status": "ok", "error_code": None}
        ]

    def test_tool_invocations_deleted_with_conversation(self, client, session) -> None:
        from app.models.tool_invocation import ToolInvocation

        cid = client.post("/api/v1/conversations", json={"title": "t"}).json()["id"]
        session.add(ToolInvocation(conversation_id=cid, tool_name="x", status="ok"))
        session.commit()

        client.delete(f"/api/v1/conversations/{cid}")

        assert session.query(ToolInvocation).count() == 0
