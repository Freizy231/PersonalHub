"""create_task 的来源追溯（§5.2：任务状态与创建必须可追踪）。

Broker 只在**票据校验通过后**把审批 id 放进 ToolContext，因此：

- 经审批创建的任务带 `created_via_approval_id`；
- 直接调用 handler（绕过审批）时为 None；
- 票据校验失败时任务**根本不会创建**（M6 的核心保证，这里一并回归）。
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.capabilities import RiskLevel, ToolContext, ToolDefinition, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.capabilities.tools import build_create_task
from app.policy import approval as approval_manager

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def _factory(session: Session) -> sessionmaker:  # type: ignore[type-arg]
    return sessionmaker(bind=session.get_bind())


class TestTaskProvenance:
    @pytest.mark.asyncio
    async def test_approved_task_records_approval_id(self, session: Session) -> None:
        factory = _factory(session)
        tool = build_create_task(factory)
        registry = ToolRegistry()
        registry.register(tool)

        seen: list[str] = []

        async def resolver(request):  # type: ignore[no-untyped-def]
            seen.append(request.approval_id)
            with factory() as decision_session:
                approval_manager.decide(
                    decision_session,
                    request.approval_id,
                    approved=True,
                    principal_id="local-user",
                )
            return True

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("create_task", '{"title": "写周报"}', CTX)
        assert result.ok, result.error_code

        from app.models.task import Task

        task = session.query(Task).one()
        assert task.title == "写周报"
        # 来源必须指向真正被消费的那张审批记录。
        assert task.created_via_approval_id == seen[0]
        assert task.created_via_approval_id is not None

    @pytest.mark.asyncio
    async def test_denied_task_is_not_created(self, session: Session) -> None:
        factory = _factory(session)
        registry = ToolRegistry()
        registry.register(build_create_task(factory))

        async def resolver(request):  # type: ignore[no-untyped-def]
            with factory() as decision_session:
                approval_manager.decide(
                    decision_session,
                    request.approval_id,
                    approved=False,
                    principal_id="local-user",
                )
            return False

        broker = ToolBroker(registry, session_factory=factory, approval_resolver=resolver)
        result = await broker.execute("create_task", '{"title": "不该存在"}', CTX)

        assert result.status == "denied"
        from app.models.task import Task

        assert session.query(Task).count() == 0

    def test_direct_handler_call_has_no_provenance(self, session: Session) -> None:
        """绕过审批直接调用 handler：来源为空，且这条路径在对话中不存在。"""
        factory = _factory(session)
        definition: ToolDefinition = build_create_task(factory)
        definition.handler({"title": "直接创建"}, CTX)

        from app.models.task import Task

        task = session.query(Task).one()
        assert task.created_via_approval_id is None

    def test_create_task_is_write_risk(self) -> None:
        definition = build_create_task(None)  # type: ignore[arg-type]
        assert definition.risk is RiskLevel.WRITE
