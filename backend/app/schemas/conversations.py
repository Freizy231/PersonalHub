"""请求/响应 Schema（Pydantic，AGENT.md §7.1）。

约束：
- 所有外部输入在此校验，数据库侧再做长度/非空约束作为第二道防线。
- 响应不含内部字段（owner_id 等），避免暴露实现细节。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.conversation import MAX_TITLE_LENGTH


def _clean_title(value: str) -> str:
    """清洗标题：去掉首尾空白，并拒绝清洗后为空的输入。

    只在 Schema 层做 length>=1 不够：三个空格能通过 min_length，
    却会创建出空标题会话。清理必须发生在长度校验之前。
    """
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("标题不能为空")
    if len(cleaned) > MAX_TITLE_LENGTH:
        raise ValueError(f"标题不能超过 {MAX_TITLE_LENGTH} 个字符")
    return cleaned


class ConversationCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)

    _validate_title = field_validator("title")(_clean_title)


class ConversationRename(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=MAX_TITLE_LENGTH)

    _validate_title = field_validator("title")(_clean_title)


class ConversationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    created_at: datetime
    updated_at: datetime

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        """SQLite 不保存时区，读回的是 naive datetime。

        应用内所有时间都是 UTC（见 models.base.utc_now）。这里补上时区，
        保证"刚创建时的响应"与"稍后查询的响应"格式一致，
        否则前端会时而在本地时间、时而按 UTC 解析。
        """
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime) -> str:
        # 统一输出 ISO 8601 + Z，前端 new Date() 直接可用。
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class ConversationListOut(BaseModel):
    items: list[ConversationOut]
    total: int
    limit: int
    offset: int
