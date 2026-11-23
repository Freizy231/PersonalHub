"""持久化作业队列（AGENT.md §4.14、M7）。

设计要点：
- 使用 SQLite 表而非 Redis/Kafka（§4.14 明确禁止引入这些）。
- "至少一次投递 + 幂等"：不承诺 exactly-once，靠 idempotency_key 保证副作用不重复。
- 作业与业务数据尽量在同一事务中提交（Transactional Outbox 思路）。

与 tasks 表的区别（重要）：
  tasks      = 用户记录的一件事（"学习 TypeScript"），M6 建立
  job_queue  = 可被 Worker 执行的作业（"给任务 X 发提醒"），M7 建立
两者不合并：混在一张表会让"用户事项"和"执行单元"的生命周期互相纠缠。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.conversation import DEFAULT_WORKSPACE_ID


class JobStatus(StrEnum):
    PENDING = "PENDING"  # 等待执行
    RUNNING = "RUNNING"  # 已被 Worker 领取
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"  # 重试耗尽或不可重试错误
    CANCELLED = "CANCELLED"


class Job(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "job_queue"

    # 作业类型，由 Worker 注册表解析到具体处理器。
    job_type: Mapped[str] = mapped_column(String(64), nullable=False)
    # 处理器需要的参数，JSON 文本。与 tasks 的字段分开，避免耦合。
    payload: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    status: Mapped[str] = mapped_column(String(16), nullable=False, default=JobStatus.PENDING)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)

    # 最早可执行时间：退避重试靠它实现，无需额外定时器。
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )

    # 幂等键：同类型 + 同键的作业不应产生重复副作用（§4.14）。
    # 用唯一索引强制，使"重复入队"在数据库层就被拒绝。
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    workspace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_WORKSPACE_ID
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED')",
            name="status_allowed",
        ),
        CheckConstraint("attempts >= 0 AND max_attempts > 0", name="attempts_sane"),
        # Worker 领取作业的核心查询：按状态 + 可执行时间排序。
        Index("ix_job_queue_claim", "status", "available_at", "created_at"),
        # 幂等键唯一：同键的作业只能入队一次。
        Index("ix_job_queue_idempotency", "idempotency_key", unique=True),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Job {self.job_type!r} {self.status!r}>"
