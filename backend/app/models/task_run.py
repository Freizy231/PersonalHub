"""任务执行实例与事件（AGENT.md §5.1、M7）。

TaskRun：一次具体的执行尝试，从开始到结束。
    - 支持检查点：中断后可以从 checkpoint 继续，而不是从头重来。
    - 状态与 Job 分离：Job 是"要做什么"，TaskRun 是"做过什么"。

TaskEvent：执行过程中的结构化事件，用于 Activity Center 与审计。
    只记录元数据（步骤、状态变化、错误码），不记录用户内容正文。
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    # 服务重启时无法确认状态：不谎称成功也不谎称失败，标记为待恢复。
    INTERRUPTED = "INTERRUPTED"


class TaskRun(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "task_runs"

    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("job_queue.id", ondelete="CASCADE"), nullable=False
    )
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=RunStatus.RUNNING)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # 检查点：处理器可在安全点写入，恢复时从这里继续。
    # 仅存执行进度所需的最小状态，不存放敏感内容。
    checkpoint: Mapped[str | None] = mapped_column(Text, nullable=True)

    error_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'INTERRUPTED')",
            name="status_allowed",
        ),
        Index("ix_task_runs_job", "job_id", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskRun job={self.job_id!r} attempt={self.attempt} {self.status!r}>"


class TaskEvent(Base, UuidPrimaryKeyMixin, TimestampMixin):
    """执行过程中的一个事件。

    event_type 取值见下方常量。不记录用户正文（§7.4）。
    """

    __tablename__ = "task_events"

    job_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("job_queue.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str | None] = mapped_column(
        String(32), ForeignKey("task_runs.id", ondelete="SET NULL"), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    # 简短说明，供 Activity Center 展示。不得写入敏感内容。
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('enqueued', 'started', 'progress', 'succeeded', "
            "'failed', 'cancelled', 'retrying', 'interrupted', 'recovered')",
            name="event_type_allowed",
        ),
        Index("ix_task_events_job", "job_id", "created_at"),
        Index("ix_task_events_created", "created_at"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<TaskEvent {self.event_type!r} job={self.job_id!r}>"
