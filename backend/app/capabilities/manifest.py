"""Capability Manifest（AGENT.md §7.2、M8 第 1 项）。

统一描述"一种可被系统发现、授权、调用的能力"。M8 之前，工具只有
`ToolDefinition`（运行时对象，含 handler）；Manifest 是它的**可序列化描述**，
可以被存储、检索、展示、导出，而不携带任何可执行代码。

字段对应 §7.2 的清单：

    id           稳定标识（命名空间 + 名称），例如 "builtin.search_memories"
    name         面向用户的名称
    version      语义化版本
    type         能力类型（tool / mcp / skill / workflow / plugin / app）
    description  一句话说明
    input_schema JSON Schema
    output_schema JSON Schema（当前实现为描述性的空对象，M8 后续补齐）
    runtime      运行时标识（builtin / mcp:<server> / process 等）
    permissions  声明式权限（**不等于授权**，见下）
    dependencies 依赖的其他能力 id
    compatibility 兼容性说明（如最低内核版本）
    source       来源（builtin / installed / community）
    signature    包签名（未签名时为 None）

安全边界（§4.9、§7.2）：

- Manifest 只是**声明**。声明了权限不代表获得权限，实际调用仍由
  Policy Engine 与 Tool Broker 决定。
- Manifest 中不包含 handler、路径、密钥等可执行或敏感内容。
- 本模块不做 IO，不读文件、不联网，纯数据校验，便于穷举测试。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

# 能力 id 的格式：小写命名空间 + 点 + 小写名称，允许连字符与下划线。
# 限定字符集是为了让 id 可以安全地出现在 URL、日志、文件名中。
_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*(\.[a-z][a-z0-9_-]*)+$")

_SEMVER_PATTERN = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")

MAX_ID_LENGTH = 128
MAX_NAME_LENGTH = 120
MAX_VERSION_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 2_000
MAX_PERMISSIONS = 64
MAX_DEPENDENCIES = 64


class ManifestError(ValueError):
    """Manifest 不合法。message 面向开发者，不含用户数据。"""


class CapabilityType(StrEnum):
    """能力类型（§4.9）。不同类型的运行机制不同，但描述格式统一。"""

    TOOL = "tool"
    MCP = "mcp"
    SKILL = "skill"
    WORKFLOW = "workflow"
    PLUGIN = "plugin"
    APP = "app"


class CapabilityRuntime(StrEnum):
    """运行时。M8 只有内建；后续加入 MCP、独立进程等。"""

    BUILTIN = "builtin"
    MCP = "mcp"
    PROCESS = "process"


class CapabilitySource(StrEnum):
    """能力来源。决定界面上的可信度提示与未来的更新策略。"""

    BUILTIN = "builtin"
    INSTALLED = "installed"
    COMMUNITY = "community"


@dataclass(frozen=True, slots=True)
class CapabilityManifest:
    """一种能力的完整声明。

    `permissions` 用字符串数组表达（例如 ``["memory:read", "task:write"]``），
    只做**声明**，不参与任何授权判断。这样设计的原因：如果 Manifest 能
    直接授予权限，那么"声明即授权"，§7.2 的约束就被架空了。
    """

    id: str
    name: str
    version: str
    type: CapabilityType
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_schema: dict[str, Any] = field(default_factory=dict)
    runtime: CapabilityRuntime = CapabilityRuntime.BUILTIN
    permissions: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    compatibility: str = ""
    source: CapabilitySource = CapabilitySource.BUILTIN
    signature: str | None = None

    def __post_init__(self) -> None:
        # 枚举字段允许传入字符串（from_dict / manifest_from_tool 的便利），
        # 但对象内部必须持有真正的枚举，否则调用 .value 会崩。
        # frozen dataclass 用 object.__setattr__ 规范化。
        for attr, enum_cls in (
            ("type", CapabilityType),
            ("runtime", CapabilityRuntime),
            ("source", CapabilitySource),
        ):
            current = getattr(self, attr)
            if isinstance(current, str) and not isinstance(current, enum_cls):
                try:
                    object.__setattr__(self, attr, enum_cls(current))
                except ValueError as exc:
                    options = " / ".join(item.value for item in enum_cls)
                    raise ManifestError(
                        f"Manifest 字段 {attr} 取值无效：{current!r}（可选：{options}）"
                    ) from exc
        # 列表统一成元组，保证不可变并让相等比较稳定。
        for attr in ("permissions", "dependencies"):
            current = getattr(self, attr)
            if isinstance(current, list):
                object.__setattr__(self, attr, tuple(current))

        validate_manifest(self)

    # ---- 转换 ----

    def to_dict(self) -> dict[str, Any]:
        """序列化为纯 JSON 兼容结构（用于 API 响应与导出）。"""
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "type": self.type.value,
            "description": self.description,
            "input_schema": self.input_schema,
            "output_schema": self.output_schema,
            "runtime": self.runtime.value,
            "permissions": list(self.permissions),
            "dependencies": list(self.dependencies),
            "compatibility": self.compatibility,
            "source": self.source.value,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CapabilityManifest:
        """从外部数据构造。

        外部数据（安装包、社区下载）属于**不可信输入**（§8.4），
        因此逐字段校验，未知字段直接拒绝而不是静默忽略——
        静默忽略会让清单里的拼写错误变成"看起来生效了其实没生效"。
        """
        if not isinstance(data, dict):
            raise ManifestError("Manifest 必须是一个对象。")

        allowed = {
            "id",
            "name",
            "version",
            "type",
            "description",
            "input_schema",
            "output_schema",
            "runtime",
            "permissions",
            "dependencies",
            "compatibility",
            "source",
            "signature",
        }
        unknown = set(data) - allowed
        if unknown:
            raise ManifestError(f"Manifest 含有未知字段：{', '.join(sorted(unknown))}")

        known = {key: data[key] for key in data if key in allowed}
        # type 决定"这是什么能力"，必须显式声明；runtime / source 有合理默认值
        # （内建 / builtin），缺省时不应报错，否则内建能力也要写一堆样板字段。
        if "type" not in known:
            raise ManifestError("Manifest 缺少必需字段：type")
        # 枚举字段单独转换，并给出可读的错误而不是 KeyError/ValueError 堆栈。
        for key, enum_cls in (
            ("type", CapabilityType),
            ("runtime", CapabilityRuntime),
            ("source", CapabilitySource),
        ):
            if key not in known:
                continue
            raw = known[key]
            if not isinstance(raw, str):
                raise ManifestError(f"Manifest 字段 {key} 必须是字符串")
            try:
                known[key] = enum_cls(raw)
            except ValueError as exc:
                options = " / ".join(item.value for item in enum_cls)
                raise ManifestError(
                    f"Manifest 字段 {key} 取值无效：{raw}（可选：{options}）"
                ) from exc

        for key in ("permissions", "dependencies"):
            raw = known.get(key, ())
            if not isinstance(raw, (list, tuple)) or not all(isinstance(x, str) for x in raw):
                raise ManifestError(f"Manifest 字段 {key} 必须是字符串数组")
            known[key] = tuple(raw)

        if "input_schema" not in known:
            known["input_schema"] = {}
        if "output_schema" not in known:
            known["output_schema"] = {}
        for key in ("input_schema", "output_schema"):
            if not isinstance(known[key], dict):
                raise ManifestError(f"Manifest 字段 {key} 必须是对象")

        try:
            return cls(**known)  # type: ignore[arg-type]
        except TypeError as exc:
            # 缺少必填的 id/name/version 等情况。
            raise ManifestError(f"Manifest 字段不完整：{exc}") from exc


def validate_manifest(manifest: CapabilityManifest) -> None:
    """校验字段合法性。任何一项不合法都抛 ManifestError。

    刻意严格：一份写错的清单如果能通过校验，用户和后续的授权判断
    都会基于错误信息做决定。
    """
    if not isinstance(manifest.id, str) or not _ID_PATTERN.match(manifest.id):
        raise ManifestError(
            f"能力 id 不合法：{manifest.id!r}。要求形如 namespace.name，"
            "由小写字母开头，只含字母、数字、下划线、连字符与点。"
        )
    if len(manifest.id) > MAX_ID_LENGTH:
        raise ManifestError(f"能力 id 不能超过 {MAX_ID_LENGTH} 个字符。")

    _require_text("name", manifest.name, MAX_NAME_LENGTH, required=True)
    _require_text("description", manifest.description, MAX_DESCRIPTION_LENGTH)
    _require_text("compatibility", manifest.compatibility, MAX_DESCRIPTION_LENGTH)

    if not isinstance(manifest.version, str) or not _SEMVER_PATTERN.match(manifest.version):
        raise ManifestError(f"版本号必须形如 1.0.0：{manifest.version!r}")

    if len(manifest.permissions) > MAX_PERMISSIONS:
        raise ManifestError(f"权限声明不能超过 {MAX_PERMISSIONS} 项。")
    if len(manifest.dependencies) > MAX_DEPENDENCIES:
        raise ManifestError(f"依赖声明不能超过 {MAX_DEPENDENCIES} 项。")

    for permission in manifest.permissions:
        if not isinstance(permission, str) or not permission.strip():
            raise ManifestError("权限声明必须是非空字符串。")

    for dependency in manifest.dependencies:
        if not isinstance(dependency, str) or not _ID_PATTERN.match(dependency):
            raise ManifestError(f"依赖 id 不合法：{dependency!r}")
        if dependency == manifest.id:
            raise ManifestError("能力不能依赖自身。")

    if manifest.signature is not None and not isinstance(manifest.signature, str):
        raise ManifestError("signature 必须是字符串或 null。")


def _require_text(field_name: str, value: Any, limit: int, *, required: bool = False) -> None:
    if not isinstance(value, str):
        raise ManifestError(f"字段 {field_name} 必须是字符串。")
    if required and not value.strip():
        raise ManifestError(f"字段 {field_name} 不能为空。")
    if len(value) > limit:
        raise ManifestError(f"字段 {field_name} 不能超过 {limit} 个字符。")
