"""记忆模型（AGENT.md §4.4、§5.1）。

生命周期：
    PROPOSED（AI 提取的候选，未经用户确认）
      -> APPROVED（用户批准，可被检索注入上下文）
      -> DELETED（用户删除）

设计说明：
规范 §5.1 列出 memory_items / memory_sources / memory_proposals 三张表，并注明
"按需建立"。这里用**单表 memory_items + status** 表达同一生命周期，
避免 proposals 与 items 两张表的同一实体产生数据漂移；
provenance 由独立的 memory_sources 承担（规范要求记忆必须有来源）。

关键约束（§4.4）：
- 未经用户批准的 PROPOSED 记录**绝不**作为已确认事实注入模型上下文。
- 用户拒绝/删除的内容必须留痕，防止后台以同一来源反复写回。
"""

from __future__ import annotations

import hashlib
import re
from enum import StrEnum
from typing import TYPE_CHECKING

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.conversation import DEFAULT_WORKSPACE_ID

if TYPE_CHECKING:
    pass


class MemoryStatus(StrEnum):
    PROPOSED = "PROPOSED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    DELETED = "DELETED"


class MemoryKind(StrEnum):
    """记忆分类（§4.4）：区分稳定事实、经历与工作方法。"""

    SEMANTIC = "SEMANTIC"  # 事实、偏好
    EPISODIC = "EPISODIC"  # 发生过的事件
    PROCEDURAL = "PROCEDURAL"  # 用户认可的方法/流程


class Sensitivity(StrEnum):
    """数据敏感等级（§4.11）。

    当前阶段只**记录与展示**，不做外发阻断（决策 D-008 / 用户选择 B）。
    保留该字段是为了将来能按级别筛选、提醒或拦截，而不必回填历史数据。
    """

    PUBLIC = "PUBLIC"
    PERSONAL = "PERSONAL"
    SENSITIVE = "SENSITIVE"
    SECRET = "SECRET"


MAX_MEMORY_CONTENT = 2_000


def content_fingerprint(content: str) -> str:
    """内容指纹，用于去重。

    规范化：去首尾空白、折叠内部空白、转小写、去掉标点。
    目的是让"我偏好项目驱动学习"与"我 偏好项目驱动学习。"被视为同一条，
    从而在用户拒绝后不再重复提案。
    """
    normalized = re.sub(r"\s+", "", content.strip().lower())
    normalized = re.sub(r"[，。！？、,.!?;；:：\"'“”‘’()（）\[\]【】]", "", normalized)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


class MemoryItem(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "memory_items"

    content: Mapped[str] = mapped_column(Text, nullable=False)
    # 去重与"拒绝后不重复提案"的依据
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False, index=True)

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=MemoryStatus.PROPOSED)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default=MemoryKind.SEMANTIC)
    sensitivity: Mapped[str] = mapped_column(
        String(16), nullable=False, default=Sensitivity.PERSONAL
    )

    # 模型对该条目的置信度（0-100），用户可见，不参与自动批准。
    confidence: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    workspace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_WORKSPACE_ID
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")

    sources: Mapped[list[MemorySource]] = relationship(
        back_populates="memory",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PROPOSED', 'APPROVED', 'REJECTED', 'DELETED')",
            name="status_allowed",
        ),
        CheckConstraint(
            "kind IN ('SEMANTIC', 'EPISODIC', 'PROCEDURAL')",
            name="kind_allowed",
        ),
        CheckConstraint(
            "sensitivity IN ('PUBLIC', 'PERSONAL', 'SENSITIVE', 'SECRET')",
            name="sensitivity_allowed",
        ),
        CheckConstraint("confidence >= 0 AND confidence <= 100", name="confidence_range"),
        # 检索只取 APPROVED，按工作空间过滤，建立复合索引。
        Index("ix_memory_workspace_status", "workspace_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"<MemoryItem id={self.id!r} status={self.status!r}>"


class MemorySource(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """记忆来源（§4.4 要求记录原始来源与产生方式）。"""

    __tablename__ = "memory_sources"

    memory_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("memory_items.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 支持该记忆的原文片段。属于用户数据，仅本地保存，不进日志。
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, default="")

    memory: Mapped[MemoryItem] = relationship(back_populates="sources")

    __table_args__ = (
        CheckConstraint(
            "source_type IN ('conversation', 'message', 'user_edit')",
            name="source_type_allowed",
        ),
        Index("ix_memory_sources_memory", "memory_id"),
    )
