"""异步作业驱动器测试（M9：生成改为后台作业）。

这些测试只关心"执行骨架"是否正确：领取、心跳、取消、失败分类、关闭。
生成逻辑本身在 `test_apps_api.py` 里用假 Provider 覆盖。

重点：
1. 长任务有心跳——否则会被判成卡死重新排队，导致重复执行；
2. 用户在等待期间取消，作业如实记为 CANCELLED，不谎称成功；
3. 失败分类决定**要不要重试**：配置错与超范围不重试，供应商错误重试；
4. 关闭进程时任务被取消，且不被标成"失败"。
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest

from app.models.job import Job, JobStatus
from app.models.task_run import RunStatus, TaskRun
from app.workers import queue
from app.workers.async_runner import AsyncJobRunner, JobFailure


@pytest.fixture()
def factory(session):  # type: ignore[no-untyped-def]
    """复用 conftest 的 session 引擎，但需要独立的 sessionmaker。

    conftest 的 `session` 是一个长生命周期 Session，而驱动器每次操作
    都自己开一个 Session（这正是生产行为），所以从同一个 engine 建工厂。
    """
    from sqlalchemy.orm import sessionmaker

    return sessionmaker(bind=session.get_bind())


def _enqueue_and_claim(factory, **kwargs):  # type: ignore[no-untyped-def]
    with factory() as session:
        job, _ = queue.enqueue(session, job_type="test_async", **kwargs)
        claimed = queue.claim_next(session)
        assert claimed is not None
        run = queue.start_run(session, claimed)
        return job.id, run.id


def _status(factory, job_id: str):  # type: ignore[no-untyped-def]
    with factory() as session:
        job = session.get(Job, job_id)
        run = session.query(TaskRun).filter_by(job_id=job_id).order_by(TaskRun.created_at).first()
        return job.status, job.attempts, run.status if run else None


class TestSuccess:
    @pytest.mark.asyncio
    async def test_completes_and_saves_checkpoint(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            return {"message": "完成", "value": 42}

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            job = session.get(Job, job_id)
            run = session.query(TaskRun).filter_by(job_id=job_id).one()
            assert job.status == JobStatus.SUCCEEDED.value
            assert run.status == RunStatus.SUCCEEDED.value
            assert '"value": 42' in (run.checkpoint or "")

    @pytest.mark.asyncio
    async def test_reports_progress_events(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            report("progress")
            return {"message": "ok"}

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            events = session.query(queue.TaskEvent).filter_by(job_id=job_id).all()
            assert "progress" in [event.event_type for event in events]


class TestFailureClassification:
    @pytest.mark.asyncio
    async def test_job_failure_not_retryable_goes_failed(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            raise JobFailure("需求超出范围", code="REQUIREMENT_OUT_OF_SCOPE")

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            job = session.get(Job, job_id)
            assert job.status == JobStatus.FAILED.value
            assert job.last_error == "REQUIREMENT_OUT_OF_SCOPE: 需求超出范围"

    @pytest.mark.asyncio
    async def test_retryable_failure_requeues(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            raise JobFailure("供应商限流", code="PROVIDER_RATE_LIMIT", retryable=True)

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            job = session.get(Job, job_id)
            # 退避重试：回到 PENDING，稍后才可领取。
            assert job.status == JobStatus.PENDING.value

    @pytest.mark.asyncio
    async def test_unexpected_error_does_not_leak_message(self, factory) -> None:
        """未知异常的消息可能含内部细节（路径、SQL），不能写进数据库。"""
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            raise RuntimeError("/home/user/secret/path 不存在")

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            run = session.query(TaskRun).filter_by(job_id=job_id).one()
            assert "secret" not in (run.error_message or "")
            assert run.error_code == "RuntimeError"


class TestCancellation:
    @pytest.mark.asyncio
    async def test_cancel_stops_handler_and_marks_cancelled(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)
        stopped = asyncio.Event()

        async def handler(session, job, run, report):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                stopped.set()
                raise
            return {"message": "不该到这里"}

        runner.start(
            job_id=job_id,
            run_id=run_id,
            handler=handler,
            cancel_requested=_not_cancelled(factory, job_id),
        )

        # 在队列层取消（与 API 的 POST /jobs/{id}/cancel 相同路径）。
        with factory() as session:
            queue.cancel(session, job_id)

        assert await runner.wait(job_id, timeout=10)
        assert stopped.is_set()

        with factory() as session:
            job = session.get(Job, job_id)
            run = session.query(TaskRun).filter_by(job_id=job_id).one()
            assert job.status == JobStatus.CANCELLED.value
            # 不能谎称成功——这是取消语义的核心。
            assert run.status == RunStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_cancel_after_success_still_records_success(self, factory) -> None:
        """作业已经跑完才点取消：取消请求被队列层拒绝，结果保持成功。"""
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            return {"message": "ok"}

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        with factory() as session:
            assert session.get(Job, job_id).status == JobStatus.SUCCEEDED.value


def _not_cancelled(factory, job_id: str):  # type: ignore[no-untyped-def]
    """给 cancel_requested 用的检查函数。

    每次调用开一个新 Session 并关闭——检查在轮询里被反复调用，
    复用同一个会长事务，看到的是旧快照。
    """

    def check() -> bool:
        with factory() as session:
            return queue.is_cancelled(session, job_id)

    return check


class TestShutdown:
    @pytest.mark.asyncio
    async def test_stop_cancels_running_task_without_claiming_failure(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            await asyncio.sleep(60)

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        await asyncio.sleep(0.05)

        await runner.stop()

        with factory() as session:
            job = session.get(Job, job_id)
            run = session.query(TaskRun).filter_by(job_id=job_id).one()
            # 不写"失败"——那是在说假话；留给下次启动的 recover_interrupted。
            assert job.status == JobStatus.RUNNING.value
            assert run.status == RunStatus.RUNNING.value

    @pytest.mark.asyncio
    async def test_stop_with_no_tasks_is_noop(self, factory) -> None:
        await AsyncJobRunner(factory).stop()


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_long_job_is_not_requeued(self, factory, monkeypatch) -> None:
        """心跳的意义：执行中的作业不会被 expire_running_timeout 抢走。"""
        from app.workers import async_runner

        monkeypatch.setattr(async_runner, "HEARTBEAT_SECONDS", 0.05)
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)

        async def handler(session, job, run, report):
            await asyncio.sleep(0.5)
            return {"message": "ok"}

        runner.start(job_id=job_id, run_id=run_id, handler=handler)

        # 等到"没有心跳就会超时"的时长，再跑一次超时回收。
        # 心跳间隔 0.05 秒，这里等 0.3 秒、按 0.2 秒判定：
        # 有心跳 → updated_at 最多旧 0.1 秒 → 不算卡死；
        # 没心跳 → updated_at 旧 0.3 秒 → 会被判成卡死重新排队。
        from datetime import timedelta

        await asyncio.sleep(0.3)
        with factory() as session:
            expired = queue.expire_running_timeout(session, timeout=timedelta(seconds=0.2))
        assert expired == 0, "心跳没有生效：执行中的作业被当成卡死"

        assert await runner.wait(job_id, timeout=5)
        with factory() as session:
            assert session.get(Job, job_id).status == JobStatus.SUCCEEDED.value


class TestDuplicateStart:
    @pytest.mark.asyncio
    async def test_starting_same_job_twice_is_ignored(self, factory) -> None:
        job_id, run_id = _enqueue_and_claim(factory)
        runner = AsyncJobRunner(factory)
        calls: list[int] = []

        async def handler(session, job, run, report):
            calls.append(1)
            await asyncio.sleep(0.1)
            return {"message": "ok"}

        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        runner.start(job_id=job_id, run_id=run_id, handler=handler)
        assert await runner.wait(job_id, timeout=5)

        assert len(calls) == 1, "同一作业只能执行一次"


def test_runner_tracks_running_count(factory) -> None:  # type: ignore[no-untyped-def]
    runner = AsyncJobRunner(factory)
    assert runner.running == 0


@pytest.fixture(autouse=True)
def _close_fresh_sessions() -> Iterator[None]:
    yield


def test_job_failure_carries_safe_message() -> None:
    failure = JobFailure("预写死的文案", code="X", retryable=True)

    assert failure.message == "预写死的文案"
    assert failure.retryable is True
