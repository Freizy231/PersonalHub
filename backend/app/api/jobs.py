"""作业与活动接口（AGENT.md §7.1、M7）。

    GET  /api/v1/jobs                 列出作业（可按状态过滤）
    GET  /api/v1/jobs/{id}            作业详情（含执行实例与事件）
    POST /api/v1/jobs/{id}/cancel     取消
    POST /api/v1/jobs/{id}/retry      重试失败的作业
    GET  /api/v1/activity             最近事件（Activity Center 基础版）

安全：所有查询限定当前工作空间；不返回 payload 正文之外的内部字段。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.deps import SessionDep
from app.models.job import Job, JobStatus
from app.models.task_run import TaskEvent, TaskRun
from app.workers import queue

router = APIRouter(tags=["jobs"])

WORKSPACE_ID = "default"
MAX_PAGE = 200


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_type: str
    status: str
    attempts: int
    max_attempts: int
    last_error: str | None
    available_at: str
    created_at: str
    updated_at: str
    # 输入摘要：由后端从 payload 中提取**有限长度**的安全字段生成。
    # 不返回原始 payload —— 它可能含完整用户描述，界面只需要一行摘要。
    summary: str = ""
    # 最近一次执行事件（阶段名 + 简短说明）。服务端没有时为空。
    stage: str | None = None
    stage_message: str | None = None


class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    job_id: str
    run_id: str | None
    event_type: str
    message: str
    created_at: str


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    attempt: int
    status: str
    started_at: str
    finished_at: str | None
    error_code: str | None
    # 成功后的结果摘要（例如生成应用得到的 staging_id 与标题）。
    # 处理器写入的检查点本身就是"结果"，前端据此跳转，不必再查一次。
    # 解析失败时置为 None，不让坏数据导致整个详情接口 500。
    result: dict | None = None  # type: ignore[type-arg]


class JobDetailOut(JobOut):
    runs: list[RunOut] = []
    events: list[EventOut] = []


class JobListOut(BaseModel):
    items: list[JobOut]
    total: int


class ActivityOut(BaseModel):
    items: list[EventOut]
    total: int


def _iso(value) -> str:  # type: ignore[no-untyped-def]
    # SQLite 读回的是 naive datetime，统一补 Z。
    text = value.isoformat()
    return text if value.tzinfo else text + "Z"


# payload 里允许展示给用户的字段。白名单而不是黑名单：
# payload 可能含完整用户描述或将来新增的敏感字段，默认不展示才安全。
SUMMARY_FIELDS: dict[str, tuple[str, ...]] = {
    "generate_app": ("request",),
}

MAX_SUMMARY_CHARS = 120

# 已结束的作业不再有"进行到哪一步"的概念。
TERMINAL_STATUSES = frozenset(
    {JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value}
)


def is_terminal(job_status: str) -> bool:
    return job_status in TERMINAL_STATUSES


def _summary_of(job: Job) -> str:
    """从 payload 提取一行输入摘要。

    只取白名单字段、截断到固定长度，并折叠空白——
    活动/作业列表是元数据视图（§7.4），不是用户内容的展示处。
    """
    fields = SUMMARY_FIELDS.get(job.job_type)
    if not fields:
        return ""
    data = _parse_result(job.payload)
    if not data:
        return ""
    for name in fields:
        value = data.get(name)
        if not isinstance(value, str):
            continue
        text = " ".join(value.split())
        if not text:
            continue
        if len(text) > MAX_SUMMARY_CHARS:
            text = text[:MAX_SUMMARY_CHARS].rstrip() + "…"
        return text
    return ""


def _job_out(job: Job) -> JobOut:
    return JobOut(
        id=job.id,
        job_type=job.job_type,
        status=job.status,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        last_error=job.last_error,
        available_at=_iso(job.available_at),
        created_at=_iso(job.created_at),
        updated_at=_iso(job.updated_at),
        summary=_summary_of(job),
    )


def _latest_stages(session, job_ids: list[str]) -> dict[str, tuple[str, str]]:  # type: ignore[no-untyped-def]
    """批量取每个作业的最近事件（阶段）。一次查询，避免 N+1。"""
    if not job_ids:
        return {}
    events = session.scalars(
        select(TaskEvent)
        .where(TaskEvent.job_id.in_(job_ids))
        .order_by(TaskEvent.job_id, TaskEvent.created_at.desc(), TaskEvent.id.desc())
    )
    latest: dict[str, tuple[str, str]] = {}
    for event in events:
        # 按 created_at 倒序，第一次见到的就是每个作业的最新一条。
        latest.setdefault(event.job_id, (event.event_type, event.message))
    return latest


def _event_out(event: TaskEvent) -> EventOut:
    return EventOut(
        id=event.id,
        job_id=event.job_id,
        run_id=event.run_id,
        event_type=event.event_type,
        message=event.message,
        created_at=_iso(event.created_at),
    )


def _parse_result(checkpoint: str | None) -> dict | None:  # type: ignore[type-arg]
    if not checkpoint:
        return None
    import json

    try:
        data = json.loads(checkpoint)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _require_job(session, job_id: str) -> Job:  # type: ignore[no-untyped-def]
    job = session.get(Job, job_id)
    if job is None or job.workspace_id != WORKSPACE_ID:
        # 不区分"不存在"与"无权访问"。
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    return job


@router.get("/jobs", response_model=JobListOut, summary="列出作业")
async def list_jobs(
    session: SessionDep,
    status_filter: Annotated[JobStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> JobListOut:
    stmt = select(Job).where(Job.workspace_id == WORKSPACE_ID)
    if status_filter is not None:
        stmt = stmt.where(Job.status == status_filter.value)
    jobs = list(session.scalars(stmt.order_by(Job.created_at.desc()).limit(limit)))

    stages = _latest_stages(session, [job.id for job in jobs])
    items: list[JobOut] = []
    for job in jobs:
        item = _job_out(job)
        stage = stages.get(job.id)
        # 已结束的作业不显示"阶段"：最后一次中间事件不是当前状态，
        # 显示它会让人以为还在进行。
        if stage is not None and not is_terminal(job.status):
            item.stage, item.stage_message = stage
        items.append(item)
    return JobListOut(items=items, total=len(items))


@router.get("/jobs/{job_id}", response_model=JobDetailOut, summary="作业详情")
async def get_job(job_id: str, session: SessionDep) -> JobDetailOut:
    job = _require_job(session, job_id)

    runs = list(
        session.scalars(
            select(TaskRun).where(TaskRun.job_id == job.id).order_by(TaskRun.created_at.asc())
        )
    )
    events = list(
        session.scalars(
            select(TaskEvent).where(TaskEvent.job_id == job.id).order_by(TaskEvent.created_at.asc())
        )
    )

    base = _job_out(job).model_dump()
    if events and not is_terminal(job.status):
        base["stage"] = events[-1].event_type
        base["stage_message"] = events[-1].message
    return JobDetailOut(
        **base,
        runs=[
            RunOut(
                id=r.id,
                attempt=r.attempt,
                status=r.status,
                started_at=_iso(r.started_at),
                finished_at=_iso(r.finished_at) if r.finished_at else None,
                error_code=r.error_code,
                result=_parse_result(r.checkpoint),
            )
            for r in runs
        ],
        events=[_event_out(e) for e in events],
    )


@router.post("/jobs/{job_id}/cancel", response_model=JobOut, summary="取消作业")
async def cancel_job(job_id: str, session: SessionDep) -> JobOut:
    _require_job(session, job_id)
    try:
        job = queue.cancel(session, job_id)
    except queue.JobError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc
    return _job_out(job)


@router.post("/jobs/{job_id}/retry", response_model=JobOut, summary="重试作业")
async def retry_job(job_id: str, session: SessionDep, request: Request) -> JobOut:
    _require_job(session, job_id)
    try:
        # 状态机与重置逻辑都在队列层，API 只负责 HTTP 适配。
        job = queue.retry(session, job_id)
    except queue.JobError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT) from exc

    # 唤醒 Worker，让用户不必等下一个轮询周期。
    worker = getattr(request.app.state, "worker", None)
    if worker is not None:
        worker.notify()

    return _job_out(job)


@router.get("/activity", response_model=ActivityOut, summary="最近活动")
async def activity(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 50,
) -> ActivityOut:
    events = queue.recent_events(session, limit=limit)
    return ActivityOut(items=[_event_out(e) for e in events], total=len(events))
