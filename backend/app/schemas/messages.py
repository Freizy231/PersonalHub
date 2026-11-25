"""消息 Schema。

安全设计（AGENT.md §8.4）：
客户端只能创建 role="user" 的消息。若允许客户端指定 system，
任何能调用 API 的脚本都能直接写入"系统指令"，构成指令注入。
assistant / system / tool 三类消息将来只由后端 Agent 运行时写入。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.message import MAX_CONTENT_LENGTH


def _clean_content(value: str) -> str:
    """去掉首尾空白并拒绝空内容。

    与标题同样的陷阱：纯空白能通过 min_length=1，却会存进一条空消息。
    只裁首尾，保留内部换行与缩进（用户可能粘贴代码）。
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("消息内容不能为空")
    if len(cleaned) > MAX_CONTENT_LENGTH:
        raise ValueError(f"消息内容不能超过 {MAX_CONTENT_LENGTH} 个字符")
    return cleaned


class MessageCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 只允许 user：见模块文档。
    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=MAX_CONTENT_LENGTH)

    _validate_content = field_validator("content")(_clean_content)


class ToolInvocationOut(BaseModel):
    """工具调用摘要。只含元数据，不含参数与结果正文。"""

    tool: str
    status: str
    error_code: str | None = None


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    role: str
    content: str
    created_at: datetime
    # 该回合调用过的工具；仅助手消息可能有值。
    tools: list[ToolInvocationOut] = []

    @field_validator("created_at", mode="before")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        # SQLite 不保存时区，与应用内其他时间字段保持一致地补 UTC。
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @field_serializer("created_at")
    def _serialize_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MessageListOut(BaseModel):
    items: list[MessageOut]
    total: int
    limit: int
    offset: int
    # 是否还有**更早**的消息未被取到（迭代 2：长会话"加载更早"）。
    #
    # 由服务端判断而不是让前端用 total 推算：total 是全会话条数，
    # 而本页可能已经从尾部取了一段，两者相减只有在 order 与 offset
    # 的含义完全对齐时才正确——这种"前端自己算"的约定最容易在
    # 改动 offset 语义时静默出错。服务端多返回一个布尔值没有代价。
    #
    # 注意语义：它只说"更早的方向还有"，不说"更新的方向还有"。
    # 会话详情页永远从最新往回加载，因此只需要前者。
    has_more: bool = False
