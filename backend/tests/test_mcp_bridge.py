"""MCP bridge 测试（M8 第 5 项）。

用**假 client** 而不是真实子进程：这些测试要验证的是"外部工具是否被
正确地纳入既有安全链路"，不需要真起一个 MCP Server（那属于集成验证）。
真 Server 的端到端验证记在 docs/ROADMAP.md。
"""

from __future__ import annotations

import pytest

from app.capabilities import RiskLevel, ToolContext, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.mcp.bridge import (
    UNTRUSTED_NOTICE,
    discover_and_register,
    manifest_for_mcp_tool,
)
from app.mcp.client import McpCallError, McpCallResult, McpToolInfo
from app.mcp.config import McpServerConfig

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def _server(name: str = "fake", **overrides) -> McpServerConfig:  # type: ignore[no-untyped-def]
    data = {"name": name, "transport": "stdio", "command": "npx", "enabled": True}
    data.update(overrides)
    return McpServerConfig(**data)


def _tool(name: str = "echo", server: str = "fake", **overrides) -> McpToolInfo:  # type: ignore[no-untyped-def]
    data = {
        "server": server,
        "name": name,
        "description": f"{name} 的描述",
        "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
    }
    data.update(overrides)
    return McpToolInfo(**data)


class FakeClient:
    """可编程的假 MCP client。"""

    def __init__(self, tools=None, error=None, call_result=None, call_error=None):  # type: ignore[no-untyped-def]
        self.tools = tools if tools is not None else [_tool()]
        self.error = error
        self.call_result = call_result or McpCallResult(
            text="结果", is_error=False, truncated=False
        )
        self.call_error = call_error
        self.calls: list[tuple[str, str, dict]] = []

    async def list_tools(self, server):  # type: ignore[no-untyped-def]
        if self.error is not None:
            raise self.error
        return self.tools

    async def call_tool(self, server, tool_name, arguments):  # type: ignore[no-untyped-def]
        self.calls.append((server.name, tool_name, arguments))
        if self.call_error is not None:
            raise self.call_error
        return self.call_result


class TestDiscovery:
    @pytest.mark.asyncio
    async def test_registers_tools_with_prefix(self) -> None:
        registry = ToolRegistry()
        client = FakeClient()

        result, discovered = await discover_and_register(
            client=client,
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )

        assert result.registered == ("mcp__fake__echo",)
        assert registry.get("mcp__fake__echo") is not None
        assert discovered[0][0].name == "fake"

    @pytest.mark.asyncio
    async def test_unreachable_server_does_not_block_others(self) -> None:
        """一个 Server 连不上，不影响其他 Server，也不影响内建工具。"""
        registry = ToolRegistry()
        registry.register(_builtin())

        class PartialClient(FakeClient):
            async def list_tools(self, server):  # type: ignore[no-untyped-def]
                if server.name == "broken":
                    raise McpCallError("连不上", code="MCP_UNAVAILABLE")
                return [_tool(server=server.name)]

        result, discovered = await discover_and_register(
            client=PartialClient(),  # type: ignore[arg-type]
            servers=(_server("broken"), _server("good")),
            registry=registry,
        )

        assert result.registered == ("mcp__good__echo",)
        assert len(result.skipped) == 1
        assert "broken" in result.skipped[0][0]
        assert registry.get("builtin.peek") is not None

    @pytest.mark.asyncio
    async def test_name_conflict_is_skipped_not_overwritten(self) -> None:
        """重名时必须拒绝登记并报告，绝不静默覆盖。"""
        registry = ToolRegistry()
        registry.register(_builtin(name="mcp__fake__echo"))

        result, _ = await discover_and_register(
            client=FakeClient(),
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )

        assert result.registered == ()
        assert result.skipped
        assert "已被占用" in result.skipped[0][1]


