"""插件清单（M8 第 6 项，提案第 1 步，见 docs/PROPOSAL_PLUGINS.md）。

本期只实现 **declarative（声明式）插件**：它不含任何可执行代码，
只是把已有的内建工具组合成一个新工具。因此这一层的全部职责是
"读取并严格校验一份 JSON"，没有任何执行逻辑。

清单来自外部（用户从别处拿到的目录/压缩包），属于**不可信输入**（§8.4），
因此：

- 未知字段一律拒绝，不静默忽略——否则拼错的字段会显示"生效了"实际没有；
- `composes` 只能引用**已注册的内建工具**，且必须是白名单形式；
  `composes` 不能引用另一个插件（避免插件套插件形成隐式授权链，
  也避免依赖环）；
- 清单大小、字段长度、工具数量都有上限。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

PLUGIN_MANIFEST_FILENAME = "plugin.json"

# 清单文件上限。一个真正需要超过 256KB 清单的插件多半有问题。
MAX_MANIFEST_BYTES = 256 * 1024
MAX_TOOLS = 20
MAX_ID_LENGTH = 128
MAX_NAME_LENGTH = 120
MAX_DESCRIPTION_LENGTH = 2_000
MAX_VERSION_LENGTH = 64
MAX_SCHEMA_DEPTH = 8

# 插件 id：反向域名风格，避免与内建能力或其他作者冲突。
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(\.[a-z][a-z0-9_-]*){1,5}$")
_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

# 本期的插件 id 命名空间。安装时强制加前缀，防止冒充内建能力
# （例如插件 Celery 起名 builtin.create_task 来覆盖同名的内建工具）。
PLUGIN_TOOL_PREFIX = "plugin"


class PluginManifestError(ValueError):
    """插件清单不合法。message 面向用户，必须指出哪个字段错了。"""


class PluginType(StrEnum):
    """插件类型。本期只支持 declarative；process 在后续阶段加入。"""

    DECLARATIVE = "declarative"
    PROCESS = "process"


@dataclass(frozen=True, slots=True)
class PluginToolSpec:
    """插件声明的一个工具。"""

    name: str
    description: str
    input_schema: dict[str, Any]
    display_name: str = ""
    # 声明式插件：按顺序调用的内建工具名。
    composes: tuple[str, ...] = ()
    # 给用户看的参数标签（与内建工具一致，审批摘要用，D-012）。
    argument_labels: tuple[tuple[str, str], ...] = ()

    @property
    def qualified_name(self) -> str:
        return f"{PLUGIN_TOOL_PREFIX}__{self.name}"


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """一份已校验的插件清单。不包含任何可执行内容。"""

    id: str
    name: str
    version: str
    type: PluginType
    description: str = ""
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    compatibility: str = ""
    tools: tuple[PluginToolSpec, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if isinstance(self.type, str) and not isinstance(self.type, PluginType):
            try:
                object.__setattr__(self, "type", PluginType(self.type))
            except ValueError as exc:
                options = " / ".join(item.value for item in PluginType)
                raise PluginManifestError(
                    f"插件类型无效：{self.type!r}（可选：{options}）"
                ) from exc
        validate_manifest(self)


def validate_manifest(manifest: PluginManifest) -> None:
    """逐字段校验。任何一项不合法都抛 PluginManifestError。"""
    if not _ID_PATTERN.match(manifest.id):
        raise PluginManifestError(
            f"插件 id 不合法：{manifest.id!r}。要求形如 com.example.notes"
            "（小写字母、数字、点，至少两段）。"
        )
    if len(manifest.id) > MAX_ID_LENGTH:
        raise PluginManifestError(f"插件 id 不能超过 {MAX_ID_LENGTH} 个字符。")

    if not manifest.name.strip():
        raise PluginManifestError("插件 name 不能为空。")
    if len(manifest.name) > MAX_NAME_LENGTH:
        raise PluginManifestError(f"插件 name 不能超过 {MAX_NAME_LENGTH} 个字符。")
    if len(manifest.description) > MAX_DESCRIPTION_LENGTH:
        raise PluginManifestError(f"插件 description 不能超过 {MAX_DESCRIPTION_LENGTH} 个字符。")
    if len(manifest.version) > MAX_VERSION_LENGTH or not _SEMVER_PATTERN.match(manifest.version):
        raise PluginManifestError(f"插件 version 必须形如 1.0.0：{manifest.version!r}")

    if manifest.type is PluginType.PROCESS:
        # 本期明确不支持：不静默忽略成 declarative，否则用户会以为
        # 自己的可执行插件已经被/不会被运行，两种情况都很危险。
        raise PluginManifestError(
            "本期只支持 declarative（声明式）插件；process 类型将在后续阶段支持。"
        )

    if not manifest.tools:
        raise PluginManifestError("插件至少要声明一个工具。")
    if len(manifest.tools) > MAX_TOOLS:
        raise PluginManifestError(f"单个插件最多声明 {MAX_TOOLS} 个工具。")

    seen: set[str] = set()
    for tool in manifest.tools:
        _validate_tool(tool, seen)

    for dependency in manifest.dependencies:
        # 依赖必须是另一个插件的 id，不能是内建工具名。
        if not _ID_PATTERN.match(dependency):
            raise PluginManifestError(f"依赖 id 不合法：{dependency!r}")
        if dependency == manifest.id:
            raise PluginManifestError("插件不能依赖自身。")


def _validate_tool(tool: PluginToolSpec, seen: set[str]) -> None:
    if not _TOOL_NAME_PATTERN.match(tool.name):
        raise PluginManifestError(
            f"工具名 {tool.name!r} 不合法：只能用小写字母、数字、下划线，以字母开头，最长 64。"
        )
    if tool.name in seen:
        raise PluginManifestError(f"插件内工具名重复：{tool.name}")
    seen.add(tool.name)

    if not tool.description.strip():
        raise PluginManifestError(f"工具 {tool.name} 缺少 description。")
    if len(tool.description) > MAX_DESCRIPTION_LENGTH:
        raise PluginManifestError(f"工具 {tool.name} 的 description 过长。")

    if not tool.composes:
        raise PluginManifestError(
            f"工具 {tool.name} 必须声明 composes（要组合的内建工具）。"
            "声明式插件不含代码，它只能复用已有能力。"
        )
    for composed in tool.composes:
        # 必须是内建的 <namespace>.<tool> 形式；不接受外部插件或任意字符串。
        if not re.match(r"^builtin\.[a-z][a-z0-9_]*$", composed):
            raise PluginManifestError(
                f"工具 {tool.name} 的 composes 项 {composed!r} 不合法："
                "只能引用内建工具（形如 builtin.list_tasks）。"
            )

    if not isinstance(tool.input_schema, dict):
        raise PluginManifestError(f"工具 {tool.name} 的 input_schema 必须是对象。")
    _validate_schema_depth(tool.input_schema, tool.name)


def _validate_schema_depth(schema: Any, tool_name: str, depth: int = 0) -> None:
    """限制 JSON Schema 嵌套深度。

    没有深度限制的 Schema 会让参数校验递归很深，是廉价的 DoS 入口。
    """
    if depth > MAX_SCHEMA_DEPTH:
        raise PluginManifestError(f"工具 {tool_name} 的 input_schema 嵌套过深。")
    if isinstance(schema, dict):
        for value in schema.values():
            _validate_schema_depth(value, tool_name, depth + 1)
    elif isinstance(schema, list):
        for item in schema:
            _validate_schema_depth(item, tool_name, depth + 1)


def _parse_tool(entry: Any, index: int) -> PluginToolSpec:
    if not isinstance(entry, dict):
        raise PluginManifestError(f"第 {index + 1} 个工具必须是对象。")

    allowed = {"name", "description", "input_schema", "display_name", "composes", "argument_labels"}
    unknown = set(entry) - allowed
    if unknown:
        raise PluginManifestError(
            f"第 {index + 1} 个工具含有未知字段：{', '.join(sorted(unknown))}"
        )

    composes = entry.get("composes", [])
    if not isinstance(composes, list) or not all(isinstance(x, str) for x in composes):
        raise PluginManifestError(f"第 {index + 1} 个工具的 composes 必须是字符串数组。")

    labels_raw = entry.get("argument_labels", [])
    labels: list[tuple[str, str]] = []
    if labels_raw:
        if not isinstance(labels_raw, list):
            raise PluginManifestError(f"第 {index + 1} 个工具的 argument_labels 必须是数组。")
        for pair in labels_raw:
            if not (
                isinstance(pair, list) and len(pair) == 2 and all(isinstance(x, str) for x in pair)
            ):
                raise PluginManifestError(
                    f"第 {index + 1} 个工具的 argument_labels 每项必须是 [字段名, 中文标签]。"
                )
            labels.append((pair[0], pair[1]))

    schema = entry.get("input_schema", {"type": "object", "properties": {}})
    return PluginToolSpec(
        name=str(entry.get("name", "")),
        description=str(entry.get("description", "")),
        input_schema=schema,
        display_name=str(entry.get("display_name", "")),
        composes=tuple(composes),
        argument_labels=tuple(labels),
    )


def parse_manifest(data: dict[str, Any]) -> PluginManifest:
    """从字典构造清单。未知字段一律拒绝。"""
    if not isinstance(data, dict):
        raise PluginManifestError("清单顶层必须是一个对象。")

    allowed = {
        "id",
        "name",
        "version",
        "type",
        "description",
        "permissions",
        "dependencies",
        "compatibility",
        "tools",
        # process 相关字段在解析阶段就拒绝，给出明确说明而不是"未知字段"。
        "entrypoint",
        "limits",
    }
    unknown = set(data) - allowed
    if unknown:
        raise PluginManifestError(f"清单含有未知字段：{', '.join(sorted(unknown))}")

    if "entrypoint" in data or "limits" in data:
        raise PluginManifestError(
            "本期只支持 declarative（声明式）插件，不支持 entrypoint / limits。"
        )

    for key in ("id", "name", "version", "type"):
        if key not in data:
            raise PluginManifestError(f"清单缺少必需字段：{key}")

    permissions = data.get("permissions", [])
    if not isinstance(permissions, list) or not all(isinstance(x, str) for x in permissions):
        raise PluginManifestError("permissions 必须是字符串数组。")

    dependencies = data.get("dependencies", [])
    if not isinstance(dependencies, list) or not all(isinstance(x, str) for x in dependencies):
        raise PluginManifestError("dependencies 必须是字符串数组。")

    tools_raw = data.get("tools", [])
    if not isinstance(tools_raw, list):
        raise PluginManifestError("tools 必须是数组。")

    tools = tuple(_parse_tool(entry, i) for i, entry in enumerate(tools_raw))

    for key in ("name", "description", "compatibility"):
        if key in data and not isinstance(data[key], str):
            raise PluginManifestError(f"{key} 必须是字符串。")

    return PluginManifest(
        id=str(data["id"]),
        name=str(data["name"]),
        version=str(data["version"]),
        type=data["type"],
        description=str(data.get("description", "")),
        permissions=tuple(permissions),
        dependencies=tuple(dependencies),
        compatibility=str(data.get("compatibility", "")),
        tools=tools,
    )


def load_manifest(directory: Path) -> PluginManifest:
    """从一个插件目录读取并校验清单。失败抛 PluginManifestError。"""
    manifest_path = directory / PLUGIN_MANIFEST_FILENAME
    if not manifest_path.is_file():
        raise PluginManifestError(f"缺少 {PLUGIN_MANIFEST_FILENAME}。")

    try:
        size = manifest_path.stat().st_size
    except OSError as exc:
        raise PluginManifestError("无法读取插件清单。") from exc
    if size > MAX_MANIFEST_BYTES:
        raise PluginManifestError(f"插件清单过大（{size} 字节，上限 {MAX_MANIFEST_BYTES}）。")

    try:
        text = manifest_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise PluginManifestError("插件清单不是可读的 UTF-8 文本。") from exc

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PluginManifestError(
            f"插件清单不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}"
        ) from exc

    return parse_manifest(data)
