"""用户任务接口（`tasks` 表，D-013）。

    GET    /api/v1/user-tasks                 列出用户任务（可按状态过滤）
    POST   /api/v1/user-tasks/{id}/complete   标记完成
    POST   /api/v1/user-tasks/{id}/reopen     重新打开
    DELETE /api/v1/user-tasks/{id}            删除

**这不是「后台作业」的接口。** 作业（Worker 执行记录，`job_queue`）在
`/api/v1/jobs`（D-013）。两者刻意不共用路径空间：把它们放进同一个
`/tasks` 前缀，正是此前界面把作业当任务展示、而用户任务无处可看的根源。

创建只发生在对话内：模型调用 `create_task`，经用户审批后由 Tool Broker 落库。
这里不提供创建接口——绕过审批直接建任务会破坏 §2.4 的确认要求。

安全：所有查询与变更都限定当前工作空间；不存在与无权访问一律 404，
不通过错误差异泄露资源是否存在。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from app.api.deps import SessionDep
from app.models.task import TaskStatus
from app.repositories.tasks import TaskRepository
from app.schemas.tasks import UserTaskListOut, UserTaskOut

router = APIRouter(prefix="/user-tasks", tags=["user-tasks"])

MAX_PAGE_SIZE = 200


def _not_found() -> HTTPException:
    # 统一错误处理器会给出安全的通用文案。
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND)


def _require(repo: TaskRepository, task_id: str):  # type: ignore[no-untyped-def]
    task = repo.get_in_workspace(task_id)
    if task is None:
        raise _not_found()
    return task


@router.get("", response_model=UserTaskListOut, summary="列出我的任务")
async def list_user_tasks(
    session: SessionDep,
    status_filter: Annotated[TaskStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE_SIZE)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserTaskListOut:
    repo = TaskRepository(session)
    items = repo.list(status=status_filter, limit=limit, offset=offset)
    return UserTaskListOut(
        items=[UserTaskOut.model_validate(item) for item in items],
        total=repo.count(status=status_filter),
        open_count=repo.count(status=TaskStatus.OPEN),
        limit=limit,
        offset=offset,
    )


@router.post("/{task_id}/complete", response_model=UserTaskOut, summary="标记任务完成")
async def complete_user_task(task_id: str, session: SessionDep) -> UserTaskOut:
    repo = TaskRepository(session)
    task = repo.set_status(_require(repo, task_id), TaskStatus.DONE)
    return UserTaskOut.model_validate(task)


@router.post("/{task_id}/reopen", response_model=UserTaskOut, summary="重新打开任务")
async def reopen_user_task(task_id: str, session: SessionDep) -> UserTaskOut:
    repo = TaskRepository(session)
    task = repo.set_status(_require(repo, task_id), TaskStatus.OPEN)
    return UserTaskOut.model_validate(task)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT, summary="删除任务")
async def delete_user_task(task_id: str, session: SessionDep) -> None:
    repo = TaskRepository(session)
    repo.delete(_require(repo, task_id))
