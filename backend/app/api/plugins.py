"""插件接口（M8 第 6–7 项）。

    GET    /api/v1/plugins                     列出已安装插件与状态
    POST   /api/v1/plugins/install             从本地目录安装
    POST   /api/v1/plugins/{id}/enable         启用
    POST   /api/v1/plugins/{id}/disable        禁用
    DELETE /api/v1/plugins/{id}                卸载（保留目录）

安全：

- 安装只接受**本地路径**，且固定从 `PH_PLUGIN_IMPORT_DIR`（默认数据目录下的
  `plugin-import/`）解析，不接受任意绝对路径——否则一个 HTTP 请求就能
  把机器上任意目录"装"进系统。
- 安装前必须由用户看到清单摘要；这里返回 `preview` 供前端展示，
  真正的安装由第二次请求提交。
- 本期不执行任何插件代码：所有操作都是读 JSON 与移动文件。
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import PublicMessageError
from app.plugins.manifest import PluginManifestError, load_manifest
from app.plugins.registry import PluginError, PluginRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plugins", tags=["plugins"])

MAX_TOOL_LABEL = 200


class PluginToolOut(BaseModel):
    name: str
    display_name: str
    description: str
    risk_hint: str
    composes: list[str]


class PluginOut(BaseModel):
    id: str
    name: str
    version: str
    type: str
    description: str
    enabled: bool
    installed_at: str
    permissions: list[str]
    dependencies: list[str]
    tools: list[PluginToolOut]
    error: str | None = None


class PluginListOut(BaseModel):
    items: list[PluginOut]
    total: int
    # 沙箱状态：进程插件依赖它。不可用时如实告知，而不是让用户装完才发现跑不了。
    sandbox_available: bool = False
    sandbox_reason: str = ""
    notice: str = (
        "本期插件为声明式：不含任何可执行代码，只是复用已有工具的固定组合。"
        "插件声明的权限不代表已获授权，调用仍由策略引擎逐次判断。"
    )


class InstallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 相对 PH_PLUGIN_IMPORT_DIR 的目录名，不是任意路径。
    directory: str = Field(min_length=1, max_length=128)
    enabled: bool = False


class PreviewOut(BaseModel):
    id: str
    name: str
    version: str
    description: str
    permissions: list[str]
    dependencies: list[str]
    tools: list[PluginToolOut]
    warnings: list[str]


class OkOut(BaseModel):
    ok: bool = True
    message: str = ""


def _registry(request: Request) -> PluginRegistry:
    registry = getattr(request.app.state, "plugin_registry", None)
    if registry is None:
        raise PublicMessageError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PLUGIN_REGISTRY_UNAVAILABLE",
            message="插件注册表尚未初始化。",
        )
    return registry


def _to_out(installed) -> PluginOut:  # type: ignore[no-untyped-def]
    manifest = installed.manifest
    if manifest is None:
        return PluginOut(
            id=installed.directory,
            name=installed.directory,
            version="",
            type="unknown",
            description="",
            enabled=False,
            installed_at=installed.installed_at,
            permissions=[],
            dependencies=[],
            tools=[],
            error=installed.error,
        )

    return PluginOut(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        type=manifest.type.value,
        description=manifest.description,
        enabled=installed.enabled,
        installed_at=installed.installed_at,
        permissions=list(manifest.permissions),
        dependencies=list(manifest.dependencies),
        tools=[
            PluginToolOut(
                name=tool.name,
                display_name=tool.display_name or tool.name,
                description=tool.description[:MAX_TOOL_LABEL],
                risk_hint=_risk_hint(manifest.type.value, tool.composes),
                composes=list(tool.composes),
            )
            for tool in manifest.tools
        ],
    )


def _risk_hint(plugin_type: str, composes: tuple[str, ...]) -> str:
    """给用户的风险提示。

    进程插件**一律**需要确认（它执行第三方代码），与它声明了什么无关。
    这一处曾经错了：进程插件没有 composes，于是被标成"只读"——
    那会让用户在预览里以为它不会执行外部代码。
    """
    if plugin_type == "process":
        return "需要确认（执行外部代码）"
    return "需要确认" if _composes_write(composes) else "只读"


def _composes_write(composes: tuple[str, ...]) -> bool:
    # 本期内建工具里只有 create_task 是写操作。这里用**保守判断**：
    # 除已知只读工具外，一律按写处理（新增写工具时不会漏标）。
    read_only = {"builtin.search_memories", "builtin.list_tasks"}
    return any(item not in read_only for item in composes)


@router.get("", response_model=PluginListOut, summary="列出插件")
async def list_plugins(request: Request) -> PluginListOut:
    items = [_to_out(plugin) for plugin in _registry(request).list()]

    from app.plugins.process import sandbox_available

    available, reason = sandbox_available()
    return PluginListOut(
        items=items,
        total=len(items),
        sandbox_available=available,
        sandbox_reason=reason,
    )


@router.get("/preview", response_model=PreviewOut, summary="安装前预览")
async def preview_plugin(
    request: Request,
    directory: Annotated[str, Query(min_length=1, max_length=128)],
) -> PreviewOut:
    """安装前展示来源、权限、依赖与风险（§8.5 要求）。"""
    source = _resolve_import_dir(request, directory)
    try:
        manifest = load_manifest(source)
    except PluginManifestError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="PLUGIN_MANIFEST_INVALID",
            message=str(exc),
        ) from exc

    warnings: list[str] = []
    if manifest.type.value == "process":
        from app.plugins.process import sandbox_available

        available, reason = sandbox_available()
        if not available:
            # 装得上也跑不了——必须现在就说清楚。
            warnings.append(f"沙箱不可用，该进程插件将无法执行：{reason}")
        else:
            warnings.append(
                "该插件会在沙箱中运行外部代码：无网络、只读的系统目录、"
                f"内存 {manifest.limits.memory_mb if manifest.limits else 256}MB、"
                f"CPU {manifest.limits.cpu_seconds if manifest.limits else 10} 秒上限。"
            )
    if manifest.dependencies:
        warnings.append(f"依赖其他插件：{', '.join(manifest.dependencies)}")
    if manifest.permissions:
        warnings.append(f"声明了权限：{', '.join(manifest.permissions)}（声明不等于授权）")
    if manifest.type.value == "process" or any(
        _composes_write(tool.composes) for tool in manifest.tools
    ):
        warnings.append("该插件包含写操作或执行外部代码，调用时会要求你确认。")

    return PreviewOut(
        id=manifest.id,
        name=manifest.name,
        version=manifest.version,
        description=manifest.description,
        permissions=list(manifest.permissions),
        dependencies=list(manifest.dependencies),
        tools=[
            PluginToolOut(
                name=tool.name,
                display_name=tool.display_name or tool.name,
                description=tool.description[:MAX_TOOL_LABEL],
                risk_hint=_risk_hint(manifest.type.value, tool.composes),
                composes=list(tool.composes),
            )
            for tool in manifest.tools
        ],
        warnings=warnings,
    )


@router.post("/install", response_model=PluginOut, summary="从本地目录安装")
async def install_plugin(payload: InstallIn, request: Request) -> PluginOut:
    source = _resolve_import_dir(request, payload.directory)
    try:
        installed = _registry(request).install_from_directory(source, enabled=payload.enabled)
    except PluginManifestError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="PLUGIN_MANIFEST_INVALID",
            message=str(exc),
        ) from exc
    except PluginError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_409_CONFLICT, code=exc.code, message=str(exc)
        ) from exc

    # 安装完成后再登记工具（如果已启用）。
    if installed.enabled:
        _activate(request, installed.id)
    return _to_out(installed)


@router.post("/{plugin_id}/enable", response_model=PluginOut, summary="启用插件")
async def enable_plugin(plugin_id: str, request: Request) -> PluginOut:
    return _set_enabled(request, plugin_id, True)


@router.post("/{plugin_id}/disable", response_model=PluginOut, summary="禁用插件")
async def disable_plugin(plugin_id: str, request: Request) -> PluginOut:
    return _set_enabled(request, plugin_id, False)


@router.delete("/{plugin_id}", response_model=OkOut, summary="卸载插件（保留目录）")
async def uninstall_plugin(plugin_id: str, request: Request) -> OkOut:
    registry = _registry(request)
    try:
        message = registry.uninstall(plugin_id)
    except PluginError as exc:
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND, code=exc.code, message=str(exc)
        ) from exc
    _deactivate(request, plugin_id)
    return OkOut(message=message)


def _set_enabled(request: Request, plugin_id: str, enabled: bool) -> PluginOut:
    registry = _registry(request)
    try:
        installed = registry.set_enabled(plugin_id, enabled)
    except PluginError as exc:
        code = (
            status.HTTP_404_NOT_FOUND
            if exc.code == "PLUGIN_NOT_FOUND"
            else status.HTTP_409_CONFLICT
        )
        raise PublicMessageError(status_code=code, code=exc.code, message=str(exc)) from exc

    if enabled:
        _activate(request, plugin_id)
    else:
        _deactivate(request, plugin_id)
    return _to_out(installed)


def _activate(request: Request, plugin_id: str) -> None:
    """把已启用插件的工具登记进注册表与能力页。"""
    registry: PluginRegistry = _registry(request)
    installed = registry.get(plugin_id)
    if installed is None or not installed.ok or installed.manifest is None:
        return

    from app.capabilities import ToolRegistry
    from app.plugins.declarative import (
        manifest_for_plugin_tool,
        register_plugin_tools,
        register_process_plugin,
    )
    from app.plugins.manifest import PluginType

    tools: ToolRegistry = request.app.state.tool_registry
    if installed.manifest.type is PluginType.PROCESS:
        result = register_process_plugin(
            installed.manifest,
            plugin_dir=registry.root / installed.directory,
            registry=tools,
        )
    else:
        result = register_plugin_tools(installed.manifest, registry=tools)

    for name, reason in result.skipped:
        logger.warning("插件工具登记失败", extra={"tool": name, "reason": reason})

    for spec in installed.manifest.tools:
        if f"plugin__{spec.name}" in result.registered:
            request.app.state.capability_registry.register(
                manifest_for_plugin_tool(installed.manifest, spec)
            )


def _deactivate(request: Request, plugin_id: str) -> None:
    """从两份注册表移除该插件的工具。

    实测缺陷：原先只从工具注册表移除，能力页仍然列着它——
    用户会以为禁用后仍可调用。两处必须一致。
    """
    registry: PluginRegistry = _registry(request)
    installed = registry.get(plugin_id)
    specs = installed.manifest.tools if installed and installed.manifest else ()
    manifest_id = installed.manifest.id if installed and installed.manifest else plugin_id

    tools = request.app.state.tool_registry
    capabilities = request.app.state.capability_registry
    for spec in specs:
        tools.remove(f"plugin__{spec.name}")
        capabilities.unregister(f"plugin.{manifest_id}.{spec.name}")


def _resolve_import_dir(request: Request, directory: str):  # type: ignore[no-untyped-def]
    """把用户给的目录名解析到受限的导入目录内。

    不接受绝对路径、不接受 `..`：一个 HTTP 请求不能把机器上任意目录
    "装"进系统。用户需要先把插件目录放进导入目录。
    """
    from pathlib import Path

    name = directory.strip()
    if not name or "/" in name or "\\" in name or name in {".", ".."} or name.startswith("."):
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="PLUGIN_PATH_INVALID",
            message="目录名不合法：只能是不含路径分隔符的普通目录名。",
        )

    import_root: Path = request.app.state.settings.data_dir / "plugin-import"
    source = (import_root / name).resolve()
    if not source.is_relative_to(import_root.resolve()):
        # 双保险：即使拼接出逃逸路径也拒绝。
        raise PublicMessageError(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            code="PLUGIN_PATH_INVALID",
            message="目录名不合法。",
        )
    if not source.is_dir():
        raise PublicMessageError(
            status_code=status.HTTP_404_NOT_FOUND,
            code="PLUGIN_SOURCE_NOT_FOUND",
            message=f"导入目录中找不到 {name}。请先把插件目录放到 {import_root}。",
        )
    return source
