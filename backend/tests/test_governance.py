"""治理与审批测试（M6 验收）。

AGENT.md M6 验收标准逐条对应：
1. 未授权操作在执行入口被拒绝
2. 用户批准绑定主体、工具、参数和资源范围
3. 过期或参数被改变的审批不可复用
4. API Key 不进入普通数据库、Prompt 或日志（由既有测试覆盖）
5. 越权测试
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from app.capabilities import RiskLevel, ToolContext, ToolDefinition, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.models.approval import Approval, ApprovalStatus
from app.policy import Decision, PolicyRequest, arguments_fingerprint, evaluate
from app.policy import approval as am

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def _tool(name: str = "echo", risk: RiskLevel = RiskLevel.READ, handler=None):  # type: ignore[no-untyped-def]
    return ToolDefinition(
        name=name,
        description="测试工具",
        parameters={"type": "object", "properties": {"text": {"type": "string"}}},
        risk=risk,
        handler=handler or (lambda args, ctx: {"echo": args.get("text")}),
    )


class TestPolicy:
    def test_read_always_allowed(self) -> None:
        result = evaluate(PolicyRequest("u", "default", _tool(), {"text": "x"}))

        assert result.decision is Decision.ALLOW

    def test_write_requires_approval(self) -> None:
        result = evaluate(PolicyRequest("u", "default", _tool(risk=RiskLevel.WRITE), {}))

        assert result.decision is Decision.REQUIRE_APPROVAL

    def test_session_grant_allows_write(self) -> None:
        result = evaluate(
            PolicyRequest(
                "u", "default", _tool(risk=RiskLevel.WRITE), {}, session_grants=frozenset({"echo"})
            )
        )

        assert result.decision is Decision.ALLOW

    def test_session_grant_is_per_tool(self) -> None:
        """为 A 勾选"不再询问"不能顺带放行 B。"""
        result = evaluate(
            PolicyRequest(
                "u",
                "default",
                _tool(name="other", risk=RiskLevel.WRITE),
                {},
                session_grants=frozenset({"echo"}),
            )
        )

        assert result.decision is Decision.REQUIRE_APPROVAL

    def test_unknown_risk_denied(self) -> None:
        class Weird:
            name = "weird"
            risk = "SOMETHING_ELSE"

        result = evaluate(
            PolicyRequest("u", "default", Weird(), {})  # type: ignore[arg-type]
        )

        assert result.decision is Decision.DENY


class TestFingerprint:
    def test_key_order_does_not_matter(self) -> None:
        a = arguments_fingerprint("t", {"x": 1, "y": 2})
        b = arguments_fingerprint("t", {"y": 2, "x": 1})

        assert a == b

    def test_different_values_differ(self) -> None:
        assert arguments_fingerprint("t", {"x": 1}) != arguments_fingerprint("t", {"x": 2})

    def test_different_tools_differ(self) -> None:
        """同参数不同工具不能共用同一指纹。"""
        assert arguments_fingerprint("a", {"x": 1}) != arguments_fingerprint("b", {"x": 1})

    def test_nested_structures_are_stable(self) -> None:
        args = {"outer": {"b": [1, 2], "a": "中"}}
        assert arguments_fingerprint("t", args) == arguments_fingerprint("t", dict(args))


class TestApprovalLifecycle:
    def _pending(self, session, **kwargs):  # type: ignore[no-untyped-def]
        defaults = dict(
            tool_name="create_task",
            arguments={"title": "学习 TS"},
            principal_id="u1",
            workspace_id="default",
            conversation_id="c" * 32,
        )
        defaults.update(kwargs)
        return am.create_pending(session, **defaults)

    def test_create_is_pending(self, session) -> None:
        approval = self._pending(session)

        assert approval.status == ApprovalStatus.PENDING.value
        assert approval.arguments_fingerprint

    def test_approve_then_consume(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")

        ticket = am.consume_ticket(
            session,
            approval.id,
            tool_name="create_task",
            arguments={"title": "学习 TS"},
            principal_id="u1",
            workspace_id="default",
        )

        assert ticket.approval_id == approval.id

    def test_reject_prevents_consumption(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=False, principal_id="u1")

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "学习 TS"},
                principal_id="u1",
                workspace_id="default",
            )

        assert exc.value.code == "APPROVAL_NOT_APPROVED"

    def test_cannot_decide_twice(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")

        with pytest.raises(am.ApprovalError) as exc:
            am.decide(session, approval.id, approved=False, principal_id="u1")

        assert exc.value.code == "APPROVAL_ALREADY_DECIDED"

    def test_expired_approval_rejected(self, session) -> None:
        approval = self._pending(session, ttl=timedelta(seconds=-1))

        with pytest.raises(am.ApprovalError) as exc:
            am.decide(session, approval.id, approved=True, principal_id="u1")

        assert exc.value.code == "APPROVAL_EXPIRED"

    def test_expiry_applies_even_after_approval(self, session) -> None:
        """批准后放太久也不能用：过期检查独立于状态。"""
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")
        approval.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        session.commit()

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "学习 TS"},
                principal_id="u1",
                workspace_id="default",
            )

        assert exc.value.code == "APPROVAL_EXPIRED"

    def test_one_time_use(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")
        am.consume_ticket(
            session,
            approval.id,
            tool_name="create_task",
            arguments={"title": "学习 TS"},
            principal_id="u1",
            workspace_id="default",
        )

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "学习 TS"},
                principal_id="u1",
                workspace_id="default",
            )

        assert exc.value.code == "APPROVAL_ALREADY_CONSUMED"

    def test_changed_arguments_invalidated(self, session) -> None:
        """核心：批准了 A，不能拿来执行 B。"""
        approval = self._pending(session, arguments={"title": "学习 TS"})
        am.decide(session, approval.id, approved=True, principal_id="u1")

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "删除所有数据"},
                principal_id="u1",
                workspace_id="default",
            )

        assert exc.value.code == "APPROVAL_MISMATCH"

    def test_changed_tool_invalidated(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="danger",
                arguments={"title": "学习 TS"},
                principal_id="u1",
                workspace_id="default",
            )

        assert exc.value.code == "APPROVAL_MISMATCH"

    def test_other_principal_cannot_decide(self, session) -> None:
        approval = self._pending(session)

        with pytest.raises(am.ApprovalError) as exc:
            am.decide(session, approval.id, approved=True, principal_id="attacker")

        assert exc.value.code == "APPROVAL_NOT_FOUND"

    def test_other_workspace_cannot_consume(self, session) -> None:
        approval = self._pending(session)
        am.decide(session, approval.id, approved=True, principal_id="u1")

        with pytest.raises(am.ApprovalError) as exc:
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "学习 TS"},
                principal_id="u1",
                workspace_id="other-workspace",
            )

        assert exc.value.code == "APPROVAL_MISMATCH"

    def test_failed_consumption_does_not_use_up_ticket(self, session) -> None:
        """校验失败不消费：用户可能还需要这张票去执行真正的请求。"""
        approval = self._pending(session, arguments={"title": "学习 TS"})
        am.decide(session, approval.id, approved=True, principal_id="u1")

        with pytest.raises(am.ApprovalError):
            am.consume_ticket(
                session,
                approval.id,
                tool_name="create_task",
                arguments={"title": "别的东西"},
                principal_id="u1",
                workspace_id="default",
            )

        # 用正确参数仍可使用
        ticket = am.consume_ticket(
            session,
            approval.id,
            tool_name="create_task",
            arguments={"title": "学习 TS"},
            principal_id="u1",
            workspace_id="default",
        )
        assert ticket.approval_id == approval.id

    def test_expire_stale_marks_pending(self, session) -> None:
        approval = self._pending(session, ttl=timedelta(seconds=-1))

        count = am.expire_stale(session)

        session.refresh(approval)
        assert count == 1
        assert approval.status == ApprovalStatus.EXPIRED.value


class TestBrokerWithApproval:
    """Broker 必须在**内部**校验票据，不能让 resolver 说了算。"""

    def _broker(self, session, approve: bool, tool: ToolDefinition | None = None):  # type: ignore[no-untyped-def]
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=session.get_bind())
        executed: list[str] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            executed.append("ran")
            return {"ok": True}

        registry = ToolRegistry()
        if tool is None:
            registry.register(_tool(name="create_task", risk=RiskLevel.WRITE, handler=handler))
        else:
            registry.register(_tool(name=tool.name, risk=tool.risk, handler=handler))

        async def resolver(request):  # type: ignore[no-untyped-def]
            if approve:
                am.decide(session, request.approval_id, approved=True, principal_id="local-user")
            return approve

        return (
            ToolBroker(registry, session_factory=factory, approval_resolver=resolver),
            executed,
        )

    @pytest.mark.asyncio
    async def test_approved_write_executes(self, session) -> None:
        broker, executed = self._broker(session, approve=True)

        result = await broker.execute("create_task", '{"text":"x"}', CTX)

        assert result.ok
        assert executed == ["ran"]
        assert session.query(Approval).one().status == ApprovalStatus.APPROVED.value

    @pytest.mark.asyncio
    async def test_rejected_write_does_not_execute(self, session) -> None:
        broker, executed = self._broker(session, approve=False)

        result = await broker.execute("create_task", '{"text":"x"}', CTX)

        assert result.status == "denied"
        assert result.error_code == "APPROVAL_DENIED"
        assert executed == []

    @pytest.mark.asyncio
    async def test_resolver_returning_true_without_deciding_is_rejected(self, session) -> None:
        """安全回归：resolver 只返回 True 而没真正批准时，票据校验必须拦下。

        这正是重构前的漏洞——校验被推给 resolver 就等于没有校验。
        """
        from sqlalchemy.orm import sessionmaker

        executed: list[str] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            executed.append("ran")
            return {}

        registry = ToolRegistry()
        registry.register(_tool(name="create_task", risk=RiskLevel.WRITE, handler=handler))

        async def liar(request):  # type: ignore[no-untyped-def]
            return True  # 声称批准了，但数据库里仍是 PENDING

        broker = ToolBroker(
            registry,
            session_factory=sessionmaker(bind=session.get_bind()),
            approval_resolver=liar,
        )

        result = await broker.execute("create_task", '{"text":"x"}', CTX)

        assert result.status == "denied"
        assert result.error_code == "APPROVAL_NOT_APPROVED"
        assert executed == [], "未经真实批准的写操作绝不能执行"

    @pytest.mark.asyncio
    async def test_read_tool_skips_approval(self, session) -> None:
        broker, executed = self._broker(session, approve=False, tool=_tool(name="peek"))

        result = await broker.execute("peek", '{"text":"x"}', CTX)

        assert result.ok
        assert executed == ["ran"]
        assert session.query(Approval).count() == 0

    @pytest.mark.asyncio
    async def test_session_grant_skips_approval_prompt(self, session) -> None:
        from sqlalchemy.orm import sessionmaker

        calls: list[str] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            calls.append("ran")
            return {}

        registry = ToolRegistry()
        registry.register(_tool(name="create_task", risk=RiskLevel.WRITE, handler=handler))

        async def should_not_be_called(request):  # type: ignore[no-untyped-def]
            calls.append("asked")
            return False

        broker = ToolBroker(
            registry,
            session_factory=sessionmaker(bind=session.get_bind()),
            approval_resolver=should_not_be_called,
            session_grants=frozenset({"create_task"}),
        )

        result = await broker.execute("create_task", '{"text":"x"}', CTX)

        assert result.ok
        assert "asked" not in calls


class TestApprovalApi:
    def test_list_pending(self, client: TestClient, session) -> None:
        am.create_pending(
            session,
            tool_name="create_task",
            arguments={"title": "a"},
            principal_id="local-user",
            workspace_id="default",
            conversation_id=None,
        )

        body = client.get("/api/v1/approvals").json()

        assert body["total"] == 1
        assert body["items"][0]["status"] == "PENDING"

    def test_decide_approves(self, client: TestClient, session) -> None:
        approval = am.create_pending(
            session,
            tool_name="create_task",
            arguments={"title": "a"},
            principal_id="local-user",
            workspace_id="default",
            conversation_id=None,
        )

        body = client.post(
            f"/api/v1/approvals/{approval.id}/decide", json={"decision": "approve"}
        ).json()

        assert body["status"] == "APPROVED"

    def test_decide_rejects(self, client: TestClient, session) -> None:
        approval = am.create_pending(
            session,
            tool_name="create_task",
            arguments={"title": "a"},
            principal_id="local-user",
            workspace_id="default",
            conversation_id=None,
        )

        body = client.post(
            f"/api/v1/approvals/{approval.id}/decide", json={"decision": "reject"}
        ).json()

        assert body["status"] == "DENIED"

    def test_invalid_decision_rejected(self, client: TestClient) -> None:
        assert (
            client.post(
                f"/api/v1/approvals/{'0' * 32}/decide", json={"decision": "maybe"}
            ).status_code
            == 422
        )

    def test_missing_approval_404(self, client: TestClient) -> None:
        assert client.get(f"/api/v1/approvals/{'0' * 32}").status_code == 404

    def test_double_decide_conflicts(self, client: TestClient, session) -> None:
        approval = am.create_pending(
            session,
            tool_name="create_task",
            arguments={"title": "a"},
            principal_id="local-user",
            workspace_id="default",
            conversation_id=None,
        )
        client.post(f"/api/v1/approvals/{approval.id}/decide", json={"decision": "approve"})

        second = client.post(f"/api/v1/approvals/{approval.id}/decide", json={"decision": "reject"})

        assert second.status_code == 409

    def test_preview_does_not_leak_into_logs(self, client: TestClient, session, caplog) -> None:
        import logging

        caplog.set_level(logging.DEBUG)
        am.create_pending(
            session,
            tool_name="create_task",
            arguments={"title": "我的私密计划"},
            principal_id="local-user",
            workspace_id="default",
            conversation_id=None,
        )

        # 摘要应出现在接口返回里（用户需要看到），但不应进入日志
        assert any("我的私密计划" in str(r) for r in caplog.records) is False


class TestTaskTools:
    def test_create_and_list(self, client: TestClient, session) -> None:
        from app.capabilities import ToolContext as Ctx
        from app.capabilities.tools import build_create_task, build_list_tasks

        factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
        ctx = Ctx(workspace_id="default", conversation_id="c" * 32)

        created = build_create_task(factory).handler({"title": "学习 TS"}, ctx)
        listed = build_list_tasks(factory).handler({}, ctx)

        assert created["title"] == "学习 TS"
        assert listed["count"] == 1

    def test_rejects_empty_title(self, session) -> None:
        from app.capabilities import ToolContext as Ctx
        from app.capabilities import ToolExecutionError
        from app.capabilities.tools import build_create_task

        factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
        ctx = Ctx(workspace_id="default", conversation_id="c" * 32)

        with pytest.raises(ToolExecutionError):
            build_create_task(factory).handler({"title": "   "}, ctx)

    def test_workspace_isolation(self, session) -> None:
        from app.capabilities import ToolContext as Ctx
        from app.capabilities.tools import build_create_task, build_list_tasks

        factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
        mine = Ctx(workspace_id="default", conversation_id="c" * 32)
        other = Ctx(workspace_id="other", conversation_id="c" * 32)

        build_create_task(factory).handler({"title": "我的任务"}, mine)

        assert build_list_tasks(factory).handler({}, other)["count"] == 0


class TestDeniedResultTellsModelTruth:
    """实测发现：模型面对"被拒绝"会含糊带过，甚至让用户以为成功。

    工具消息必须明确告知"操作未执行"，并指示模型如实说明。
    """

    def test_denied_message_instructs_not_to_claim_success(self) -> None:
        from app.agent.loop import _tool_message
        from app.capabilities.broker import ToolResult

        result = ToolResult(
            "c1", "create_task", "denied", "用户拒绝了这个操作。", "APPROVAL_DENIED"
        )
        message = _tool_message(result)
        body = json.loads(message.content)

        assert body["status"] == "denied"
        assert "instruction" in body
        assert "不要声称已完成" in body["instruction"]

    def test_error_message_instructs_truthfulness(self) -> None:
        from app.agent.loop import _tool_message
        from app.capabilities.broker import ToolResult

        body = json.loads(_tool_message(ToolResult("c", "t", "error", "x", "TOOL_ERROR")).content)

        assert "不要编造结果" in body["instruction"]

    def test_success_has_no_instruction(self) -> None:
        from app.agent.loop import _tool_message
        from app.capabilities.broker import ToolResult

        body = json.loads(_tool_message(ToolResult("c", "t", "ok", "{}")).content)

        assert "instruction" not in body

    def test_tool_output_still_marked_untrusted(self) -> None:
        from app.agent.loop import _tool_message
        from app.capabilities.broker import ToolResult

        body = json.loads(_tool_message(ToolResult("c", "t", "ok", "恶意内容")).content)

        assert body["trust"] == "untrusted"
        assert body["source"] == "tool"
