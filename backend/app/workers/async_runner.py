"""异步作业驱动器（M9：生成改为后台作业）。

## 为什么需要它

生成要走**两次模型调用**，耗时以十秒计。`Worker` 的处理器是同步的，
并且跑在一个轮询循环里：一个处理器跑 40 秒，整个 Worker 就 40 秒不干活，
用户在这期间提交的其它作业全部排在后面。

所以要"领取还是 Worker 做，执行放到自己的 asyncio 任务里"：

    Worker.run_once  → 识别为异步作业 → claim_job + start_run
                     → 交给 AsyncJobRunner 起一个任务
                     → 立刻返回，继续轮询下一个作业

## 三个必须处理的细节

1. **心跳**：作业一旦 RUNNING，`expire_running_timeout` 会在超时后把它
   当成卡死重新排队。长任务必须定期刷新 `updated_at`，否则同一作业跑两遍，
   用户会看到两次生成。这里每 10 秒一次。
2. **取消**：用户在活动中心点"取消"时，队列层把作业标成 CANCELLED。
   驱动器在模型流的每个 chunk 之间检查一次，发现已取消就停掉——
   不能假装没看见继续读流（那会继续计费）。
3. **进程退出**：任务还在跑时不能悄无声息地丢。`stop()` 会先取消任务，
   再让队列层的 `recover_interrupted` 在下次启动时处理残留，
   语义与同步作业一致（至少一次 + 幂等）。

## 为什么任务表放在内存

它只记录"本进程正在跑哪些任务"，不需要持久化：进程一退出，
执行状态由数据库里 Job/TaskRun 的状态表达，重启后 recover 逻辑接手。
写进数据库反而多一份可能不一致的状态。
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from app.models.job import Job, JobStatus
from app.models.task_run import TaskRun
from app.workers import queue

logger = logging.getLogger(__name__)

# 心跳间隔。必须显著小于 Worker 的 RUN_TIMEOUT（10 分钟），
# 否则长任务仍会被判成卡死。
HEARTBEAT_SECONDS = 10.0
# 关闭时等待任务自行结束的时间；超时则取消。
SHUTDOWN_GRACE_SECONDS = 3.0

# 异步处理器签名：拿到作业参数与一个"报告阶段"的回调，
# 自己负责往 staging 写内容。抛异常表示失败。
AsyncJobHandler = Callable[
    [Session, Job, TaskRun, Callable[[str], None]], Awaitable[dict[str, Any] | None]
]


class AsyncJobRunner:
    """持有本进程内的异步作业任务。"""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._tasks: dict[str, asyncio.Task[None]] = {}

    @property
    def running(self) -> int:
        return len(self._tasks)

    def start(
        self,
        *,
        job_id: str,
        run_id: str,
        handler: AsyncJobHandler,
        on_failure: Callable[[Exception], None] | None = None,
        cancel_requested: Callable[[], bool] | None = None,
    ) -> None:
        """为已领取的作业起一个任务。重复 start 同一作业会被忽略。"""
        if job_id in self._tasks:
            return
        task = asyncio.create_task(
            self._execute(job_id, run_id, handler, on_failure, cancel_requested),
            name=f"async-job-{job_id}",
        )
        self._tasks[job_id] = task
        task.add_done_callback(lambda _: self._tasks.pop(job_id, None))

    def cancel(self, job_id: str) -> bool:
        """取消本进程内的任务。返回是否找到并取消了任务。"""
        task = self._tasks.get(job_id)
        if task is None:
            return False
        task.cancel()
        return True

    async def wait(self, job_id: str, timeout: float | None = None) -> bool:
        """等待某个作业任务结束。返回是否已结束（超时为 False）。测试用。"""
        task = self._tasks.get(job_id)
        if task is None:
            return True
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except TimeoutError:
            return False
        except asyncio.CancelledError:
            # 任务**自身**被取消（用户取消或关闭进程）也是"结束"了。
            # 若不加这一条，调用方会把正常的取消当成异常抛出去。
            if asyncio.current_task().cancelling():  # type: ignore[union-attr]
                raise
        return True

    async def stop(self) -> None:
        """停止全部任务。

        先礼后兵：给它们一点时间自行收尾（正常路径下它们会写完成/失败状态），
        超时再取消。取消后的残留由下次启动的 recover_interrupted 处理。
        """
        tasks = list(self._tasks.values())
        if not tasks:
            return
        done, pending = await asyncio.wait(tasks, timeout=SHUTDOWN_GRACE_SECONDS)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
            logger.warning("关闭时强制停止了异步作业", extra={"count": len(pending)})

    async def _execute(
        self,
        job_id: str,
        run_id: str,
        handler: AsyncJobHandler,
        on_failure: Callable[[Exception], None] | None,
        cancel_requested: Callable[[], bool] | None,
    ) -> None:
        heartbeat = asyncio.create_task(self._heartbeat_loop(job_id))
        try:
            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is None or run is None:  # pragma: no cover - 被删除的极端情况
                    return

            def report(event_type: str, message: str = "") -> None:
                # 回调在处理器内部同步调用，用独立 Session，避免与处理器的
                # Session 生命周期纠缠（处理器可能正要提交）。
                #
                # `message` 是可选的阶段说明，会展示给用户。调用方必须传
                # **预先写死**的文案——绝不拼接需求原文、模型输出或异常正文
                # （与 JobFailure.message 同一条约束）。
                with self._session_factory() as session:
                    queue.record_event(
                        session,
                        job_id=job_id,
                        run_id=run_id,
                        event_type=event_type,
                        message=message,
                    )

            with self._session_factory() as session:
                job = session.get(Job, job_id)
                run = session.get(TaskRun, run_id)
                if job is None or run is None:  # pragma: no cover
                    return
                checkpoint = await run_with_cancel_check(
                    handler(session, job, run, report),
                    cancel_requested or (lambda: queue.is_cancelled(session, job_id)),
                )
        except asyncio.CancelledError:
            # 两种情况都会走到这里：
            # 1. 用户取消（队列层已把 Job 标成 CANCELLED）——如实记录这次执行被取消；
            # 2. 进程正在退出——**不写"失败"**（那是在说假话），
            #    交给下次启动的 recover_interrupted 标为 INTERRUPTED 并重新排队。
            with suppress(Exception):
                self._finish_cancelled(job_id, run_id)
            raise
        except Exception as exc:
            logger.exception("异步作业失败", extra={"job_id": job_id})
            if on_failure is not None:
                with suppress(Exception):
                    on_failure(exc)
            self._finish_failed(job_id, run_id, exc)
        else:
            self._finish_succeeded(job_id, run_id, checkpoint or {})
        finally:
            heartbeat.cancel()
            with suppress(asyncio.CancelledError):
                await heartbeat

    def _finish_cancelled(self, job_id: str, run_id: str) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            run = session.get(TaskRun, run_id)
            if job is None or run is None:  # pragma: no cover
                return
            # 只有确实被用户取消时才动执行实例；进程退出时保持 RUNNING，
            # 让 recover_interrupted 走它自己的语义（INTERRUPTED + 重新排队）。
            if job.status == JobStatus.CANCELLED.value:
                queue.mark_run_cancelled(session, job_id, run)

    def _finish_succeeded(self, job_id: str, run_id: str, checkpoint: dict[str, Any]) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            run = session.get(TaskRun, run_id)
            if job is None or run is None:  # pragma: no cover
                return
            # 用户可能在最后一刻取消了：取消优先，不谎称成功。
            if job.status == JobStatus.CANCELLED.value:
                queue.mark_run_cancelled(session, job_id, run)
                return
            if checkpoint:
                queue.save_checkpoint(session, run, checkpoint)
            queue.complete_run(session, job, run, message=checkpoint.get("message", ""))

    def _finish_failed(self, job_id: str, run_id: str, error: Exception) -> None:
        with self._session_factory() as session:
            job = session.get(Job, job_id)
            run = session.get(TaskRun, run_id)
            if job is None or run is None:  # pragma: no cover
                return

            if job.status == JobStatus.CANCELLED.value:
                queue.mark_run_cancelled(session, job_id, run)
                return

            # 处理器可以抛 JobFailure 带上用户可读的原因；其余一律按内部错误处理。
            failure = error if isinstance(error, JobFailure) else None
            queue.fail_run(
                session,
                job,
                run,
                error_code=(failure.code if failure is not None else type(error).__name__),
                # 只把处理器明确标注为安全的原因写进数据库；
                # 未知异常的消息可能含内部细节，不落库也不外露。
                error_message=(failure.message if failure is not None else "作业执行出错"),
                retryable=failure.retryable if failure is not None else True,
            )

    async def _heartbeat_loop(self, job_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                with self._session_factory() as session:
                    queue.heartbeat(session, job_id)
            except Exception:  # pragma: no cover - 心跳失败不该拖垮任务
                logger.warning("作业心跳失败", extra={"job_id": job_id})


class JobFailure(Exception):
    """处理器主动报告的可解释失败。

    `message` 必须是**预先写死或已确认安全**的文案：它会写进数据库并
    在活动中心展示给用户。绝不要拼接异常正文、供应商原文或文件路径。
    """

    def __init__(self, message: str, *, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.retryable = retryable


async def run_with_cancel_check(
    awaitable: Awaitable[dict[str, Any] | None],
    cancel_requested: Callable[[], bool],
    *,
    poll_seconds: float = 1.0,
) -> dict[str, Any] | None:
    """执行处理器，并周期性检查是否被取消。

    为什么要这样：处理器内部在等模型流，它不知道用户点了取消。
    单纯 `task.cancel()` 也能停，但那会让处理器里的清理逻辑走异常路径，
    并且取消原因丢失。这里用"等一会儿再查一眼"的方式：
    取消时同样抛 CancelledError（保持与 asyncio 语义一致），
    但先由这里写下正确的原因。

    1 秒轮询：模型流本身的粒度就是秒级，更细没有意义，
    而且每次检查是一次 SQLite 读，不必更频繁。
    """
    task = asyncio.ensure_future(awaitable)
    try:
        while True:
            done, _ = await asyncio.wait({task}, timeout=poll_seconds)
            if task in done:
                return task.result()
            if cancel_requested():
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
                raise asyncio.CancelledError()
    finally:
        if not task.done():  # pragma: no cover - 防御性
            task.cancel()
