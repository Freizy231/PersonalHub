"""记忆接口（AGENT.md §7.1）。

契约：
    GET    /api/v1/memory            列出记忆（可按 status 过滤）
    GET    /api/v1/memory/{id}       获取单条
    POST   /api/v1/memory/{id}/approve
    POST   /api/v1/memory/{id}/reject
    PATCH  /api/v1/memory/{id}       修改内容
    DELETE /api/v1/memory/{id}

安全边界：
- 只有 APPROVED 的记忆会进入模型上下文（由服务层保证，见 app/services/memory.py）。
- 所有操作限定在当前工作空间，越权访问一律 404。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import MemoryRepoDep
from app.models.memory import MemoryStatus
from app.schemas.memory import MemoryListOut, MemoryOut, MemoryUpdate

router = APIRouter(prefix="/memory", tags=["memory"])

MAX_PAGE_SIZE = 200


def _require(repo: MemoryRepoDep, memory_id: str):  # type: ignore[no-untyped-def]
    item = repo.get_in_workspace(memory_id)
    if item is None:
        # 不区分"不存在"与"无权访问"，避免探测他人数据。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return item


@router.get("", response_model=MemoryListOut, summary="列出记忆")
async def list_memories(
    repo: MemoryRepoDep,
    status_filter: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MemoryListOut:
    items = repo.list(status=status_filter, limit=limit, offset=offset)
    return MemoryListOut(
        items=[MemoryOut.model_validate(item) for item in items],
        total=repo.count(status=status_filter),
        pending_count=repo.count(status=MemoryStatus.PROPOSED),
        limit=limit,
        offset=offset,
    )


@router.get("/{memory_id}", response_model=MemoryOut, summary="获取记忆")
async def get_memory(memory_id: str, repo: MemoryRepoDep) -> MemoryOut:
    return MemoryOut.model_validate(_require(repo, memory_id))


@router.post("/{memory_id}/approve", response_model=MemoryOut, summary="批准记忆")
async def approve_memory(memory_id: str, repo: MemoryRepoDep) -> MemoryOut:
    item = _require(repo, memory_id)
    if item.status == MemoryStatus.APPROVED.value:
        # 幂等：重复批准不报错，直接返回当前状态。
        return MemoryOut.model_validate(item)
    return MemoryOut.model_validate(repo.approve(item))


@router.post("/{memory_id}/reject", response_model=MemoryOut, summary="拒绝记忆")
async def reject_memory(memory_id: str, repo: MemoryRepoDep) -> MemoryOut:
    item = _require(repo, memory_id)
    if item.status == MemoryStatus.REJECTED.value:
        return MemoryOut.model_validate(item)
    return MemoryOut.model_validate(repo.reject(item))


@router.patch("/{memory_id}", response_model=MemoryOut, summary="修改记忆内容")
async def update_memory(memory_id: str, payload: MemoryUpdate, repo: MemoryRepoDep) -> MemoryOut:
    item = _require(repo, memory_id)
    return MemoryOut.model_validate(repo.update_content(item, payload.content))


@router.delete("/{memory_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除记忆")
async def delete_memory(memory_id: str, repo: MemoryRepoDep) -> None:
    item = _require(repo, memory_id)
    repo.delete(item)
