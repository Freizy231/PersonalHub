"""Tool Broker（AGENT.md §4.10）。

模型与实际工具之间的**唯一**受控入口。所有调用都必须经过这里，
不允许 Agent 直接调用 Handler。

M5 实现的检查链（§4.10 步骤 1–3、8–9）：
    1. 工具是否已注册
    2. 调用参数 Schema 校验（不可信输入）
    3. 风险级别：M5 只放行 READ；WRITE 一律拒绝（等 M6 审批机制）
    4. 执行
    5. 输出大小与类型校验
    6. 审计记录

Policy Engine 与 Approval Manager 属于 M6；届时在第 3 步与执行之间插入。
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.capabilities import (
    RiskLevel,
    ToolContext,
    ToolExecutionError,
    ToolRegistry,
    validate_arguments,
)

logger = logging.getLogger(__name__)

# 工具返回给模型的文本上限。过大的结果会挤占上下文并可能拖慢模型，
# 也会让"通过工具塞入大量内容"成为攻击面（§8.4）。
MAX_RESULT_CHARS = 8_000


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Broker 的返回结果。status 决定如何回填给模型。"""

    tool_call_id: str
    name: str
    status: str  # ok / error / denied
    content: str
    error_code: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"


class ToolBroker:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def execute(self, name: str, raw_arguments: str, context: ToolContext) -> ToolResult:
        """执行一次工具调用。**任何**失败都返回结构化结果，不向模型抛异常。

        为什么不抛异常：模型需要看到"这个调用失败了以及为什么"，
        才能决定换个参数重试或改用别的回答方式。
        """
        tool_call_id = ""  # 由调用方在包装时填入

        definition = self._registry.get(name)
        if definition is None:
            return self._denied(name, tool_call_id, "TOOL_NOT_FOUND", f"没有名为 {name} 的工具。")

        if definition.risk is RiskLevel.WRITE:
            # M5 绝不执行写操作：审批机制尚未就绪（§2.4、§4.10 第 5 步）。
            logger.warning("拒绝执行写操作工具", extra={"tool": name})
            return self._denied(
                name,
                tool_call_id,
                "WRITE_NOT_ALLOWED",
                "写操作需要用户审批，当前版本尚不支持。",
            )

        try:
            arguments = validate_arguments(definition.parameters, raw_arguments)
        except ToolExecutionError as exc:
            return self._error(name, tool_call_id, "INVALID_ARGUMENTS", str(exc))

        started = time.monotonic()
        try:
            output = definition.handler(arguments, context)
        except ToolExecutionError as exc:
            return self._error(name, tool_call_id, "TOOL_ERROR", str(exc))
        except Exception:
            # 工具内部异常不暴露给模型（可能含内部结构）。
            logger.exception("工具执行异常", extra={"tool": name})
            return self._error(name, tool_call_id, "TOOL_CRASHED", "工具执行失败。")
        finally:
            logger.info(
                "工具调用",
                extra={
                    "tool": name,
                    "duration_ms": round((time.monotonic() - started) * 1000, 1),
                },
            )

        return ToolResult(
            tool_call_id=tool_call_id,
            name=name,
            status="ok",
            content=_encode_output(output),
        )

    def _denied(self, name: str, call_id: str, code: str, message: str) -> ToolResult:
        return ToolResult(call_id, name, "denied", message, error_code=code)

    def _error(self, name: str, call_id: str, code: str, message: str) -> ToolResult:
        return ToolResult(call_id, name, "error", message, error_code=code)


def _encode_output(output: Any) -> str:
    """把工具输出编码为字符串，并施加大小上限。

    必须是 JSON：模型更容易正确解读结构化结果，也便于我们断言其形态。
    """
    try:
        text = json.dumps(output, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "工具返回了无法序列化的结果"}, ensure_ascii=False)

    if len(text) > MAX_RESULT_CHARS:
        # 截断时保持 JSON 合法，避免模型收到半截结构而解析失败。
        return json.dumps(
            {
                "truncated": True,
                "note": f"结果超过 {MAX_RESULT_CHARS} 字符已截断",
                "preview": text[:MAX_RESULT_CHARS],
            },
            ensure_ascii=False,
        )
    return text
