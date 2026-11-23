"""任务模型（AGENT.md §5.1）。

M6 只建立最小字段，让 create_task 成为**真实可审批的写操作**。
队列、Worker、执行状态机、检查点属于 M7，届时在此表上扩展，
不在这里提前造未使用的列。

状态说明：M6 的任务只表示"用户记录了一件事"，不表示"正在后台执行某物"。
因此 FSM 保持极简：OPEN / DONE。M7 引入执行运行态时再扩充。
"""

from __future__ import annotations

from enum import StrEnum

from sqlalchemy import CheckConstraint, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UuidPrimaryKeyMixin
from app.models.conversation import DEFAULT_WORKSPACE_ID

MAX_TASK_TITLE = 200
MAX_TASK_DESCRIPTION = 4_000


class TaskStatus(StrEnum):
    OPEN = "OPEN"
    DONE = "DONE"


class Task(Base, UuidPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "tasks"

    title: Mapped[str] = mapped_column(String(MAX_TASK_TITLE), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=TaskStatus.OPEN)

    workspace_id: Mapped[str] = mapped_column(
        String(64), nullable=False, default=DEFAULT_WORKSPACE_ID
    )
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False, default="local-user")

    # 来源可追溯：由哪个会话/哪次审批创建（§5.2 任务状态变更必须可追踪）。
    created_via_approval_id: Mapped[str | None] = mapped_column(String(32), nullable=True)

    __table_args__ = (
        CheckConstraint("status IN ('OPEN', 'DONE')", name="status_allowed"),
        Index("ix_tasks_workspace_status", "workspace_id", "status"),
    )

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Task id={self.id!r} status={self.status!r}>"
