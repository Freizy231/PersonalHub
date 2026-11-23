"""消息接口。

挂在会话之下：/api/v1/conversations/{conversation_id}/messages

安全要点（AGENT.md §8.2）：
每个请求都必须先确认会话属于当前工作空间，再读写其消息。
否则只要能猜到 conversation_id，就能读写他人的对话。
不存在的会话一律返回 404，不区分"不存在"与"无权访问"。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import ConversationRepoDep, MessageRepoDep
from app.schemas.messages import MessageCreate, MessageListOut, MessageOut

router = APIRouter(
    prefix="/conversations/{conversation_id}/messages",
    tags=["messages"],
)

MAX_PAGE_SIZE = 200


def _require_conversation(repo: ConversationRepoDep, conversation_id: str) -> None:
    """会话不存在或不属于当前工作空间时返回 404。"""
    if repo.get_in_workspace(conversation_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)


@router.get("", response_model=MessageListOut, summary="列出会话消息")
async def list_messages(
    conversation_id: str,
    conversations: ConversationRepoDep,
    messages: MessageRepoDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MessageListOut:
    _require_conversation(conversations, conversation_id)

    items = messages.list_for_conversation(conversation_id, limit=limit, offset=offset)
    tool_map = messages.tool_invocations_for(conversation_id)

    return MessageListOut(
        items=[
            MessageOut(
                **MessageOut.model_validate(item).model_dump(exclude={"tools"}),
                tools=tool_map.get(item.id, []),
            )
            for item in items
        ],
        total=messages.count_for_conversation(conversation_id),
        limit=limit,
        offset=offset,
    )


@router.post(
    "",
    response_model=MessageOut,
    status_code=status.HTTP_201_CREATED,
    summary="追加消息",
)
async def create_message(
    conversation_id: str,
    payload: MessageCreate,
    conversations: ConversationRepoDep,
    messages: MessageRepoDep,
) -> MessageOut:
    conversation = conversations.get_in_workspace(conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    # role 由 Schema 固定为 "user"，这里显式传入可见的常量而不是 payload.role，
    # 避免将来有人放宽 Schema 时无声改变写入行为。
    message = messages.append(conversation_id, payload.content, role="user")
    # 新消息代表会话有新活动，刷新 updated_at，使列表按最近活动排序。
    conversations.touch(conversation)
    return MessageOut.model_validate(message)
