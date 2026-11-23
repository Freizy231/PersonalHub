"""作业队列操作（AGENT.md §4.14、M7）。

核心不变量：
1. **幂等入队**：相同 idempotency_key 只能存在一个作业。
   重复入队不报错，返回已有作业——这让"至少一次投递"不会产生重复副作用。
2. **原子领取**：领取用条件更新，避免两个 Worker 抢到同一作业。
3. **退避重试**：失败后设置 available_at，不依赖额外定时器。
4. **状态机**：只允许合法转换，非法转换被拒绝而不是静默修改。

为什么不用 SELECT ... FOR UPDATE：
SQLite 不支持行级锁。这里用 "UPDATE ... WHERE status='PENDING'" 的条件更新，
影响行数为 1 即代表领取成功。SQLite 的写操作本身是串行的，配合 WAL 足够安全。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.job import Job, JobStatus
from app.models.task_run import RunStatus, TaskEvent, TaskRun

logger = logging.getLogger(__name__)

# 重试退避基数：第 n 次重试等待 BASE_BACKOFF * 2^(n-1) 秒。
BASE_BACKOFF_SECONDS = 5
MAX_BACKOFF_SECONDS = 300


class JobError(RuntimeError):
    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def enqueue(
    session: Session,
    *,
    job_type: str,
    payload: dict[str, Any] | None = None,
    idempotency_key: str | None = None,
    workspace_id: str = "default",
    available_at: datetime | None = None,
    max_attempts: int = 3,
) -> tuple[Job, bool]:
    """入队一个作业。返回 (作业, 是否新建)。

    幂等：若同 idempotency_key 已存在，直接返回它且 created=False。
    这让调用方可以放心重试入队，不必先查后写（那会有竞态）。
    """
    if idempotency_key:
        existing = session.scalars(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).first()
        if existing is not None:
            return existing, False

    job = Job(
        job_type=job_type,
        payload=json.dumps(payload or {}, ensure_ascii=False),
        status=JobStatus.PENDING.value,
        workspace_id=workspace_id,
        idempotency_key=idempotency_key,
        available_at=available_at or _now(),
        max_attempts=max_attempts,
    )
    session.add(job)
    try:
        session.commit()
    except IntegrityError:
        # 并发下两个请求同时入队同键作业：唯一索引会拒绝其中一个，
        # 此时返回已存在的那一个，行为仍是"只存在一个作业"。
        session.rollback()
        existing = session.scalars(
            select(Job).where(Job.idempotency_key == idempotency_key)
        ).first()
        if existing is None:
            raise
        return existing, False

    session.add(TaskEvent(job_id=job.id, run_id=None, event_type="enqueued", message=job.job_type))
    session.commit()
    return job, True


def claim_next(session: Session, *, now: datetime | None = None) -> Job | None:
    """领取一个到期作业并置为 RUNNING。

    用条件 UPDATE 保证原子性：只有真正把状态从 PENDING 改成 RUNNING
    的那一次调用算领取成功。
    """
    moment = now or _now()

    # 先选出候选（按到期时间与创建时间排序，先到先做）。
    candidates = list(
        session.scalars(
            select(Job.id)
            .where(Job.status == JobStatus.PENDING.value, Job.available_at <= moment)
            .order_by(Job.available_at.asc(), Job.created_at.asc())
            .limit(10)
        )
    )
    for job_id in candidates:
        result = session.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.PENDING.value)
            .values(status=JobStatus.RUNNING.value, attempts=Job.attempts + 1)
        )
        if result.rowcount == 1:
            session.commit()
            job = session.get(Job, job_id)
            if job is not None:
                return job
        # 被别人抢走：继续尝试下一个候选。
        session.rollback()

    return None


def start_run(session: Session, job: Job) -> TaskRun:
    """为一次领取创建 TaskRun（执行实例）并记录事件。"""
    run = TaskRun(job_id=job.id, attempt=job.attempts, status=RunStatus.RUNNING.value)
    session.add(run)
    session.commit()
    session.add(
        TaskEvent(
            job_id=job.id, run_id=run.id, event_type="started", message=f"第 {run.attempt} 次尝试"
        )
    )
    session.commit()
    return run


def save_checkpoint(session: Session, run: TaskRun, checkpoint: dict[str, Any]) -> None:
    """写入检查点。

    处理器应在"可安全重放"的位置写检查点：恢复时从检查点继续，
    已经产生的副作用不会被重复执行。
    """
    run.checkpoint = json.dumps(checkpoint, ensure_ascii=False)
    session.commit()


def complete_run(session: Session, job: Job, run: TaskRun, *, message: str = "") -> None:
    run.status = RunStatus.SUCCEEDED.value
    run.finished_at = _now()
    job.status = JobStatus.SUCCEEDED.value
    job.last_error = None
    session.add(TaskEvent(job_id=job.id, run_id=run.id, event_type="succeeded", message=message))
    session.commit()


def fail_run(
    session: Session,
    job: Job,
    run: TaskRun,
    *,
    error_code: str,
    error_message: str,
    retryable: bool = True,
) -> bool:
    """记录失败。返回是否安排了重试。"""
    run.status = RunStatus.FAILED.value
    run.finished_at = _now()
    run.error_code = error_code
    run.error_message = error_message
    job.last_error = f"{error_code}: {error_message}"[:500]

    if retryable and job.attempts < job.max_attempts:
        delay = min(BASE_BACKOFF_SECONDS * (2 ** (job.attempts - 1)), MAX_BACKOFF_SECONDS)
        job.status = JobStatus.PENDING.value
        job.available_at = _now() + timedelta(seconds=delay)
        session.add(
            TaskEvent(
                job_id=job.id,
                run_id=run.id,
                event_type="retrying",
                message=f"{error_code}，{delay} 秒后重试",
            )
        )
        session.commit()
        return True

    job.status = JobStatus.FAILED.value
    session.add(TaskEvent(job_id=job.id, run_id=run.id, event_type="failed", message=error_code))
    session.commit()
    return False


def cancel(session: Session, job_id: str) -> Job:
    """取消作业。已结束的作业不能取消（避免语义混淆）。

    取消的语义（§7.3）：停止新步骤、记录已完成的部分，
    不声称撤销了不可逆的外部操作。
    """
    job = session.get(Job, job_id)
    if job is None:
        raise JobError("作业不存在。", code="JOB_NOT_FOUND")

    if job.status in (JobStatus.SUCCEEDED.value, JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise JobError("该作业已结束，无法取消。", code="JOB_ALREADY_FINISHED")

    was_running = job.status == JobStatus.RUNNING.value
    job.status = JobStatus.CANCELLED.value
    session.add(
        TaskEvent(
            job_id=job.id,
            run_id=None,
            event_type="cancelled",
            message="执行中取消，已完成的步骤不会回滚" if was_running else "排队中取消",
        )
    )
    session.commit()
    return job


def retry(session: Session, job_id: str) -> Job:
    """手动重试一个已结束的作业。

    重置 attempts 给足完整的重试预算，并立即变为可执行。
    状态机约束：只有 FAILED / CANCELLED 可以重试——
    对 SUCCEEDED 重试会让"已完成"的语义失真，对 RUNNING 重试会与 Worker 冲突。
    """
    job = session.get(Job, job_id)
    if job is None:
        raise JobError("作业不存在。", code="JOB_NOT_FOUND")

    if job.status not in (JobStatus.FAILED.value, JobStatus.CANCELLED.value):
        raise JobError("只有失败或已取消的作业可以重试。", code="JOB_NOT_RETRYABLE")

    job.status = JobStatus.PENDING.value
    job.attempts = 0
    job.last_error = None
    job.available_at = _now()
    session.add(TaskEvent(job_id=job.id, run_id=None, event_type="enqueued", message="手动重试"))
    session.commit()
    return job


def recover_interrupted(session: Session) -> int:
    """服务启动时处理上次残留的 RUNNING 作业。

    这些作业在进程退出时正在执行，状态不可知。处理方式：
    - 执行实例标为 INTERRUPTED（不谎称成功或失败）
    - 作业放回 PENDING（若还有重试次数）或标为 FAILED
      —— 依靠处理器的幂等性，重新执行是安全的

    这是"至少一次"语义的直接体现：可能重复执行，副作用由幂等保证。
    """
    running = list(session.scalars(select(Job).where(Job.status == JobStatus.RUNNING.value)))
    recovered = 0
    for job in running:
        open_runs = list(
            session.scalars(
                select(TaskRun).where(
                    TaskRun.job_id == job.id, TaskRun.status == RunStatus.RUNNING.value
                )
            )
        )
        for run in open_runs:
            run.status = RunStatus.INTERRUPTED.value
            run.finished_at = _now()

        if job.attempts < job.max_attempts:
            job.status = JobStatus.PENDING.value
            job.available_at = _now()
            event_type = "recovered"
            message = "服务重启，作业重新排队"
        else:
            job.status = JobStatus.FAILED.value
            event_type = "failed"
            message = "服务重启且重试已耗尽"

        session.add(TaskEvent(job_id=job.id, run_id=None, event_type=event_type, message=message))
        recovered += 1

    if recovered:
        session.commit()
        logger.info("恢复了中断的作业", extra={"count": recovered})
    return recovered


def expire_running_timeout(session: Session, *, timeout: timedelta) -> int:
    """把超时仍在 RUNNING 的作业按中断处理。

    防止 Worker 崩溃后作业永久卡在 RUNNING。
    """
    cutoff = _now() - timeout
    stuck = list(
        session.scalars(
            select(Job).where(Job.status == JobStatus.RUNNING.value, Job.updated_at <= cutoff)
        )
    )
    for job in stuck:
        job.status = JobStatus.PENDING.value
        job.available_at = _now()
        session.add(
            TaskEvent(
                job_id=job.id,
                run_id=None,
                event_type="recovered",
                message="执行超时，重新排队",
            )
        )
    if stuck:
        session.commit()
    return len(stuck)


def recent_events(session: Session, *, limit: int = 50) -> list[TaskEvent]:
    """Activity Center 用的最近事件（只读）。"""
    return list(
        session.scalars(select(TaskEvent).order_by(TaskEvent.created_at.desc()).limit(limit))
    )


def list_jobs(session: Session, *, status: JobStatus | None = None, limit: int = 50) -> list[Job]:
    stmt = select(Job)
    if status is not None:
        stmt = stmt.where(Job.status == status.value)
    return list(session.scalars(stmt.order_by(Job.created_at.desc()).limit(limit)))
