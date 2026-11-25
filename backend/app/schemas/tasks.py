"""用户任务 Schema（M6 建立的 `tasks` 表，D-013 术语约定）。

字段只映射现有列，不新增、不迁移。`created_via_approval_id` 是 M6 就有的列
（用于追溯"这条任务由哪次审批创建"），此前写入路径没有填它，本次补上。
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator


def _utc(value: datetime) -> datetime:
    """SQLite 读回 naive datetime；应用内一律按 UTC 处理（同 conversations）。"""
    if isinstance(value, datetime) and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


class UserTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    description: str
    status: str
    created_at: datetime
    updated_at: datetime
    # 来源：由哪次审批创建。没有则来自其它路径（例如将来的手动创建）。
    created_via_approval_id: str | None = None

    @field_validator("created_at", "updated_at", mode="before")
    @classmethod
    def _assume_utc(cls, value: datetime) -> datetime:
        return _utc(value)

    @field_serializer("created_at", "updated_at")
    def _serialize_utc(self, value: datetime) -> str:
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


class UserTaskListOut(BaseModel):
    items: list[UserTaskOut]
    total: int
    limit: int
    offset: int
    # 未完成数量：首页与导航角标用，避免用户漏看。
    open_count: int
