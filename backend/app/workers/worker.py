"""后台 Worker（AGENT.md M7）。

在应用进程内运行一个 asyncio 循环，周期性从队列领取作业并执行。

生命周期：
    startup  -> 恢复上次中断的作业 (recover_interrupted)
             -> 启动轮询循环
    shutdown -> 通知停止，等待当前作业结束（有上限）

为什么是轮询而不是事件驱动：
SQLite 没有 LISTEN/NOTIFY。轮询间隔 1 秒对本地单用户工具足够，
且实现简单、没有额外的通知通道需要同步。入队后也可主动唤醒。

隔离：每个作业拿独立 Session。处理器抛异常不会污染其他作业的会话。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job
from app.models.task_run import TaskRun
from app.workers import queue

logger = logging.getLogger(__name__)

# 轮询间隔：本地单用户工具，1 秒延迟可接受。
POLL_INTERVAL_SECONDS = 1.0
# 超过这个时长仍在 RUNNING 的作业视为卡死，重新排队。
RUN_TIMEOUT = timedelta(minutes=10)
# 关闭时等待当前作业的最长时间。
SHUTDOWN_GRACE_SECONDS = 5.0


class JobCancelled(RuntimeError):
    """处理器主动抛出表示作业应停止（例如检测到取消信号）。"""


@dataclass
class JobResult:
    """处理器返回值。message 会写入事件，供 Activity Center 展示。"""

    message: str = ""
    # 检查点：处理器可在返回前写入，便于重试时跳过已完成的步骤。
    checkpoint: dict[str, Any] | None = field(default=None)


# 处理器签名：接收作业与运行实例，可读写 Session。
JobHandler = Callable[[Session, Job, TaskRun], JobResult]


class HandlerRegistry:
    """作业类型 -> 处理器。

    显式注册，不做自动发现：避免某个模块被导入就悄悄获得执行能力。
    """

    def __init__(self) -> None:
        self._handlers: dict[str, JobHandler] = {}

    def register(self, job_type: str, handler: JobHandler) -> None:
        if job_type in self._handlers:
            raise ValueError(f"作业类型已注册：{job_type}")
        self._handlers[job_type] = handler

    def get(self, job_type: str) -> JobHandler | None:
        return self._handlers.get(job_type)

    def __len__(self) -> int:
        return len(self._handlers)


class Worker:
    """单实例 Worker。

    单实例是刻意的：SQLite 不适合多进程并发写（§4.13），
    MVP 只有一个进程，一个 Worker 即可。
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        registry: HandlerRegistry,
        *,
        poll_interval: float = POLL_INTERVAL_SECONDS,
        run_timeout: timedelta = RUN_TIMEOUT,
    ) -> None:
        self._session_factory = session_factory
        self._registry = registry
        self._poll_interval = poll_interval
        self._run_timeout = run_timeout
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        # 测试用：同步等待一次 tick 完成，避免依赖 sleep。
        self.processed = 0

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop.clear()
        # 启动前先收拾上次残留的 RUNNING 作业。
        with self._session_factory() as session:
            queue.recover_interrupted(session)
        self._task = asyncio.create_task(self._loop(), name="job-worker")
        logger.info("后台 Worker 已启动")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop.set()
        self._wake.set()
        try:
            await asyncio.wait_for(self._task, timeout=SHUTDOWN_GRACE_SECONDS)
        except TimeoutError:
            # 进程退出时卡在某个作业上：取消任务，交给下次启动的
            # recover_interrupted 处理（这正是它存在的意义）。
            self._task.cancel()
            logger.warning("Worker 关闭超时，已强制停止")
        finally:
            self._task = None
            logger.info("后台 Worker 已停止")

    def notify(self) -> None:
        """有新作业入队时调用，立即唤醒轮询。"""
        self._wake.set()

    async def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                did_work = await self.run_once()
            except Exception:
                # Worker 循环本身绝不能因单次失败而退出。
                logger.exception("Worker 循环异常")
                did_work = False

            if not did_work:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=self._poll_interval)
                except TimeoutError:
                    pass
            # 顺带回收卡死的作业（每轮都查，代价很低且有索引）。
            with self._session_factory() as session:
                queue.expire_running_timeout(session, timeout=self._run_timeout)

    async def run_once(self) -> bool:
        """领取并执行一个作业。返回是否有作业被处理。

        处理器是同步的（本地 SQLite 操作，毫秒级），
        但用 to_thread 包一层，避免将来某个处理器阻塞事件循环。
        """
        with self._session_factory() as session:
            job = queue.claim_next(session)
            if job is None:
                return False
            run = queue.start_run(session, job)
            job_id, run_id = job.id, run.id
            job_type = job.job_type

        handler = self._registry.get(job_type)
        if handler is None:
            # 未知类型：不可重试（重试也不会成功），直接失败。
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is not None and run is not None:
                    queue.fail_run(
                        session,
                        job,
                        run,
                        error_code="UNKNOWN_JOB_TYPE",
                        error_message=f"未注册的作业类型：{job_type}",
                        retryable=False,
                    )
            self.processed += 1
            return True

        try:
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is None or run is None:  # pragma: no cover - 被删除的极端情况
                    return False
                result = await asyncio.to_thread(handler, session, job, run)
        except JobCancelled:
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                if job is not None:
                    try:
                        queue.cancel(session, job_id)
                    except queue.JobError:
                        pass
        except Exception as exc:
            logger.exception("作业执行失败", extra={"job_id": job_id, "job_type": job_type})
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is not None and run is not None:
                    queue.fail_run(
                        session,
                        job,
                        run,
                        # 只暴露异常类型名，不暴露消息内容（可能含内部细节）。
                        error_code=type(exc).__name__,
                        error_message="作业执行出错",
                        retryable=True,
                    )
        else:
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is not None and run is not None:
                    if result.checkpoint is not None:
                        queue.save_checkpoint(session, run, result.checkpoint)
                    queue.complete_run(session, job, run, message=result.message)

        self.processed += 1
        return True


def builtin_handlers() -> HandlerRegistry:
    """注册内建作业类型。

    M7 只提供 noop：用于验证队列、重试、取消、恢复机制本身。
    真实作业类型（定时记忆提取、批量整理等）按需在此登记，
    遵循"不提前造未使用功能"。
    """
    registry = HandlerRegistry()

    def noop(session: Session, job: Job, run: TaskRun) -> JobResult:
        """空作业。payload 可含 {"fail_times": n} 用于测试重试。"""
        import json

        payload = json.loads(job.payload or "{}")
        fail_times = payload.get("fail_times", 0)
        if isinstance(fail_times, int) and job.attempts <= fail_times:
            raise RuntimeError("模拟失败")
        return JobResult(message="noop 完成")

    registry.register("noop", noop)
    return registry
