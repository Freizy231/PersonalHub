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
from collections.abc import Awaitable
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

    M8 起，外部能力（MCP）通过 `AsyncToolHandler` 接入，二者由 Broker
    分别处理；本协议保持不变，避免所有内建工具被迫改成 async。
    """

    def __call__(self, arguments: dict[str, Any], context: ToolContext) -> Any: ...


class AsyncToolHandler(Protocol):
    """异步工具实现协议（M8，供 MCP 等需要 IO 的外部能力使用）。

    为什么单独定义而不是把同步协议改成 async：MCP 调用必须 await
    （它在等外部进程/网络），而内建工具全是纯同步逻辑。让两者并存，
    内建工具保持简单可测，外部能力也有正确的位置。
    Broker 负责区分：同步 handler 直接调用，异步 handler 用 await。
    """

    def __call__(self, arguments: dict[str, Any], context: ToolContext) -> Awaitable[Any]: ...


@dataclass(frozen=True, slots=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, object]
    risk: RiskLevel
    handler: ToolHandler = field(repr=False)
    # 给用户看的工具名（"创建任务"）。审批卡片用它，而不是内部标识 create_task。
    display_name: str = ""
    # 参数名 -> 中文标签。审批摘要据此输出"标题：…"，而不是 JSON 键值对。
    argument_labels: tuple[tuple[str, str], ...] = ()

    def to_spec(self) -> ToolSpec:
        return ToolSpec(name=self.name, description=self.description, parameters=self.parameters)

    @property
    def title(self) -> str:
        """面向用户的名称。没声明时退回内部名称（仍然不是 JSON）。"""
        return self.display_name or self.name

    def summarize_arguments(self, arguments: dict[str, Any]) -> list[tuple[str, str]]:
        """把参数翻译成「标签 + 值」列表，供审批界面展示。

        刻意不输出 JSON 结构：审批是给人看的，`{"title": "..."}` 只会让用户
        去解析括号和引号（见 docs/DECISIONS.md D-012）。
        未知参数用原始键名兜底，保证将来新增工具时不会退化成 JSON。
        """
        labels = dict(self.argument_labels)
        summary: list[tuple[str, str]] = []
        for key, value in arguments.items():
            text = humanize_value(value)
            if text:
                summary.append((labels.get(key, key), text))
        return summary


def humanize_value(value: Any, *, limit: int = 200) -> str:
    """把任意参数值转成可读的一行文本。

    - 列表用顿号连接（"a、b、c"），而不是 `["a","b"]`。
    - 布尔显示为是/否，None 省略。
    - 过长的文本截断并加省略号，避免一条描述挤满整张卡片。
    """
    if value is None:
        return ""
    if isinstance(value, bool):
        text = "是" if value else "否"
    elif isinstance(value, str):
        text = value.strip()
    elif isinstance(value, (list, tuple)):
        parts = [humanize_value(item, limit=limit) for item in value]
        text = "、".join(part for part in parts if part)
    elif isinstance(value, dict):
        pairs = [
            f"{key}={humanize_value(item, limit=limit)}" for key, item in value.items() if item
        ]
        text = "；".join(pairs)
    else:
        text = str(value)

    if len(text) > limit:
        text = text[:limit].rstrip() + "…"
    return text


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

    def remove(self, name: str) -> bool:
        """移除一个已注册工具（M8：禁用插件时使用）。返回是否真的移除了。

        Registry 只负责登记，这里不判断调用方是否有权移除；
        权限由 API 层控制的插件状态决定。
        """
        return self._tools.pop(name, None) is not None

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
