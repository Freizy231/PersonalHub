"""工具定义与注册表（AGENT.md §4.9）。

职责边界：
- Registry 只**登记与描述**工具：名称、说明、参数 Schema、风险级别。
- Registry **不执行**任何工具，也不做权限判断。
- 实际调用一律经过 Tool Broker（app/capabilities/broker.py）。

安全要点（§7.2）：Manifest 声明了参数 Schema 不等于获得权限；
Schema 只用于校验输入形态，是否允许调用由 M6 的 Policy Engine 决定。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from app.ai import ToolSpec


class RiskLevel(StrEnum):
    """工具风险级别。M5 只允许 READ；WRITE 必须等 M6 审批机制就绪。"""

    READ = "READ"
    WRITE = "WRITE"


class ToolExecutionError(RuntimeError):
    """工具执行出错。message 会作为工具结果回给模型，必须安全、不含内部细节。"""


@dataclass(frozen=True, slots=True)
class ToolContext:
    """一次工具调用的执行上下文。

    只包含工具真正需要的信息，不携带数据库会话或任意文件句柄，
    避免工具越权访问（§8.2 最小权限）。
    """

    workspace_id: str
    conversation_id: str


class ToolHandler(Protocol):
    """工具实现协议。

    实现必须是同步的纯业务函数：不直接访问网络、不接受任意路径、
    不执行 Shell。任何副作用型能力都要在 M6 之后单独评审。
    """

    def __call__(self, arguments: dict[str, Any], context: ToolContext) -> Any: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, object]
    risk: RiskLevel
    handler: ToolHandler = field(repr=False)

    def to_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)


class ToolRegistry:
    """进程内工具注册表。按需在 app.main.create_app 时注册。"""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ValueError(f"工具已注册：{definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def specs_for_model(self) -> list[ToolSpec]:
        """提供给模型的工具声明。"""
        return [t.to_spec() for t in self._tools.values()]

    def __len__(self) -> int:
        return len(self._tools)


def validate_arguments(schema: dict[str, object], raw: str) -> dict[str, Any]:
    """校验并解析模型给出的参数。

    模型输出是不可信数据（§8.4）：
    - 非法 JSON 直接拒绝，不做"尽力修复"。
    - 未知字段拒绝（避免模型塞入实现不认识的参数）。
    - 类型不符拒绝。
    这里只做结构校验；语义校验由各工具自己完成。
    """
    try:
        parsed = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError as exc:
        raise ToolExecutionError("参数不是合法的 JSON。") from exc

    if not isinstance(parsed, dict):
        raise ToolExecutionError("参数必须是一个 JSON 对象。")

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        properties = {}

    allowed = set(properties)
    unknown = set(parsed) - allowed
    if unknown:
        raise ToolExecutionError(f"存在不支持的参数：{', '.join(sorted(unknown))}。")

    required = schema.get("required")
    if isinstance(required, list):
        missing = [key for key in required if key not in parsed]
        if missing:
            raise ToolExecutionError(f"缺少必需参数：{', '.join(missing)}。")

    for key, value in parsed.items():
        spec = properties.get(key)
        if not isinstance(spec, dict):
            continue
        expected = spec.get("type")
        if expected is not None and not _matches_type(value, expected):
            raise ToolExecutionError(f"参数 {key} 的类型应为 {expected}。")

    return parsed


def _matches_type(value: Any, expected: object) -> bool:
    """只支持 MVP 用到的几种 JSON Schema 类型。"""
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        # 布尔是 int 的子类，必须排除，否则 true 会被当成 1。
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "array":
        return isinstance(value, list)
    if expected == "object":
        return isinstance(value, dict)
    return True