class TestSecurityProperties:
    """这些断言是整个 MCP 接入的安全底线。"""

    @pytest.mark.asyncio
    async def test_every_mcp_tool_is_write_regardless_of_server_claims(self) -> None:
        """Server 自称只读也不影响本地决策（提案 §4 信任模型）。"""
        registry = ToolRegistry()
        read_only_claim = _tool()
        await discover_and_register(
            client=FakeClient(tools=[read_only_claim]),  # type: ignore[arg-type]
            servers=(_server(),),
            registry=registry,
        )

        definition = registry.get("mcp__fake__echo")
        assert definition is not None
        assert definition.risk is RiskLevel.WRITE

    @pytest.mark.asyncio
    async def test_description_marks_untrusted_source(self) -> None:
        registry = ToolRegistry()
        await discover_and_register(
            client=FakeClient(),
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )

        description = registry.get("mcp__fake__echo").description  # type: ignore[union-attr]
        assert description.startswith(UNTRUSTED_NOTICE)
        assert "fake" in description

    @pytest.mark.asyncio
    async def test_description_is_truncated(self) -> None:
        registry = ToolRegistry()
        await discover_and_register(
            client=FakeClient(tools=[_tool(description="长" * 5000)]),  # type: ignore[arg-type]
            servers=(_server(),),
            registry=registry,
        )

        description = registry.get("mcp__fake__echo").description  # type: ignore[union-attr]
        assert "描述已截断" in description
        assert len(description) < 2_000

    @pytest.mark.asyncio
    async def test_write_tool_requires_approval_through_broker(self, session) -> None:
        """核心：MCP 工具必须经审批，不能因为"是外部的"就放行。"""

        registry = ToolRegistry()
        await discover_and_register(
            client=FakeClient(),
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )
        broker = ToolBroker(registry)  # 无审批通道

        result = await broker.execute("mcp__fake__echo", '{"text":"x"}', CTX)

        assert result.status == "denied"
        assert result.error_code == "APPROVAL_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_approved_call_executes(self, session) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.policy import approval as am

        factory = sessionmaker(bind=session.get_bind())
        registry = ToolRegistry()
        client = FakeClient(call_result=McpCallResult(text="回声", is_error=False, truncated=False))
        await discover_and_register(
            client=client,
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )

        async def resolver(request):  # type: ignore[no-untyped-def]
            am.decide(session, request.approval_id, approved=True, principal_id="local-user")
            return True

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("mcp__fake__echo", '{"text":"你好"}', CTX)

        assert result.ok
        assert client.calls == [("fake", "echo", {"text": "你好"})]

    @pytest.mark.asyncio
    async def test_call_failure_becomes_structured_error(self, session) -> None:
        """调用失败不抛异常：模型需要看到原因才能改口如实告知用户。"""
        from sqlalchemy.orm import sessionmaker

        from app.policy import approval as am

        factory = sessionmaker(bind=session.get_bind())
        registry = ToolRegistry()
        await discover_and_register(
            client=FakeClient(call_error=McpCallError("超时", code="MCP_TIMEOUT")),  # type: ignore[arg-type]
            servers=(_server(),),
            registry=registry,
        )

        async def resolver(request):  # type: ignore[no-untyped-def]
            am.decide(session, request.approval_id, approved=True, principal_id="local-user")
            return True

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("mcp__fake__echo", '{"text":"x"}', CTX)

        assert result.ok  # Broker 层面成功（工具返回了结构化结果）
        assert "MCP_TIMEOUT" in result.content

    @pytest.mark.asyncio
    async def test_arguments_validated_against_server_schema(self) -> None:
        """Server 的 inputSchema 参与校验；不合规参数在到达 Server 前就被拒绝。"""

        registry = ToolRegistry()
        client = FakeClient(
            tools=[
                _tool(
                    input_schema={
                        "type": "object",
                        "properties": {"text": {"type": "string"}},
                        "required": ["text"],
                    }
                )
            ]
        )
        await discover_and_register(
            client=client,
            servers=(_server(),),
            registry=registry,  # type: ignore[arg-type]
        )
        broker = ToolBroker(registry)

        # 缺必需参数：在参数校验阶段就失败，不会进入审批流程。
        result = await broker.execute("mcp__fake__echo", "{}", CTX)

        assert result.status == "error"
        assert result.error_code == "INVALID_ARGUMENTS"
        assert client.calls == []


def _builtin(name: str = "builtin.peek"):  # type: ignore[no-untyped-def]
    from app.capabilities import ToolDefinition

    return ToolDefinition(
        name=name,
        description="内建",
        parameters={"type": "object", "properties": {}},
        risk=RiskLevel.READ,
        handler=lambda args, ctx: {},
    )


class TestManifest:
    def test_manifest_identifies_server_and_tool(self) -> None:
        manifest = manifest_for_mcp_tool(_tool(), _server())

        assert manifest.id == "mcp.fake.echo"
        assert manifest.type.value == "mcp"
        assert manifest.runtime.value == "mcp"
        # 声明需要审批（声明本身不等于授权）。
        assert manifest.permissions == ("mcp:fake:echo:write",)

    def test_manifest_description_marks_untrusted(self) -> None:
        manifest = manifest_for_mcp_tool(_tool(), _server())

        assert manifest.description.startswith(UNTRUSTED_NOTICE)
