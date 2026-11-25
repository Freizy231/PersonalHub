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

from app.api.deps import MemoryRepoDep, SessionDep
from app.models.memory import MemoryStatus
from app.schemas.memory import MemoryListOut, MemoryOut, MemoryUpdate, SimilarMemoryOut

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
    session: SessionDep,
    status_filter: Annotated[MemoryStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    q: Annotated[str, Query(max_length=200, description="按内容搜索")] = "",
) -> MemoryListOut:
    items = repo.list(status=status_filter, limit=limit, offset=offset, query=q)

    similar = _similar_map(session, items)
    return MemoryListOut(
        items=[
            MemoryOut.model_validate(item).model_copy(update={"similar": similar.get(item.id, [])})
            for item in items
        ],
        total=repo.count(status=status_filter, query=q),
        pending_count=repo.count(status=MemoryStatus.PROPOSED),
        limit=limit,
        offset=offset,
        query=q,
    )


def _similar_map(session, items):  # type: ignore[no-untyped-def]
    """为当前页的每条记忆附上"和它很像的其他条目"（迭代 2 第 4 项）。

    比较范围：**同状态**下的全部记忆（不受搜索词限制）——如果用户的搜索
    只命中了一条，恰恰更需要知道还有一条几乎一样的没被搜索框过滤掉。

    只对 PROPOSED 计算：提示的目的是"别连续批准重复项"。已批准记忆之间
    的相似对用户没有可执行动作，显示出来只是噪音。

    这是纯计算，不写库、不改数据、不自动合并——判断权在用户。
    """
    from sqlalchemy import select

    from app.models.memory import MemoryItem
    from app.services.similarity import find_similar

    proposed = [item for item in items if item.status == MemoryStatus.PROPOSED.value]
    if not proposed:
        return {}

    workspace_id = proposed[0].workspace_id
    pool = [
        (row.id, row.content)
        for row in session.scalars(
            select(MemoryItem).where(
                MemoryItem.workspace_id == workspace_id,
                MemoryItem.status == MemoryStatus.PROPOSED.value,
            )
        )
    ]

    # 比较池每次查询一次、所有条目共用：避免"每条记忆各查一次库"的 N+1。
    result: dict[str, list] = {}  # type: ignore[type-arg]
    for item in proposed:
        others = [(oid, content) for oid, content in pool if oid != item.id]
        matches = find_similar(item.content, others)
        if matches:
            result[item.id] = [
                SimilarMemoryOut(id=oid, content=content, score=round(score, 2))
                for oid, content, score in matches
            ]
    return result


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
