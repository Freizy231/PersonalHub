"""能力接口（AGENT.md §4.9、M8 第 1–2 项）。

    GET  /api/v1/capabilities                 列出已登记能力（可检索、可筛选）
    GET  /api/v1/capabilities/types           系统里实际存在的类型及数量
    GET  /api/v1/capabilities/{id}            获取单个能力（默认最高版本）
    GET  /api/v1/capabilities/{id}/versions   列出该能力的全部版本
    GET  /api/v1/capabilities/health/deps     依赖完整性与依赖环

安全：

- 只读。M8 本阶段不提供安装、卸载、调用接口——
  安装与调用需要单独的授权流程（§8.5），不能和"查看"混在一起先做出来。
- **只返回 Manifest 描述**，绝不返回 handler、路径、密钥或任何运行时对象。
  Manifest 里本来就不含这些字段，这是设计上的保证，不是靠过滤。
- Manifest 中的 permissions 只是**声明**。界面上要如实表达"声明不等于授权"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict

from app.capabilities.manifest import CapabilityManifest, CapabilityType
from app.capabilities.registry import CapabilityRegistry
from app.core.errors import PublicMessageError

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


class CapabilityOut(BaseModel):
    """能力的对外描述。字段与 §7.2 的 Manifest 清单一一对应。"""

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    version: str
    type: str
    description: str
    input_schema: dict[str, object]
    output_schema: dict[str, object]
    runtime: str
    permissions: list[str]
    dependencies: list[str]
    compatibility: str
    source: str
    signature: str | None

    @classmethod
    def from_manifest(cls, manifest: CapabilityManifest) -> CapabilityOut:
        return cls(**manifest.to_dict())


class CapabilityListOut(BaseModel):
    items: list[CapabilityOut]
    total: int
    # 明确告知前端：声明不是授权，避免界面把它画成"已开启的能力"。
    notice: str = (
        "这里展示的是能力声明。声明了权限不代表已被授权，"
        "实际调用仍由策略引擎逐次判断，写操作还需要你确认。"
    )


class TypeCountOut(BaseModel):
    """一种能力类型及其实例数。"""

    type: str
    count: int


class TypeListOut(BaseModel):
    """系统里**实际存在**的能力类型。

    前端据此渲染筛选下拉框：只列出有数据的类型。
    此前前端写死六类（含 `workflow`），而后端从未注册过工作流能力，
    于是"工作流"是个永远筛不出东西的选项——用户会以为功能坏了。
    """

    items: list[TypeCountOut]
    total: int


class DependencyHealthOut(BaseModel):
    """依赖健康度：缺失依赖与依赖环。"""

    missing: dict[str, list[str]]
    cycle: list[str]
    healthy: bool


def _registry(request: Request) -> CapabilityRegistry:
    registry: CapabilityRegistry | None = getattr(request.app.state, "capability_registry", None)
    if registry is None:
        # 理论上不会发生（create_app 必然注册）；给明确 503 而不是 500。
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="能力注册表尚未初始化。",
        )
    return registry


# 注意：/health/deps 必须声明在 /{capability_id} 之前，
# 否则 "health" 会被当成能力 id 匹配掉。
@router.get("/health/deps", response_model=DependencyHealthOut, summary="依赖健康度")
async def dependency_health(request: Request) -> DependencyHealthOut:
    registry = _registry(request)
    missing = registry.missing_dependencies()
    cycle = registry.check_dependency_cycle()
    return DependencyHealthOut(
        missing={key: list(value) for key, value in missing.items()},
        cycle=list(cycle),
        healthy=not missing and not cycle,
    )


@router.get("", response_model=CapabilityListOut, summary="列出能力")
async def list_capabilities(
    request: Request,
    q: Annotated[str, Query(max_length=200, description="按 id 或名称检索")] = "",
    type: Annotated[str | None, Query(description="按类型筛选，如 tool")] = None,
    source: Annotated[str | None, Query(description="按来源筛选，如 builtin")] = None,
) -> CapabilityListOut:
    registry = _registry(request)

    type_filter: CapabilityType | None = None
    if type is not None:
        try:
            type_filter = CapabilityType(type)
        except ValueError:
            options = " / ".join(item.value for item in CapabilityType)
            # 普通 HTTPException 的 detail 会被统一错误处理丢弃（只映射成
            # "请求处理失败。"），用户因此完全不知道该怎么改。这里用
            # PublicMessageError 透出**本模块写死的**可选值列表，安全且可操作。
            raise PublicMessageError(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                code="UNKNOWN_CAPABILITY_TYPE",
                message=f"未知的能力类型：{type}（可选：{options}）",
            ) from None

    result = registry.search(query=q, type=type_filter, source=source)
    return CapabilityListOut(
        items=[CapabilityOut.from_manifest(item) for item in result.items],
        total=result.total,
    )


@router.get("/types", response_model=TypeListOut, summary="实际存在的能力类型")
async def list_types(request: Request) -> TypeListOut:
    """返回有能力的类型及数量（按固定顺序）。

    **必须注册在 `/{capability_id}` 之前**：否则 "types" 会被当成能力 id
    去查，返回 404 而不是类型列表。
    """
    registry = _registry(request)
    counts = registry.count_by_type()
    # 按枚举定义顺序输出，而不是字典顺序：界面下拉框的顺序要稳定。
    ordered = [
        TypeCountOut(type=item.value, count=counts[item.value])
        for item in CapabilityType
        if counts.get(item.value)
    ]
    return TypeListOut(items=ordered, total=sum(item.count for item in ordered))


@router.get("/{capability_id}", response_model=CapabilityOut, summary="获取能力")
async def get_capability(
    capability_id: str,
    request: Request,
    version: Annotated[str | None, Query(max_length=64)] = None,
) -> CapabilityOut:
    manifest = _registry(request).get(capability_id, version)
    if manifest is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CapabilityOut.from_manifest(manifest)


@router.get(
    "/{capability_id}/versions",
    response_model=CapabilityListOut,
    summary="列出能力的所有版本",
)
async def list_versions(capability_id: str, request: Request) -> CapabilityListOut:
    versions = _registry(request).versions(capability_id)
    if not versions:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return CapabilityListOut(
        items=[CapabilityOut.from_manifest(item) for item in versions],
        total=len(versions),
    )
