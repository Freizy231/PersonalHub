"""审批卡片的"会发生什么"（`ToolDefinition.impact`，迭代 2 第 2 项）。

约束：
1. `impact` 是**说明**，不参与任何权限判断——有测试锁定"没有 impact 照常审批"；
2. 内置写工具必须声明 impact（否则用户不知道自己在批准什么）；
3. 外部能力（MCP / 进程插件）的 impact 必须写明"不受本系统控制"，
   不能让审批卡片看起来像本地操作。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.capabilities import RiskLevel, ToolContext, ToolDefinition, ToolRegistry
from app.capabilities.broker import ApprovalRequest, ToolBroker
from app.capabilities.tools import build_create_task
from app.policy import approval as approval_manager

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


@pytest.fixture()
def factory(session: Session) -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(bind=session.get_bind())


class TestImpactReachesResolver:
    @pytest.mark.asyncio
    async def test_create_task_impact_is_sent(self, session: Session, factory) -> None:
        registry = ToolRegistry()
        registry.register(build_create_task(factory))
        seen: list[ApprovalRequest] = []

        async def resolver(request: ApprovalRequest) -> bool:
            seen.append(request)
            with factory() as decision_session:
                approval_manager.decide(
                    decision_session,
                    request.approval_id,
                    approved=False,
                    principal_id="local-user",
                )
            return False

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        await broker.execute("create_task", '{"title": "写周报"}', CTX)

        assert seen
        assert "「我的任务」" in seen[0].impact
        assert seen[0].title == "创建任务"

    @pytest.mark.asyncio
    async def test_tool_without_impact_still_requires_approval(
        self, session: Session, factory
    ) -> None:
        """impact 只是文案：没声明它也不会跳过审批（权限与文案彻底分开）。"""
        registry = ToolRegistry()

        def handler(arguments, context):  # type: ignore[no-untyped-def]
            return {"ok": True}

        registry.register(
            ToolDefinition(
                name="silent_write",
                description="没有声明影响的写工具",
                parameters={"type": "object", "properties": {}, "required": []},
                risk=RiskLevel.WRITE,
                handler=handler,
            )
        )
        seen: list[ApprovalRequest] = []

        async def resolver(request: ApprovalRequest) -> bool:
            seen.append(request)
            return False

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("silent_write", "{}", CTX)

        assert len(seen) == 1
        assert seen[0].impact == ""
        assert result.status == "denied"

    @pytest.mark.asyncio
    async def test_read_tool_never_produces_approval(self, session: Session, factory) -> None:
        registry = ToolRegistry()

        def handler(arguments, context):  # type: ignore[no-untyped-def]
            return {"ok": True}

        registry.register(
            ToolDefinition(
                name="quiet_read",
                description="只读",
                parameters={"type": "object", "properties": {}, "required": []},
                risk=RiskLevel.READ,
                handler=handler,
                impact="这段文案不应该出现，因为只读工具不需要确认。",
            )
        )

        async def resolver(request: ApprovalRequest) -> bool:  # pragma: no cover - 不应被调用
            raise AssertionError("只读工具不该触发审批")

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("quiet_read", "{}", CTX)

        assert result.ok


class TestBuiltinImpactsAreHumanReadable:
    def test_create_task_declares_impact(self) -> None:
        definition = build_create_task(None)  # type: ignore[arg-type]

        assert definition.impact
        # 不出现内部标识或 JSON 片段。
        assert "create_task" not in definition.impact
        assert "{" not in definition.impact

    def test_app_modification_says_version_does_not_change_yet(self) -> None:
        """D-027 的教训：不说清楚，模型与用户都会以为改完就生效了。"""
        from app.builder.staging import AppStore
        from app.capabilities.app_tools import build_propose_app_change

        definition = build_propose_app_change(AppStore(root=__import__("pathlib").Path("/tmp")))  # type: ignore[arg-type]

        assert "不会立刻改变" in definition.impact
        assert "安装" in definition.impact

    def test_process_plugin_impact_mentions_sandbox(self) -> None:
        from app.plugins.declarative import build_process_tool
        from app.plugins.manifest import parse_manifest

        manifest = parse_manifest(
            {
                "id": "com.example.echo",
                "name": "回声",
                "version": "1.0.0",
                "type": "process",
                "description": "回显",
                "entrypoint": "main.py",
                "limits": {"memory_mb": 128},
                "tools": [
                    {
                        "name": "echo",
                        "description": "回显",
                        "input_schema": {"type": "object", "properties": {}},
                    }
                ],
            }
        )
        definition = build_process_tool(
            manifest, manifest.tools[0], plugin_dir=__import__("pathlib").Path("/tmp")
        )

        assert "沙箱" in definition.impact
        assert "网络" in definition.impact
