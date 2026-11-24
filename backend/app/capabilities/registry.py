"""Capability Registry（AGENT.md §4.9、§7.2、M8 第 2 项）。

职责：登记、检索、版本管理能力的 **Manifest**。它不执行任何能力，
也不做授权判断——实际调用必须经过 Tool Broker / 对应 Runtime。

与 `ToolRegistry` 的分工：

    ToolRegistry       运行时对象：name -> ToolDefinition（含 handler）
    CapabilityRegistry 描述数据：id + version -> CapabilityManifest

两者刻意分开：Manifest 可以被导出、检索、展示给用户，
而 handler 只存在于进程内，绝不出现在 API 响应或导出包里。

版本语义：
- 同一 id 可以登记多个版本；`get(id)` 返回最高版本。
- 同 id 同版本重复登记会被拒绝（除非 `replace=True`），
  避免"装了两个同名同版本的东西"而无法判断哪个在生效。
- 版本比较遵循语义化版本：1.10.0 > 1.9.0（按数字而非字符串比较）。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.capabilities.manifest import CapabilityManifest, CapabilityType, ManifestError

logger = logging.getLogger(__name__)


class CapabilityRegistryError(RuntimeError):
    """登记或查询失败。message 面向开发者，不含用户数据。"""


def version_key(version: str) -> tuple[int, int, int, int, str]:
    """把语义化版本转成可比较的键。

    实测陷阱：直接按字符串比较会把 "1.10.0" 排在 "1.9.0" **之前**。
    这里把主/次/修订号转为整数；带预发布后缀的版本排在正式版之前
    （1.0.0-rc1 < 1.0.0），符合语义化版本的约定。
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", version)
    if match is None:
        raise CapabilityRegistryError(f"无法解析版本号：{version!r}")
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    prerelease = match.group(4)
    # 正式版的 prerelease_rank 为 1，预发布为 0 —— 预发布排前面。
    return (major, minor, patch, 0 if prerelease else 1, prerelease or "")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """一次检索的结果。附带说明"为什么没找到"，便于界面给出可操作的提示。"""

    items: tuple[CapabilityManifest, ...]
    total: int


