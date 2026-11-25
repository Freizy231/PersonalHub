"""Staging Workspace 与应用版本管理（M9 第 1 步，提案 §5.5）。

职责：
1. 生成物先写入 `staging/`，**不直接进入正式目录**；
2. 通过检查后再安装到 `apps/<id>/versions/<version>/`；
3. 安装用**原子切换**：先写完新版本，再切换指针；失败时指针不变，
   旧版本继续可用；
4. 回滚 = 把指针切回上一版本；
5. 卸载 = 取消注册 + 目录重命名保留（与插件一致，用户决定过这个策略）。

目录结构：

    <数据目录>/apps/<app-id>/
      current              ← 指针文件，内容是版本号
      meta.json            ← 名称、安装时间、来源需求
      versions/<version>/index.html

为什么用指针文件而不是符号链接：Windows 上创建符号链接需要额外权限，
而本项目后续可能跨平台。指针文件是普通文本，任何平台都能读写。

为什么 staging 与 apps 分开：生成过程可能被中断（模型超时、用户取消），
半成品绝不能出现在正式目录里。staging 里的内容随时可以整体丢弃。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.builder.validate import VERSION_PATTERN, sanitize_app_id

logger = logging.getLogger(__name__)

APP_FILENAME = "index.html"
META_FILENAME = "meta.json"
# staging 目录里的生成记录。安装时凭它得到名称、来源需求与模型，
# 而不是让前端把需求再传一遍（前端传来的东西一律不可信）。
STAGING_META_FILENAME = "requirement.json"
POINTER_FILENAME = "current"
VERSIONS_DIR = "versions"

MAX_APPS = 50
MAX_VERSIONS_PER_APP = 10

_SAFE_FILENAME = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")

# staging 目录名与 staging_path() 的校验保持一致（UUID 十六进制）。
_SAFE_STAGING_ID = re.compile(r"^[a-z0-9]{8,64}$")


class BuilderError(RuntimeError):
    """构建或安装失败。message 面向用户，不含内部路径细节。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class InstalledApp:
    """一个已安装应用。`version` 为 None 表示指针损坏或没有版本。"""

    id: str
    name: str
    version: str | None
    versions: tuple[str, ...]
    installed_at: str
    description: str = ""

    @property
    def ok(self) -> bool:
        return self.version is not None


