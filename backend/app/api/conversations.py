"""会话 CRUD 接口（AGENT.md §7.1 候选契约）。

仅暴露 MVP 需要的操作。所有查询都带 workspace scope；
找不到资源时统一返回 404，不区分"不存在"与"不属于当前工作空间"，
避免通过错误差异推断他人数据是否存在。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ConversationRepoDep
from app.schemas.conversations import (
    ConversationCreate,
    ConversationListOut,
    ConversationOut,
    ConversationRename,
)

router = APIRouter(prefix="/conversations", tags=["conversations"])

MAX_PAGE_SIZE = 200


def _not_found() -> HTTPException:
    # 具体文案由统一错误处理器按状态码给出，这里只提供状态。
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.get("", response_model=ConversationListOut, summary="列出会话")
async def list_conversations(
    repo: ConversationRepoDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConversationListOut:
    items = repo.list(limit=limit, offset=offset)
    return ConversationListOut(
        items=[ConversationOut.model_validate(item) for item in items],
        total=repo.count(),
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=ConversationOut,
    status_code=status.HTTP_201_CREATED,
    summary="创建会话",
)
async def create_conversation(
    payload: ConversationCreate,
    repo: ConversationRepoDep,
) -> ConversationOut:
    conversation = repo.create(title=payload.title.strip())
    return ConversationOut.model_validate(conversation)


@router.get("/{conversation_id}", response_model=ConversationOut, summary="获取会话")
async def get_conversation(
    conversation_id: str,
    repo: ConversationRepoDep,
) -> ConversationOut:
    conversation = repo.get_in_workspace(conversation_id)
    if conversation is None:
        raise _not_found()
    return ConversationOut.model_validate(conversation)


@router.patch("/{conversation_id}", response_model=ConversationOut, summary="重命名会话")
async def rename_conversation(
    conversation_id: str,
    payload: ConversationRename,
    repo: ConversationRepoDep,
) -> ConversationOut:
    conversation = repo.get_in_workspace(conversation_id)
    if conversation is None:
        raise _not_found()
    updated = repo.rename(conversation, payload.title.strip())
    return ConversationOut.model_validate(updated)


@router.delete(
    "/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="删除会话",
)
async def delete_conversation(
    conversation_id: str,
    repo: ConversationRepoDep,
) -> None:
    conversation = repo.get_in_workspace(conversation_id)
    if conversation is None:
        raise _not_found()
    repo.delete(conversation)
