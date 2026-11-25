"""插件注册表（M8 第 7 项，提案第 2 步）。

管理插件的**安装、启用、禁用、卸载与状态**。本期不做任何代码执行，
因此这里的风险集中在两处：

1. **安装时的文件操作**：从一个目录复制到数据目录。必须防止路径逃逸
   （`../`、绝对路径、符号链接）把文件写到数据目录之外。
2. **启用状态**：卸载时按用户决定**保留插件目录**（提案问题 3），
   只取消注册；用户可手动删除。

状态存放：

    <数据目录>/plugins/<plugin-id>/plugin.json    插件包（用户可读可改）
    <数据目录>/plugins/state.json                 安装与启用状态

为什么状态单独放一份而不是写回 plugin.json：清单是**插件作者提供的内容**，
用户改它不应该影响"我装了什么、我启用没启用"；两者混在一起会导致
"改了清单所以卸载状态丢了"这类问题。
"""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from app.plugins.manifest import (
    PLUGIN_MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    load_manifest,
)

logger = logging.getLogger(__name__)

STATE_FILENAME = "state.json"

# 单个插件目录的总大小上限。声明式插件只有 json，正常远小于此；
# 上限是防止误装一个巨大的目录把数据盘塞满。
MAX_PLUGIN_DIR_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_PLUGIN = 200
MAX_PLUGINS = 100