class AppStore:
    """应用仓库：staging 与正式目录的读写。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.apps_dir = root / "apps"
        self.staging_dir = root / "staging"

    # ---- staging ----

    def staging_path(self, staging_id: str) -> Path:
        """一次生成尝试对应的临时目录。

        `staging_id` 由调用方生成（UUID），这里再做一次白名单校验：
        它会被拼进路径，任何异常字符都可能是路径逃逸。
        """
        if not re.match(r"^[a-z0-9]{8,64}$", staging_id):
            raise BuilderError("临时的生成标识不合法。", code="APP_INVALID_STAGING_ID")
        return self.staging_dir / staging_id

    def write_staging(self, staging_id: str, html: str) -> Path:
        """把生成结果写入 staging。返回文件路径。

        写入前会再次检查 id，避免上层逻辑出错时把文件写到别处。
        """
        path = self.staging_path(staging_id)
        path.mkdir(parents=True, exist_ok=True)
        target = path / APP_FILENAME
        target.write_text(html, encoding="utf-8")
        return target

    def write_staging_meta(self, staging_id: str, data: dict) -> None:  # type: ignore[type-arg]
        """把这次生成的需求与结果记录到 staging 目录。"""
        path = self.staging_path(staging_id)
        path.mkdir(parents=True, exist_ok=True)
        self._write_json(path / STAGING_META_FILENAME, data)

    def read_staging_meta(self, staging_id: str) -> dict | None:  # type: ignore[type-arg]
        try:
            path = self.staging_path(staging_id)
        except BuilderError:
            return None
        return self._read_json(path / STAGING_META_FILENAME)

    def read_staging(self, staging_id: str) -> str | None:
        target = self.staging_path(staging_id) / APP_FILENAME
        if not target.is_file():
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    def list_staging(self) -> list[tuple[str, dict]]:  # type: ignore[type-arg]
        """列出 staging 里的生成结果，最新的在前。

        返回 `(staging_id, meta)`。用于"最近的生成"入口：生成结果在用户
        点安装之前一直在磁盘上（1 小时后才清理），但此前界面只能通过
        sessionStorage 记住**当前标签页**的那一次。换个标签页、换个浏览器、
        或者关掉页面，结果就找不到了——尽管它还在。

        目录名先过一遍白名单：staging 目录可能被别的东西写进过，
        不合法的一律跳过，不让它进入接口响应。
        """
        if not self.staging_dir.is_dir():
            return []

        entries: list[tuple[float, str, dict]] = []  # type: ignore[type-arg]
        for child in self.staging_dir.iterdir():
            if not child.is_dir() or not _SAFE_STAGING_ID.match(child.name):
                continue
            meta = self.read_staging_meta(child.name)
            if not isinstance(meta, dict):
                continue
            # 没有内容文件的目录不是可预览的结果（写了一半就中断）。
            if not (child / APP_FILENAME).is_file():
                continue
            try:
                mtime = child.stat().st_mtime
            except OSError:
                continue
            entries.append((mtime, child.name, meta))

        entries.sort(key=lambda item: (-item[0], item[1]))
        return [(staging_id, meta) for _, staging_id, meta in entries]

    def discard_staging(self, staging_id: str) -> None:
        """丢弃一次生成尝试。失败不影响其它内容。"""
        try:
            path = self.staging_path(staging_id)
        except BuilderError:
            return
        shutil.rmtree(path, ignore_errors=True)

    def cleanup_stale_staging(
        self,
        keep_ids: set[str] | None = None,
        *,
        max_age_seconds: float | None = None,
    ) -> int:
        """清理遗留的 staging 目录，返回清理数量。

        生成过程被中断时会留下目录；它们在启动时清理。
        `keep_ids` 保留仍在使用的（例如正在预览的）；
        `max_age_seconds` 只清理超过该时长的目录——启动时用它，
        避免把用户刚生成、还开着预览页的产物删掉。
        """
        if not self.staging_dir.is_dir():
            return 0
        keep = keep_ids or set()
        cutoff = _now_ts() - max_age_seconds if max_age_seconds is not None else None
        removed = 0
        for child in self.staging_dir.iterdir():
            if not child.is_dir() or child.name in keep:
                continue
            if cutoff is not None:
                try:
                    if child.stat().st_mtime > cutoff:
                        continue
                except OSError:
                    continue
            shutil.rmtree(child, ignore_errors=True)
            removed += 1
        return removed

    # ---- 安装 ----

    def install(
        self,
        *,
        app_id: str,
        name: str,
        version: str,
        html: str,
        description: str = "",
        source_request: str = "",
    ) -> InstalledApp:
        """把已通过检查的内容安装为一个新版本，并切换指针。

        步骤刻意做成"先写完整，再切换"：
        1. 校验 id 与版本号；
        2. 把新版本写到 `versions/<version>/`（此时指针未变）；
        3. 原子写指针文件。

        第 2 步失败时正式目录里可能留下一个不完整的版本目录，
        但它**不会被任何指针引用**，因此不影响可用性；第 3 步失败同理。
        """
        app_id = sanitize_app_id(app_id)
        if not VERSION_PATTERN.match(version):
            raise BuilderError(f"版本号必须形如 1.0.0：{version!r}", code="APP_INVALID_VERSION")

        app_root = self.apps_dir / app_id
        versions_root = app_root / VERSIONS_DIR

        existing = sorted(self._list_versions(app_id))
        if version in existing:
            raise BuilderError(
                f"版本 {version} 已存在。请使用新的版本号。", code="APP_VERSION_EXISTS"
            )
        if not existing and len(self._list_apps()) >= MAX_APPS:
            raise BuilderError(f"应用数量已达上限（{MAX_APPS}）。", code="APP_LIMIT_REACHED")
        if len(existing) >= MAX_VERSIONS_PER_APP:
            raise BuilderError(
                f"版本数已达上限（{MAX_VERSIONS_PER_APP}），请先卸载旧版本。",
                code="APP_TOO_MANY_VERSIONS",
            )

        version_dir = versions_root / version
        if version_dir.exists():
            # 上次安装失败留下的残留。它没有被任何指针引用，可以安全清理。
            shutil.rmtree(version_dir, ignore_errors=True)

        versions_root.mkdir(parents=True, exist_ok=True)
        try:
            version_dir.mkdir()
            (version_dir / APP_FILENAME).write_text(html, encoding="utf-8")
        except OSError as exc:
            shutil.rmtree(version_dir, ignore_errors=True)
            logger.warning("写入应用版本失败", extra={"error": type(exc).__name__})
            raise BuilderError("写入应用文件失败。", code="APP_WRITE_FAILED") from exc

        # 元信息与指针。两处都用"写临时文件再替换"，避免读到半个文件。
        self._write_json(
            app_root / META_FILENAME,
            {
                "id": app_id,
                "name": name,
                "description": description,
                "source_request": source_request,
                "installed_at": _now_iso(),
                "latest": version,
            },
        )
        self._write_atomic(app_root / POINTER_FILENAME, version)

        logger.info("应用已安装", extra={"app_id": app_id, "version": version})
        return self.get(app_id)  # type: ignore[return-value]

    def rollback(self, app_id: str) -> InstalledApp:
        """把指针切回上一个版本。没有上一个版本时报错。"""
        app_id = sanitize_app_id(app_id)
        versions = self._list_versions(app_id)
        current = self._current_version(app_id)
        if current is None:
            raise BuilderError("应用没有可回滚的版本。", code="APP_NO_CURRENT")
        if len(versions) < 2:
            raise BuilderError("没有更早的版本可回滚。", code="APP_NO_PREVIOUS")

        index = versions.index(current)
        if index == 0:
            raise BuilderError("当前已是最早版本。", code="APP_NO_PREVIOUS")

        previous = versions[index - 1]
        self._write_atomic(self.apps_dir / app_id / POINTER_FILENAME, previous)
        logger.info("应用已回滚", extra={"app_id": app_id, "to": previous})
        return self.get(app_id)  # type: ignore[return-value]

    def uninstall(self, app_id: str) -> str:
        """卸载：**保留目录**，只从扫描中移除（与插件一致的策略）。

        目录改名为 `.<id>.uninstalled`，既不被 `list_apps` 扫描到，
        用户又能随时找回来。
        """
        app_id = sanitize_app_id(app_id)
        if self.get(app_id) is None:
            raise BuilderError("应用不存在。", code="APP_NOT_FOUND")

        source = self.apps_dir / app_id
        target = self.apps_dir / f".{app_id}.uninstalled"
        if target.exists():
            target = self.apps_dir / f".{app_id}.uninstalled.{_now_stamp()}"

        try:
            source.rename(target)
        except OSError:
            # 改名失败不能假装成功。
            logger.warning("应用目录改名失败", extra={"app_id": app_id})
            raise BuilderError(
                "卸载时无法移动应用目录，请手动检查。", code="APP_UNINSTALL_FAILED"
            ) from None

        logger.info("应用已卸载（目录保留）", extra={"app_id": app_id})
        return f"已卸载。文件保留在数据目录的 apps/{target.name}，可手动删除。"

    # ---- 查询 ----

    def list_apps(self) -> list[InstalledApp]:
        return sorted(self._list_apps(), key=lambda app: app.id)

    def get(self, app_id: str) -> InstalledApp | None:
        app_id = sanitize_app_id(app_id)
        root = self.apps_dir / app_id
        if not root.is_dir() or app_id.startswith("."):
            return None

        meta = self._read_json(root / META_FILENAME) or {}
        versions = self._list_versions(app_id)
        current = self._current_version(app_id)

        return InstalledApp(
            id=app_id,
            name=str(meta.get("name", app_id)),
            version=current,
            versions=versions,
            installed_at=str(meta.get("installed_at", "")),
            description=str(meta.get("description", "")),
        )

    def read_app(self, app_id: str, version: str | None = None) -> str | None:
        """读取应用内容。默认读当前版本。"""
        app_id = sanitize_app_id(app_id)
        target_version = version or self._current_version(app_id)
        if target_version is None:
            return None
        if not _SAFE_FILENAME.match(target_version):
            # 版本号来自指针文件，仍要校验：它会被拼进路径。
            return None
        path = self.apps_dir / app_id / VERSIONS_DIR / target_version / APP_FILENAME
        if not path.is_file():
            return None
        try:
            return path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None

    # ---- 内部 ----

    def _list_apps(self) -> list[InstalledApp]:
        if not self.apps_dir.is_dir():
            return []
        apps: list[InstalledApp] = []
        for child in self.apps_dir.iterdir():
            if not child.is_dir() or child.name.startswith("."):
                continue
            try:
                app = self.get(child.name)
            except ValueError:
                # 目录名不合法（用户手动创建的）：跳过，不影响其他应用。
                continue
            if app is not None:
                apps.append(app)
        return apps

    def _list_versions(self, app_id: str) -> tuple[str, ...]:
        versions_dir = self.apps_dir / app_id / VERSIONS_DIR
        if not versions_dir.is_dir():
            return ()
        names = [
            child.name
            for child in versions_dir.iterdir()
            if child.is_dir() and _SAFE_FILENAME.match(child.name)
        ]
        return tuple(sorted(names, key=_version_key))

    def _current_version(self, app_id: str) -> str | None:
        pointer = self.apps_dir / app_id / POINTER_FILENAME
        if not pointer.is_file():
            return None
        try:
            value = pointer.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            return None
        if not _SAFE_FILENAME.match(value):
            return None
        # 指针指向的版本必须真实存在，否则视为损坏。
        if not (self.apps_dir / app_id / VERSIONS_DIR / value / APP_FILENAME).is_file():
            return None
        return value

    def _write_atomic(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)

    def _write_json(self, path: Path, data: dict) -> None:  # type: ignore[type-arg]
        self._write_atomic(path, json.dumps(data, ensure_ascii=False, indent=2))

    def _read_json(self, path: Path) -> dict | None:  # type: ignore[type-arg]
        if not path.is_file():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            return None
        return data if isinstance(data, dict) else None


def _version_key(version: str) -> tuple[int, int, int, int, str]:
    """语义化版本排序：1.10.0 > 1.9.0（不能按字符串比较）。"""
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(?:-(.+))?$", version)
    if match is None:  # pragma: no cover - 已在写入时校验
        return (0, 0, 0, 0, version)
    major, minor, patch = (int(match.group(i)) for i in (1, 2, 3))
    prerelease = match.group(4)
    return (major, minor, patch, 0 if prerelease else 1, prerelease or "")


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _now_ts() -> float:
    from datetime import UTC, datetime

    return datetime.now(UTC).timestamp()


def _now_stamp() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).strftime("%Y%m%d%H%M%S")
