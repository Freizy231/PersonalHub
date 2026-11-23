"""记忆 Schema（§7.1）。"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from app.models.memory import MAX_MEMORY_CONTENT


def _clean(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("记忆内容不能为空")
    if len(cleaned) > MAX_MEMORY_CONTENT:
        raise ValueError(f"记忆内容不能超过 {MAX_MEMORY_CONTENT} 个字符")
    return cleaned


class MemorySourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source_type: str
    source_id: str | None
    excerpt: str


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    content: str
    status: str
    kind: str
    sensitivity: str
    confidence: int
    created_at: datetime
    updated_at: datetime
    sources: list[MemorySourceOut] = []

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class MemoryListOut(BaseModel):
    items: list[MemoryOut]
    total: int
    limit: int
    offset: int
    # 待审核数量，便于首页/导航显示角标，避免用户漏看。
    pending_count: int


class MemoryUpdate(BaseModel):
    """用户修改记忆内容。

    只允许改内容：分类与敏感级别由系统与用户审核共同决定，
    提供单独接口比让客户端自由改写更可控。
    """

    model_config = ConfigDict(extra="forbid")

    content: str = Field(min_length=1, max_length=MAX_MEMORY_CONTENT)

    _validate_content = field_validator("content")(_clean)