class CapabilityRegistry:
    """进程内能力注册表，按 id + 版本存放 Manifest。"""

    def __init__(self) -> None:
        # id -> {version: manifest}
        self._by_id: dict[str, dict[str, CapabilityManifest]] = {}

    def register(self, manifest: CapabilityManifest, *, replace: bool = False) -> None:
        """登记一份 Manifest。

        不检查依赖是否存在：能力可以按任意顺序安装，依赖缺失在
        `missing_dependencies()` 里单独报告，而不是让登记失败——
        否则"先装依赖再装本体"的常规流程会被打断。
        """
        versions = self._by_id.setdefault(manifest.id, {})
        if manifest.version in versions and not replace:
            raise CapabilityRegistryError(
                f"能力 {manifest.id} 的版本 {manifest.version} 已登记。"
                "如需覆盖已安装版本，请显式使用 replace=True。"
            )
        versions[manifest.version] = manifest

    def unregister(self, capability_id: str, version: str | None = None) -> int:
        """移除某个能力的全部版本或指定版本。返回移除条数。

        仍有其他能力依赖它时拒绝移除，避免留下悬空依赖。
        """
        versions = self._by_id.get(capability_id)
        if not versions:
            return 0

        targets = [version] if version is not None else list(versions)
        remaining = {v: versions[v] for v in versions if v not in targets}
        dependents = [
            manifest.id
            for other_versions in self._by_id.values()
            for manifest in other_versions.values()
            if other_versions is not versions and capability_id in manifest.dependencies
        ]
        if dependents:
            raise CapabilityRegistryError(
                f"无法移除 {capability_id}：{', '.join(sorted(set(dependents)))} 依赖它。"
            )

        if remaining:
            self._by_id[capability_id] = remaining
        else:
            del self._by_id[capability_id]
        return len(targets)

    def get(self, capability_id: str, version: str | None = None) -> CapabilityManifest | None:
        """按 id 取 Manifest。不给版本时返回最高版本。"""
        versions = self._by_id.get(capability_id)
        if not versions:
            return None
        if version is not None:
            return versions.get(version)
        latest = max(versions, key=version_key)
        return versions[latest]

    def versions(self, capability_id: str) -> list[CapabilityManifest]:
        """返回某能力的全部版本，从低到高。"""
        versions = self._by_id.get(capability_id)
        if not versions:
            return []
        return [versions[v] for v in sorted(versions, key=version_key)]

    def all(self) -> list[CapabilityManifest]:
        """所有能力的最新版本，按 id 排序（便于界面稳定展示）。"""
        latest = [self.get(capability_id) for capability_id in self._by_id]
        return sorted((m for m in latest if m is not None), key=lambda m: m.id)

    def search(
        self,
        *,
        query: str = "",
        type: CapabilityType | None = None,
        source: str | None = None,
    ) -> SearchResult:
        """按关键词、类型、来源检索。

        关键词匹配 id 与 name（不区分大小写）。刻意不匹配 description：
        描述是自然语言，模糊匹配会把不相关的能力也召回，
        反而让"检索"变成噪声源。
        """
        needle = query.strip().lower()
        items = []
        for manifest in self.all():
            if type is not None and manifest.type is not type:
                continue
            if source is not None and manifest.source.value != source:
                continue
            if needle and needle not in manifest.id.lower() and needle not in manifest.name.lower():
                continue
            items.append(manifest)
        return SearchResult(items=tuple(items), total=len(items))

    def missing_dependencies(self) -> dict[str, tuple[str, ...]]:
        """返回"依赖了不存在的能力"的清单：能力 id -> 缺失的依赖 id。

        界面据此提示用户"还需要安装 X"，而不是在调用时才失败。
        """
        report: dict[str, tuple[str, ...]] = {}
        for manifest in self.all():
            missing = tuple(dep for dep in manifest.dependencies if dep not in self._by_id)
            if missing:
                report[manifest.id] = missing
        return report

    def check_dependency_cycle(self) -> tuple[str, ...]:
        """检测依赖环，返回环上的能力 id（无环时为空）。

        依赖环会让"按依赖顺序安装/启动"永远无法完成，必须显式报告。
        这是纯图遍历，不含 IO。
        """
        visiting: set[str] = set()
        done: set[str] = set()
        cycle: list[str] = []

        def visit(node: str, path: list[str]) -> bool:
            if node in done:
                return False
            if node in visiting:
                cycle.extend(path[path.index(node) :] + [node])
                return True
            visiting.add(node)
            manifest = self.get(node)
            for dependency in manifest.dependencies if manifest else ():
                if visit(dependency, path + [node]):
                    return True
            visiting.discard(node)
            done.add(node)
            return False

        for capability_id in self._by_id:
            if visit(capability_id, []):
                break
        return tuple(cycle)

    def __len__(self) -> int:
        """已登记的能力 id 数量（不是版本总数）。"""
        return len(self._by_id)


def manifest_from_tool(
    definition,  # type: ignore[no-untyped-def]
    *,
    version: str = "1.0.0",
    capabilities_id: str | None = None,
) -> CapabilityManifest:
    """把内建工具定义转换成 Manifest。

    这是 M8 之前世界的桥：现有工具只有一个 ToolDefinition，
    这里把它投影成统一描述，使它们也能出现在能力列表里。

    id 采用 `builtin.<tool_name>`：命名空间前缀让用户一眼看出这是内建能力，
    也为将来的 `mcp.<server>.*`、`plugin.<vendor>.*` 留出空间。
    """
    if not definition.name or not re.match(r"^[a-z][a-z0-9_]*$", definition.name):
        raise ManifestError(f"工具名无法转换为能力 id：{definition.name!r}")

    capability_id = capabilities_id or f"builtin.{definition.name}"
    return CapabilityManifest(
        id=capability_id,
        name=definition.title,
        version=version,
        type=CapabilityType.TOOL,
        description=definition.description,
        input_schema=dict(definition.parameters),
        # 输出 Schema 目前只有描述：内建工具返回自定义 dict，
        # 尚无稳定契约。M8 后续按工具逐个补全，不在这里编造。
        output_schema={"type": "object", "description": "工具定义的 JSON 结果"},
        runtime="builtin",  # type: ignore[arg-type]  # 枚举在 __post_init__ 校验
        permissions=(f"tool:{definition.name}:{definition.risk.value.lower()}",),
        compatibility=">=0.2.0",
        source="builtin",  # type: ignore[arg-type]
    )
