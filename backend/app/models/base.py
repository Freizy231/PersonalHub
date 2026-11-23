"""ORM 基类与公共列约定（AGENT.md §5.2）。

约束：
- 外部标识使用 UUID 字符串，不依赖自增 ID。
- 所有时间统一存 UTC 并带时区，展示时前端本地化。
- 重要实体带 owner / workspace scope，为 M6 治理阶段做准备。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import DateTime, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# 显式命名约束，保证迁移与测试中的约束名稳定可预期。
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def new_uuid() -> str:
    """生成 32 位十六进制 UUID（不带连字符），作为对外标识。"""
    return uuid.uuid4().hex


def utc_now() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    """创建与更新时间。

    默认值在 Python 侧计算（而不是 SQLite 的 CURRENT_TIMESTAMP），
    以保证时区信息一致；更新时间由 ORM 在 UPDATE 时自动刷新。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now
    )


class UuidPrimaryKeyMixin:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_uuid)
