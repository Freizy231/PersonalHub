"""Tool Broker（AGENT.md §4.10）。

模型与实际工具之间的**唯一**受控入口。所有调用都必须经过这里，
不允许 Agent 直接调用 Handler。

M6 完整检查链（§4.10 步骤 1–10）：
    1. 工具是否已注册
    2. 调用参数 Schema 校验（不可信输入）
    3. Policy Engine 决策（默认拒绝）
    4. REQUIRE_APPROVAL -> 暂停，等待用户决定
    5. 凭审批票据执行（参数指纹必须一*致）
    6. 输出大小与类型校验
    7. 审计记录

为什么是 async：审批需要"暂停 → 等用户 → 继续"，同步函数无法表达等待。
"""

from __future__ import annotations

import inspect
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.capabilities import (
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolRegistry,
    validate_arguments,
)
from app.policy import Decision, PolicyRequest, evaluate

logger = logging.getLogger(__name__)

# 工具返回给模型的文本上限。过大的结果会挤占上下文并可能拖慢模型，
# 也会让"通过工具塞入大量内容"成为攻击面（§8.4）。
MAX_RESULT_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Broker 的返回结果。status 决定如何回填给模型。"""

    tool_call_id: str
    name: str
    status: str  # ok / error / denied / pending_approval
    content: str
    error_code: str | None = None
    approval_id: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    """需要用户确认时，交给调用方的信息（用于 SSE 展示）。

    同时给出三种粒度，界面按需选用（D-012）：
    - title / summary：给人看的自然语言；
    - preview：多行摘要文本（兼容只显示文本的旧界面）；
    - arguments：原始参数，供"查看原始参数"折叠区核对。
    """

    approval_id: str
    tool_name: str
    preview: str
    expires_in_seconds: int
    title: str = ""
    summary: tuple[tuple[str, str], ...] = ()
    arguments: dict[str, Any] = field(default_factory=dict)


# 调用方提供：给定审批信息，等待用户决定，返回是否批准。
# 由 API 层实现（阻塞式交互）：发 SSE、等待 HTTP 决策、超时处理。
#
# 注意：这个 resolver **只负责等用户**，不负责校验票据。
# 票据校验（指纹/过期/一次性消费）留在 Broker 内，见 _execute_with_approval。
# 否则任何 resolver 只要返回 True 就能执行写操作，审批机制会被架空。
ApprovalResolver = Callable[[ApprovalRequest], Awaitable[bool]]


class ToolBroker:
    def __init__(
        self,
        registry: ToolRegistry,
        *,
        session_factory=None,  # type: ignore[no-untyped-def]
        approval_resolver: ApprovalResolver | None = None,
        session_grants: frozenset[str] = frozenset(),
    ) -> None:
        self._registry = registry
        self._session_factory = session_factory
        self._approval_resolver = approval_resolver
        self._session_grants = session_grants

    def definition_of(self, name: str) -> ToolDefinition | None:
        """按名字取工具定义（用于展示中文名等元数据，不授予任何执行能力）。"""
        return self._registry.get(name)

    async def execute(
        self,
        name: str,
        raw_arguments: str,
        context: ToolContext,
        *,
        call_id: str = "",
        principal_id: str = "local-user",
    ) -> ToolResult:
        """执行一次工具调用。**任何**失败都返回结构化结果，不向模型抛异常。

        为什么不抛异常：模型需要看到"这个调用失败了以及为什么"，
        才能决定换个参数重试或改用别的回答方式。
        """
        definition = self._registry.get(name)
        if definition is None:
            return self._result(
                name, call_id, "denied", "TOOL_NOT_FOUND", f"没有名为 {name} 的工具。"
            )

        try:
            arguments = validate_arguments(definition.parameters, raw_arguments)
        except ToolExecutionError as exc:
            return self._result(name, call_id, "error", "INVALID_ARGUMENTS", str(exc))

        # Policy 决策在参数校验之后：未通过校验的请求不值得打扰用户审批。
        decision = evaluate(
            PolicyRequest(
                principal_id=principal_id,
                workspace_id=context.workspace_id,
                tool=definition,
                arguments=arguments,
                session_grants=self._session_grants,
            )
        )

        if decision.decision is Decision.DENY:
            logger.warning("策略拒绝工具调用", extra={"tool": name, "reason": decision.reason})
            return self._result(name, call_id, "denied", "POLICY_DENIED", decision.reason)

        if decision.decision is Decision.REQUIRE_APPROVAL:
            if self._approval_resolver is None or self._session_factory is None:
                # 没有审批通道时绝不能"因为没有通道所以放行"（默认拒绝）。
                return self._result(
                    name,
                    call_id,
                    "denied",
                    "APPROVAL_UNAVAILABLE",
                    "该操作需要用户确认，但当前没有可用的确认通道。",
                )
            return await self._execute_with_approval(
                definition, name, call_id, arguments, context, principal_id
            )

        return await self._run(definition, name, call_id, arguments, context)

    async def _execute_with_approval(
        self,
        definition: ToolDefinition,
        name: str,
        call_id: str,
        arguments: dict[str, Any],
        context: ToolContext,
        principal_id: str,
    ) -> ToolResult:
        """创建审批 -> 等用户 -> **在 Broker 内校验并消费票据** -> 执行。

        票据校验必须在这里完成（而不是交给 resolver），
        否则审批的绑定语义（工具 + 参数指纹 + 主体 + 工作空间 + 过期 + 一次性）
        会被调用方绕过。
        """
        from app.policy.approval import (
            DEFAULT_TTL,
            ApprovalError,
            consume_ticket,
            create_pending,
        )

        assert self._approval_resolver is not None
        assert self._session_factory is not None

        with self._session_factory() as session:
            approval = create_pending(
                session,
                tool_name=name,
                arguments=arguments,
                principal_id=principal_id,
                workspace_id=context.workspace_id,
                conversation_id=context.conversation_id,
                definition=definition,
            )
            approval_id = approval.id
            preview = approval.arguments_preview

        approved = await self._approval_resolver(
            ApprovalRequest(
                approval_id=approval_id,
                tool_name=name,
                preview=preview,
                expires_in_seconds=int(DEFAULT_TTL.total_seconds()),
                title=definition.title,
                summary=tuple(definition.summarize_arguments(arguments)),
                arguments=arguments,
            )
        )

        if not approved:
            logger.info("用户拒绝工具调用", extra={"tool": name, "approval_id": approval_id})
            return self._result(name, call_id, "denied", "APPROVAL_DENIED", "用户拒绝了这个操作。")

        # 关键：无论 resolver 返回什么，都必须再校验一次票据。
        # 这样即便 resolver 被误实现或状态在等待期间被并发改写，也不会有漏洞。
        with self._session_factory() as session:
            try:
                consume_ticket(
                    session,
                    approval_id,
                    tool_name=name,
                    arguments=arguments,
                    principal_id=principal_id,
                    workspace_id=context.workspace_id,
                )
            except ApprovalError as exc:
                logger.warning(
                    "审批票据校验失败",
                    extra={"tool": name, "approval_id": approval_id, "code": exc.code},
                )
                return self._result(name, call_id, "denied", exc.code, str(exc))

        return await self._run(definition, name, call_id, arguments, context)

    async def _run(
        self,
        definition: ToolDefinition,
        name: str,
        call_id: str,
        arguments: dict[str, Any],
        context: ToolContext,
    ) -> ToolResult:
        """执行工具。同步 handler 直接调用，异步 handler（MCP）await。

        为什么在这里区分：Broker 是唯一执行入口（§4.10），
        外部能力的执行也必须经这唯一入口，而不是另开一条绕过校验的路径。
        """
        started = time.monotonic()
        try:
            output = definition.handler(arguments, context)
            if inspect.isawaitable(output):
                output = await output
        except ToolExecutionError as exc:
            return self._result(name, call_id, "error", "TOOL_ERROR", str(exc))
        except Exception:
            # 工具内部异常不暴露给模型（可能含内部结构）。
            logger.exception("工具执行异常", extra={"tool": name})
            return self._result(name, call_id, "error", "TOOL_CRASHED", "工具执行失败。")
        finally:
            logger.info(
                "工具调用",
                extra={
                    "tool": name,
                    "risk": definition.risk.value,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )

        return self._result(
            name,
            call_id,
            "ok",
            None,
            _encode_output(output, limit=definition.max_result_chars or MAX_RESULT_CHARS),
        )

    def _result(
        self, name: str, call_id: str, status: str, code: str | None, content: str
    ) -> ToolResult:
        return ToolResult(call_id, name, status, content, error_code=code)


def _encode_output(output: Any, *, limit: int = MAX_RESULT_CHARS) -> str:
    """把工具输出编码为字符串，并施加大小上限。

    必须是 JSON：模型更容易正确解读结构化结果，也便于我们断言其形态。

    `limit` 由工具声明：绝大多数工具用统一上限，防止"通过工具塞入大量内容"
    （§8.4）；`read_app` 这类**必须把完整内容交给模型**的工具单独放宽，
    否则模型只能看到被截断的源码，改出来的东西一定是错的。
    """
    try:
        text = json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "工具返回了无法序列化的结果"}, ensure_ascii=False)

    if len(text) > limit:
        # 截断时保持 JSON 合法，避免模型收到半截结构而解析失败。
        return json.dumps(
            {
                "truncated": True,
                "note": f"结果超过 {limit} 字符已截断",
                "preview": text[:limit],
            },
            ensure_ascii=False,
        )
    return text