class PluginError(RuntimeError):
    """插件操作失败。message 面向用户，不含内部路径细节。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InstalledPlugin:
    """一个已安装插件的状态。`manifest` 为 None 表示清单已损坏。"""

    manifest: PluginManifest | None
    directory: str
    enabled: bool
    installed_at: str
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.manifest is not None

    @property
    def id(self) -> str:
        return self.manifest.id if self.manifest else self.directory


@dataclass
class PluginRegistry:
    """插件注册表。所有方法同步执行：操作都在本地文件系统上完成且很快。"""

    root: Path
    _state: dict[str, dict[str, object]] = field(default_factory=dict, repr=False)

    # ---- 状态读写 ----

    @property
    def _state_path(self) -> Path:
        return self.root / STATE_FILENAME

    def load(self) -> None:
        """从磁盘读取状态。文件损坏时清空状态而不是让服务起不来。"""
        self._state = {}
        path = self._state_path
        if not path.is_file():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            # 状态文件损坏：插件目录仍在，但"是否启用"未知。
            # 采用保守策略——全部视为未启用，用户需要重新确认。
            logger.warning("插件状态文件损坏，所有插件按未启用处理")
            return
        if isinstance(data, dict):
            entries = data.get("plugins", {})
            if isinstance(entries, dict):
                self._state = {
                    key: value for key, value in entries.items() if isinstance(value, dict)
                }

    def _save(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        payload = {"plugins": self._state}
        tmp = self._state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        # 原子替换：避免写到一半崩溃留下半个 JSON。
        tmp.replace(self._state_path)

    # ---- 查询 ----

    def list(self) -> list[InstalledPlugin]:
        """列出所有已安装插件，按 id 排序。"""
        if not self.root.is_dir():
            return []

        plugins: list[InstalledPlugin] = []
        for child in sorted(self.root.iterdir()):
            if not child.is_dir() or child.name.startswith("."):
                continue
            plugins.append(self._load_installed(child))
        return plugins

    def _load_installed(self, directory: Path) -> InstalledPlugin:
        state = self._state.get(directory.name, {})
        installed_at = str(state.get("installed_at", ""))
        enabled = bool(state.get("enabled", False))

        try:
            manifest = load_manifest(directory)
        except PluginManifestError as exc:
            # 清单坏了：保留状态（用户可能只是手滑改错），但标出错误。
            return InstalledPlugin(
                manifest=None,
                directory=directory.name,
                enabled=False,  # 清单不合法时绝不启用
                installed_at=installed_at,
                error=str(exc),
            )

        return InstalledPlugin(
            manifest=manifest,
            directory=directory.name,
            enabled=enabled,
            installed_at=installed_at,
        )

    def get(self, plugin_id: str) -> InstalledPlugin | None:
        for plugin in self.list():
            if plugin.id == plugin_id or plugin.directory == plugin_id:
                return plugin
        return None

    # ---- 安装 ----

    def install_from_directory(self, source: Path, *, enabled: bool = False) -> InstalledPlugin:
        """从本地目录安装插件。

        先校验来源目录（清单合法、大小与文件数在限、无符号链接），
        再复制到数据目录。**不覆盖**已存在的插件：覆盖安装会让用户
        在不知情时换掉一个正在用的插件版本。
        """
        if not source.is_dir():
            raise PluginError("插件来源必须是一个目录。", code="PLUGIN_SOURCE_INVALID")

        manifest = self._validate_source(source)

        if self.get(manifest.id) is not None:
            raise PluginError(
                f"插件 {manifest.id} 已安装。如需更新，请先卸载（目录会保留）。",
                code="PLUGIN_ALREADY_INSTALLED",
            )
        if len(self.list()) >= MAX_PLUGINS:
            raise PluginError(f"插件数量已达上限（{MAX_PLUGINS}）。", code="PLUGIN_LIMIT_REACHED")

        target = self.root / manifest.id
        if target.exists():
            # 目录存在但状态里没有：上次卸载保留了目录。
            raise PluginError(
                f"目录 {manifest.id} 已存在（可能是卸载后保留的）。"
                "请先删除该目录，或换一个插件 id。",
                code="PLUGIN_DIRECTORY_EXISTS",
            )

        self.root.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copytree(source, target, symlinks=False)
        except OSError as exc:
            # 复制失败要清理半成品，避免留下一个"看起来装了"的残缺目录。
            shutil.rmtree(target, ignore_errors=True)
            logger.warning("插件复制失败", extra={"error": type(exc).__name__})
            raise PluginError("复制插件文件失败。", code="PLUGIN_COPY_FAILED") from exc

        self._state[manifest.id] = {
            "enabled": enabled,
            "installed_at": _now_iso(),
        }
        self._save()
        logger.info("插件已安装", extra={"plugin_id": manifest.id, "enabled": enabled})
        return InstalledPlugin(
            manifest=manifest,
            directory=manifest.id,
            enabled=enabled,
            installed_at=str(self._state[manifest.id]["installed_at"]),
        )

    def _validate_source(self, source: Path) -> PluginManifest:
        """校验来源目录。任何可疑之处都拒绝。"""
        manifest = load_manifest(source)

        total = 0
        count = 0
        for item in source.rglob("*"):
            count += 1
            if count > MAX_FILES_PER_PLUGIN:
                raise PluginError(
                    f"插件文件数超过上限（{MAX_FILES_PER_PLUGIN}）。", code="PLUGIN_TOO_MANY_FILES"
                )
            if item.is_symlink():
                # 符号链接可能指向数据目录之外（例如 ~/.ssh），复制时会被跟随。
                raise PluginError(
                    f"插件包含符号链接：{item.name}。为安全起见拒绝安装。",
                    code="PLUGIN_SYMLINK_REJECTED",
                )
            if item.is_file():
                total += item.stat().st_size
                if total > MAX_PLUGIN_DIR_BYTES:
                    raise PluginError("插件目录过大。", code="PLUGIN_TOO_LARGE")

        if not (source / PLUGIN_MANIFEST_FILENAME).is_file():
            raise PluginError("插件目录缺少 plugin.json。", code="PLUGIN_MANIFEST_MISSING")
        return manifest

    # ---- 启用 / 禁用 / 卸载 ----

    def set_enabled(self, plugin_id: str, enabled: bool) -> InstalledPlugin:
        """启用或禁用插件。

        清单不合法时拒绝启用：启用一个我们读不懂的插件没有意义，
        只会让界面显示一个状态不明的条目。
        """
        plugin = self.get(plugin_id)
        if plugin is None:
            raise PluginError("插件不存在。", code="PLUGIN_NOT_FOUND")
        if not plugin.ok:
            raise PluginError(
                f"插件清单有问题，无法启用：{plugin.error}", code="PLUGIN_MANIFEST_INVALID"
            )

        key = plugin.directory
        entry = self._state.setdefault(key, {})
        entry["enabled"] = enabled
        entry.setdefault("installed_at", _now_iso())
        self._save()

        return InstalledPlugin(
            manifest=plugin.manifest,
            directory=plugin.directory,
            enabled=enabled,
            installed_at=str(entry["installed_at"]),
        )

    def uninstall(self, plugin_id: str) -> str:
        """卸载插件：取消注册并**保留目录**（用户决定的策略）。

        返回被保留的目录路径说明，供界面展示。
        保留的理由：插件目录里可能含用户自己的配置，自动删除不可逆。

        实现细节：目录会被重命名为 `.<id>.uninstalled`（加点前缀）。
        为什么必须改名而不是只删状态——`list()` 扫描的是目录，
        若只删状态，保留的目录会被当作"已安装但未启用"再次列出，
        与"已卸载"直接矛盾。改名后它既不被扫描到，文件又原样保留。
        """
        plugin = self.get(plugin_id)
        if plugin is None:
            raise PluginError("插件不存在。", code="PLUGIN_NOT_FOUND")

        self._state.pop(plugin.directory, None)
        self._save()

        source = self.root / plugin.directory
        kept_name = f".{plugin.directory}.uninstalled"
        target = self.root / kept_name
        if source.is_dir():
            if target.exists():
                # 已有一份保留目录：加时间戳避免覆盖用户的旧数据。
                kept_name = f".{plugin.directory}.uninstalled.{_now_stamp()}"
                target = self.root / kept_name
            try:
                source.rename(target)
            except OSError:
                # 改名失败也不能让卸载失败——状态已经清掉了，
                # 但要把真实情况告诉用户，不能谎称"已保留在某个位置"。
                logger.warning("插件目录改名失败", extra={"plugin_id": plugin.id})
                return "已卸载。但目录未能改名，请手动检查并删除。"

        logger.info("插件已卸载（目录保留）", extra={"plugin_id": plugin.id})
        return f"已卸载。文件保留在数据目录的 plugins/{kept_name}，可手动删除。"


def _now_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")
