"""声明式插件：把内建工具组合成一个新工具（M8 第 6 项，提案第 3 步）。

插件不含代码，只能声明"我的这个工具要依次调用哪些内建工具"。

**最关键的安全问题**：如果插件把三个写操作打包成一个工具，
而审批只发生一次，用户就批准了自己没看清的东西——
审批卡片的摘要只会显示插件工具的输入参数，看不到内部会创建三条任务。

本模块的处理方式：

- 组合工具的**风险级别 = 所组合工具中最高的一级**。只要有一个 WRITE，
  整个工具就是 WRITE，需要审批（这也与 MCP 的决定一致）。
- 组合工具被批准后，**每个内部写操作仍然各自经过 Broker 的完整检查**。
  也就是说：如果用户只批准了外层，内部每个写操作仍会各自触发审批。
- 这正是"不新增绕过路径"的具体含义。

**输入参数如何传给内部工具**：声明式插件没有自己的逻辑，因此采用
"参数原样透传"——外层参数同时传给每个被组合的工具，每个工具只取
自己在 Schema 里声明的字段。这样行为可预测，也便于用户理解。
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any

from app.capabilities import (
    RiskLevel,
    ToolContext,
    ToolDefinition,
    ToolExecutionError,
    ToolRegistry,
)
from app.capabilities.broker import ToolBroker, ToolResult
from app.capabilities.manifest import (
    CapabilityManifest,
    CapabilityRuntime,
    CapabilitySource,
    CapabilityType,
)
from app.plugins.manifest import PLUGIN_TOOL_PREFIX, PluginManifest, PluginToolSpec

logger = logging.getLogger(__name__)

# 组合调用的输出上限，与内建工具一致。
MAX_COMPOSED_RESULT_CHARS = 8_000

# 当前请求的 Broker。
#
# 为什么需要它：插件工具在**启动时**登记，但审批通道是**每个请求**不同的
# （见 api/chat.py 的 approval_resolver）。如果插件工具在登记时就绑定某个
# Broker，它会一直用那个（可能是 None）审批通道，导致内部写操作无法走审批。
# 用 contextvar 在请求开始时设置当前 Broker，插件工具执行时读取，
# 就自然拿到了本请求的审批通道。
#
# ContextVar 而不是全局变量：并发请求各有各的 Broker，不能互相串用。
_current_broker: contextvars.ContextVar[ToolBroker | None] = contextvars.ContextVar(
    "plugin_current_broker", default=None
)


def set_current_broker(broker: ToolBroker) -> contextvars.Token:
    """在请求开始时调用。返回 token，用于请求结束后恢复。"""
    return _current_broker.set(broker)


def reset_current_broker(token: contextvars.Token) -> None:  # type: ignore[type-arg]
    _current_broker.reset(token)


@dataclass(frozen=True, slots=True)
class CompositionResult:
    registered: tuple[str, ...]
    skipped: tuple[tuple[str, str], ...]

    @property
    def ok(self) -> bool:
        return bool(self.registered)


def _highest_risk(dependencies: list[ToolDefinition]) -> RiskLevel:
    """取所组合工具中最高风险级别。

    未知或无法判断时按 WRITE 处理（默认更严格），
    这与 Policy Engine 的默认拒绝原则一致。
    """
    if any(dep.risk is RiskLevel.WRITE for dep in dependencies):
        return RiskLevel.WRITE
    return RiskLevel.READ


def _resolve_dependencies(spec: PluginToolSpec, registry: ToolRegistry) -> list[ToolDefinition]:
    """把 composes 解析成实际存在的内建工具定义。"""
    resolved: list[ToolDefinition] = []
    for composed in spec.composes:
        # composes 里的名字形如 builtin.list_tasks，而注册表键是 list_tasks。
        tool_name = composed.split(".", 1)[1] if composed.startswith("builtin.") else composed
        definition = registry.get(tool_name)
        if definition is None:
            raise ToolExecutionError(f"插件工具 {spec.name} 依赖的内建工具不存在：{composed}")
        resolved.append(definition)
    return resolved


def build_composed_tool(
    manifest: PluginManifest,
    spec: PluginToolSpec,
    *,
    registry: ToolRegistry,
) -> ToolDefinition:
    """把插件工具构造为 ToolDefinition。

    内部调用所用的 Broker 在执行时从 `_current_broker` 读取，
    因此拿到的是**本请求**的审批通道，内部写操作会真的弹出审批。
    """
    dependencies = _resolve_dependencies(spec, registry)
    risk = _highest_risk(dependencies)

    async def handler(arguments: dict[str, Any], context: ToolContext) -> dict[str, Any]:
        inner_broker = _current_broker.get()
        if inner_broker is None:
            # 没有审批通道时绝不"因为没有通道所以直接执行"（默认拒绝）。
            return {
                "source": "plugin",
                "plugin": manifest.id,
                "tool": spec.name,
                "status": "denied",
                "error_code": "PLUGIN_NO_BROKER",
                "message": "插件工具需要经过审批通道，但当前没有可用的通道。",
            }
        results: list[dict[str, Any]] = []
        used = 0

        for composed in spec.composes:
            tool_name = composed.split(".", 1)[1]
            # 关键：内部调用同样经过 Broker（参数校验 → Policy → 审批 → 审计）。
            # 直接调 definition.handler 会绕过用户确认。
            result: ToolResult = await inner_broker.execute(
                tool_name,
                _arguments_for(dependencies, spec, tool_name, arguments),
                context,
                call_id=f"plugin:{spec.name}:{tool_name}",
            )
            used += len(result.content)
            if used > MAX_COMPOSED_RESULT_CHARS:
                raise ToolExecutionError("插件工具的输出超过上限。")
            results.append(
                {
                    "tool": composed,
                    "status": result.status,
                    "error_code": result.error_code,
                    "content": result.content,
                }
            )
            # 内部步骤被拒绝或失败时立即停止：继续执行后续步骤会产生
            # 用户没有预期的部分结果。
            if result.status != "ok":
                return {
                    "source": "plugin",
                    "plugin": manifest.id,
                    "tool": spec.name,
                    "status": result.status,
                    "steps": results,
                    "message": f"步骤 {composed} 未成功（{result.status}），已停止后续步骤。",
                }

        return {
            "source": "plugin",
            "plugin": manifest.id,
            "tool": spec.name,
            "status": "ok",
            "steps": results,
        }

    return ToolDefinition(
        name=f"{PLUGIN_TOOL_PREFIX}__{spec.name}",
        description=(
            f"【插件 {manifest.name}（{manifest.id}）提供的组合工具】\n{spec.description}"
        ),
        parameters=spec.input_schema,
        risk=risk,
        handler=handler,
        display_name=spec.display_name or spec.name,
        argument_labels=spec.argument_labels,
    )


def _arguments_for(
    dependencies: list[ToolDefinition],
    spec: PluginToolSpec,
    tool_name: str,
    arguments: dict[str, Any],
) -> str:
    """为某个内部工具裁剪参数。

    只保留该工具 Schema 声明过的字段：插件的外层 Schema 可能包含
    多个内部工具各自的参数，全量透传会因"未知参数"被内建工具拒绝。
    """
    import json

    target = next((dep for dep in dependencies if dep.name == tool_name), None)
    properties = (target.parameters.get("properties") if target else None) or {}
    if not isinstance(properties, dict):
        properties = {}
    filtered = {key: value for key, value in arguments.items() if key in properties}
    return json.dumps(filtered, ensure_ascii=False)


def register_plugin_tools(
    manifest: PluginManifest,
    *,
    registry: ToolRegistry,
) -> CompositionResult:
    """登记一个插件的全部工具。

    任何一个工具登记失败都跳过它并记录原因，不影响同插件的其他工具，
    更不影响宿主：插件作者写错一处不应该让整个系统不可用。
    """
    registered: list[str] = []
    skipped: list[tuple[str, str]] = []

    for spec in manifest.tools:
        name = f"{PLUGIN_TOOL_PREFIX}__{spec.name}"
        if registry.get(name) is not None:
            # 与内建/MCP/其他插件的工具重名：拒绝登记，不覆盖。
            skipped.append((name, f"工具名已被占用：{name}"))
            continue
        try:
            definition = build_composed_tool(manifest, spec, registry=registry)
        except ToolExecutionError as exc:
            skipped.append((name, str(exc)))
            continue
        registry.register(definition)
        registered.append(name)

    return CompositionResult(registered=tuple(registered), skipped=tuple(skipped))


def manifest_for_plugin_tool(manifest: PluginManifest, spec: PluginToolSpec) -> CapabilityManifest:
    """能力页展示用。声明其风险来自所组合的工具。"""
    return CapabilityManifest(
        id=f"plugin.{manifest.id}.{spec.name}",
        name=spec.display_name or spec.name,
        version=manifest.version,
        type=CapabilityType.PLUGIN,
        description=f"插件 {manifest.name}（{manifest.id}）：{spec.description}",
        input_schema=spec.input_schema,
        output_schema={"type": "object", "description": "组合调用的各步骤结果"},
        runtime=CapabilityRuntime.BUILTIN,
        # 声明：组合了哪些内建能力。仍然只是声明，实际决策由 Policy 做。
        permissions=tuple(f"composes:{name}" for name in spec.composes),
        compatibility=manifest.compatibility or ">=0.2.0",
        source=CapabilitySource.INSTALLED,
    )
