"""用户任务数据访问（`tasks` 表，D-013）。

与作业队列严格分开：

    tasks      = 用户记录的一件事（"学习 TypeScript"），只有 OPEN / DONE
    job_queue  = Worker 执行的作业（生成应用等），有 PENDING/RUNNING/…

所有查询带 workspace scope（§8.2）。路由层与工具层共用同一个仓储，
避免两处各写一套过滤条件而出现越权。
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.conversation import DEFAULT_WORKSPACE_ID
from app.models.task import Task, TaskStatus


class TaskRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get_in_workspace(
        self, task_id: str, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> Task | None:
        stmt = select(Task).where(Task.id == task_id, Task.workspace_id == workspace_id)
        return self._session.scalars(stmt).one_or_none()

    def list(
        self,
        status: TaskStatus | None = None,
        workspace_id: str = DEFAULT_WORKSPACE_ID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Task]:
        stmt = select(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(Task.status == status.value)
        # 未完成在前，其次按最近更新。用户打开列表最关心"还有什么没做"。
        stmt = stmt.order_by(
            (Task.status == TaskStatus.DONE.value).asc(),
            Task.updated_at.desc(),
            Task.id.desc(),
        )
        return list(self._session.scalars(stmt.limit(limit).offset(offset)))

    def count(
        self, status: TaskStatus | None = None, workspace_id: str = DEFAULT_WORKSPACE_ID
    ) -> int:
        stmt = select(func.count()).select_from(Task).where(Task.workspace_id == workspace_id)
        if status is not None:
            stmt = stmt.where(Task.status == status.value)
        return int(self._session.scalar(stmt) or 0)

    def set_status(self, task: Task, status: TaskStatus) -> Task:
        task.status = status.value
        self._session.commit()
        return task

    def delete(self, task: Task) -> None:
        self._session.delete(task)
        self._session.commit()
