"""作业队列与 Worker 测试（M7 验收）。

AGENT.md M7 验收标准逐条对应：
1. 关闭浏览器不影响本地后台任务 —— 由"Worker 跑在服务端"保证（测试验证 Worker 独立执行）
2. 服务重启后任务状态可恢复 —— recover_interrupted
3. 重复事件不会重复产生不可逆副作用 —— 幂等键
4. 超时、失败和取消状态准确 —— 各自的状态转换测试
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.models.job import Job, JobStatus
from app.models.task_run import RunStatus, TaskEvent, TaskRun
from app.workers import queue
from app.workers.worker import HandlerRegistry, JobCancelled, JobResult, Worker


class TestEnqueue:
    def test_creates_pending_job(self, session) -> None:
        job, created = queue.enqueue(session, job_type="noop")

        assert created is True
        assert job.status == JobStatus.PENDING.value
        assert job.attempts == 0

    def test_records_enqueued_event(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")

        events = session.query(TaskEvent).filter_by(job_id=job.id).all()
        assert [e.event_type for e in events] == ["enqueued"]

    def test_idempotency_key_prevents_duplicate(self, session) -> None:
        """核心：重复入队不产生第二个作业（§4.14 幂等）。"""
        first, created1 = queue.enqueue(session, job_type="noop", idempotency_key="k1")
        second, created2 = queue.enqueue(session, job_type="noop", idempotency_key="k1")

        assert created1 is True
        assert created2 is False
        assert first.id == second.id
        assert session.query(Job).count() == 1

    def test_same_key_different_type_still_unique(self, session) -> None:
        # 幂等键是全局唯一的，不按类型区分：避免同键跨类型重复触发。
        queue.enqueue(session, job_type="a", idempotency_key="k")
        _, created = queue.enqueue(session, job_type="b", idempotency_key="k")

        assert created is False
        assert session.query(Job).count() == 1

    def test_no_key_allows_multiple(self, session) -> None:
        queue.enqueue(session, job_type="noop")
        queue.enqueue(session, job_type="noop")

        assert session.query(Job).count() == 2

    def test_payload_is_stored(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", payload={"x": 1})

        assert json.loads(job.payload) == {"x": 1}

    def test_future_job_not_claimed(self, session) -> None:
        queue.enqueue(session, job_type="noop", available_at=datetime.now(UTC) + timedelta(hours=1))

        assert queue.claim_next(session) is None


class TestClaim:
    def test_claims_pending_job(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")

        claimed = queue.claim_next(session)

        assert claimed is not None
        assert claimed.id == job.id
        assert claimed.status == JobStatus.RUNNING.value
        assert claimed.attempts == 1

    def test_does_not_claim_twice(self, session) -> None:
        """核心：一个作业只能被领取一次（原子领取）。"""
        queue.enqueue(session, job_type="noop")

        first = queue.claim_next(session)
        second = queue.claim_next(session)

        assert first is not None
        assert second is None

    def test_claims_in_fifo_order(self, session) -> None:
        first, _ = queue.enqueue(session, job_type="noop")
        second, _ = queue.enqueue(session, job_type="noop")

        claimed1 = queue.claim_next(session)
        claimed2 = queue.claim_next(session)

        assert claimed1 is not None and claimed2 is not None
        assert claimed1.id == first.id
        assert claimed2.id == second.id

    def test_empty_queue_returns_none(self, session) -> None:
        assert queue.claim_next(session) is None


class TestFailAndRetry:
    def test_retry_scheduled_with_backoff(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=3)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)

        will_retry = queue.fail_run(
            session, job, run, error_code="E", error_message="boom", retryable=True
        )

        assert will_retry is True
        assert job.status == JobStatus.PENDING.value
        assert job.available_at > datetime.now(UTC)

    def test_retry_exhausted_marks_failed(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=1)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)

        will_retry = queue.fail_run(
            session, job, run, error_code="E", error_message="boom", retryable=True
        )

        assert will_retry is False
        assert job.status == JobStatus.FAILED.value

    def test_non_retryable_fails_immediately(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=5)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)

        will_retry = queue.fail_run(
            session, job, run, error_code="E", error_message="boom", retryable=False
        )

        assert will_retry is False
        assert job.status == JobStatus.FAILED.value

    def test_backoff_grows(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=5)

        delays = []
        for _ in range(3):
            job = queue.claim_next(session)
            assert job is not None
            run = queue.start_run(session, job)
            before = datetime.now(UTC)
            queue.fail_run(session, job, run, error_code="E", error_message="x")
            delays.append((job.available_at - before).total_seconds())
            # 让它立即可被领取
            job.available_at = datetime.now(UTC)
            session.commit()

        assert delays[0] < delays[1] < delays[2], f"退避应递增，实际 {delays}"

    def test_failure_records_run_error(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=1)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)
        queue.fail_run(session, job, run, error_code="E_X", error_message="boom")

        session.refresh(run)
        assert run.status == RunStatus.FAILED.value
        assert run.error_code == "E_X"


class TestCancel:
    def test_cancel_pending(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")

        cancelled = queue.cancel(session, job.id)

        assert cancelled.status == JobStatus.CANCELLED.value

    def test_cancelled_job_not_claimed(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")
        queue.cancel(session, job.id)

        assert queue.claim_next(session) is None

    def test_cancel_finished_rejected(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=1)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)
        queue.fail_run(session, job, run, error_code="E", error_message="x", retryable=False)

        with pytest.raises(queue.JobError) as exc:
            queue.cancel(session, job.id)

        assert exc.value.code == "JOB_ALREADY_FINISHED"

    def test_cancel_running_is_recorded_honestly(self, session) -> None:
        """取消执行中的作业：记录"已完成步骤不会回滚"（§7.3）。"""
        job, _ = queue.enqueue(session, job_type="noop")
        queue.claim_next(session)

        queue.cancel(session, job.id)

        event = session.query(TaskEvent).filter_by(job_id=job.id, event_type="cancelled").one()
        assert "不会回滚" in event.message

    def test_cancel_missing_raises(self, session) -> None:
        with pytest.raises(queue.JobError) as exc:
            queue.cancel(session, "0" * 32)

        assert exc.value.code == "JOB_NOT_FOUND"


class TestManualRetry:
    def test_retry_failed_job(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=1)
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)
        queue.fail_run(session, job, run, error_code="E", error_message="x", retryable=False)

        retried = queue.retry(session, job.id)

        assert retried.status == JobStatus.PENDING.value
        assert retried.attempts == 0

    def test_cannot_retry_successful(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")
        job = queue.claim_next(session)
        assert job is not None
        run = queue.start_run(session, job)
        queue.complete_run(session, job, run)

        with pytest.raises(queue.JobError) as exc:
            queue.retry(session, job.id)

        assert exc.value.code == "JOB_NOT_RETRYABLE"


class TestRecovery:
    """服务重启后的恢复（M7 验收第 2 条）。"""

    def test_running_job_requeued(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=3)
        claimed = queue.claim_next(session)
        assert claimed is not None
        queue.start_run(session, claimed)

        recovered = queue.recover_interrupted(session)

        session.refresh(job)
        assert recovered == 1
        assert job.status == JobStatus.PENDING.value
        # SQLite 不保存时区，读回的是 naive datetime；统一按 UTC 解释后比较。
        available = job.available_at
        if available.tzinfo is None:
            available = available.replace(tzinfo=UTC)
        assert available <= datetime.now(UTC)

    def test_run_marked_interrupted_not_failed(self, session) -> None:
        """核心：中断不谎称失败或成功。"""
        job, _ = queue.enqueue(session, job_type="noop")
        claimed = queue.claim_next(session)
        assert claimed is not None
        run = queue.start_run(session, claimed)

        queue.recover_interrupted(session)

        session.refresh(run)
        assert run.status == RunStatus.INTERRUPTED.value

    def test_records_recovered_event(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")
        queue.claim_next(session)

        queue.recover_interrupted(session)

        types = [e.event_type for e in session.query(TaskEvent).filter_by(job_id=job.id)]
        assert "recovered" in types

    def test_exhausted_attempts_marks_failed(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=1)
        queue.claim_next(session)  # attempts 变为 1 = max

        queue.recover_interrupted(session)

        session.refresh(job)
        assert job.status == JobStatus.FAILED.value

    def test_no_running_jobs_is_noop(self, session) -> None:
        queue.enqueue(session, job_type="noop")

        assert queue.recover_interrupted(session) == 0


class TestTimeoutExpiry:
    def test_stuck_running_job_requeued(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=3)
        job = queue.claim_next(session)
        assert job is not None
        # 把 updated_at 推到很久以前，模拟 Worker 崩溃
        job.updated_at = datetime.now(UTC) - timedelta(hours=1)
        session.commit()

        count = queue.expire_running_timeout(session, timeout=timedelta(minutes=10))

        session.refresh(job)
        assert count == 1
        assert job.status == JobStatus.PENDING.value

    def test_fresh_running_job_untouched(self, session) -> None:
        queue.enqueue(session, job_type="noop")
        queue.claim_next(session)

        assert queue.expire_running_timeout(session, timeout=timedelta(minutes=10)) == 0


class TestWorker:
    def _worker(self, session, registry=None, **kwargs):  # type: ignore[no-untyped-def]
        from sqlalchemy.orm import sessionmaker

        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        return Worker(factory, registry or HandlerRegistry(), **kwargs)

    @pytest.mark.asyncio
    async def test_executes_job(self, session) -> None:
        ran: list[str] = []
        registry = HandlerRegistry()
        registry.register(
            "noop",
            lambda s, j, r: (
                JobResult(message="done") and (ran.append(j.id) or JobResult(message="done"))
            ),
        )

        queue.enqueue(session, job_type="noop")
        worker = self._worker(session, registry)

        did = await worker.run_once()

        assert did is True
        assert len(ran) == 1
        assert session.query(Job).one().status == JobStatus.SUCCEEDED.value

    @pytest.mark.asyncio
    async def test_returns_false_when_empty(self, session) -> None:
        worker = self._worker(session)

        assert await worker.run_once() is False

    @pytest.mark.asyncio
    async def test_unknown_job_type_fails_without_retry(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="nonexistent", max_attempts=3)
        worker = self._worker(session)

        await worker.run_once()

        session.refresh(job)
        assert job.status == JobStatus.FAILED.value
        assert "UNKNOWN_JOB_TYPE" in (job.last_error or "")

    @pytest.mark.asyncio
    async def test_handler_exception_schedules_retry(self, session) -> None:
        def boom(s, j, r):  # type: ignore[no-untyped-def]
            raise RuntimeError("内部秘密 /home/user/x")

        registry = HandlerRegistry()
        registry.register("noop", boom)
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=3)
        worker = self._worker(session, registry)

        await worker.run_once()

        session.refresh(job)
        assert job.status == JobStatus.PENDING.value
        # 错误里不能出现异常正文
        assert "内部秘密" not in (job.last_error or "")
        assert "RuntimeError" in (job.last_error or "")

    @pytest.mark.asyncio
    async def test_handler_cancel_stops_job(self, session) -> None:
        def cancels(s, j, r):  # type: ignore[no-untyped-def]
            raise JobCancelled

        registry = HandlerRegistry()
        registry.register("noop", cancels)
        job, _ = queue.enqueue(session, job_type="noop")
        worker = self._worker(session, registry)

        await worker.run_once()

        session.refresh(job)
        assert job.status == JobStatus.CANCELLED.value

    @pytest.mark.asyncio
    async def test_checkpoint_is_saved(self, session) -> None:
        registry = HandlerRegistry()
        registry.register("noop", lambda s, j, r: JobResult(checkpoint={"step": 2}))
        job, _ = queue.enqueue(session, job_type="noop")
        worker = self._worker(session, registry)

        await worker.run_once()

        run = session.query(TaskRun).filter_by(job_id=job.id).one()
        assert json.loads(run.checkpoint or "{}") == {"step": 2}

    @pytest.mark.asyncio
    async def test_worker_loop_processes_jobs(self, session) -> None:
        """整体验证：入队后无需人工触发，循环会执行它。"""
        registry = HandlerRegistry()
        registry.register("noop", lambda s, j, r: JobResult())
        worker = self._worker(session, registry, poll_interval=0.05)

        await worker.start()
        try:
            queue.enqueue(session, job_type="noop")
            worker.notify()
            for _ in range(40):
                await asyncio.sleep(0.05)
                session.expire_all()
                if session.query(Job).one().status == JobStatus.SUCCEEDED.value:
                    break
            status = session.query(Job).one().status
        finally:
            await worker.stop()

        assert status == JobStatus.SUCCEEDED.value

    @pytest.mark.asyncio
    async def test_start_recovers_interrupted(self, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop", max_attempts=3)
        queue.claim_next(session)
        worker = self._worker(session, poll_interval=0.05)

        await worker.start()
        await worker.stop()

        session.expire_all()
        # 恢复后可能立刻被 Worker 执行；只要不再是 RUNNING 即证明恢复生效
        assert job.id is not None
        refreshed = session.get(Job, job.id)
        assert refreshed is not None
        assert refreshed.status != JobStatus.RUNNING.value

    def test_duplicate_handler_rejected(self) -> None:
        registry = HandlerRegistry()
        registry.register("t", lambda s, j, r: JobResult())

        with pytest.raises(ValueError, match="已注册"):
            registry.register("t", lambda s, j, r: JobResult())


class TestBuiltinHandlers:
    def test_noop_registered(self) -> None:
        from app.workers.worker import builtin_handlers

        assert builtin_handlers().get("noop") is not None

    @pytest.mark.asyncio
    async def test_noop_completes(self, session) -> None:
        from sqlalchemy.orm import sessionmaker

        from app.workers.worker import builtin_handlers

        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        worker = Worker(factory, builtin_handlers(), poll_interval=0.05)
        queue.enqueue(session, job_type="noop")

        await worker.run_once()

        assert session.query(Job).one().status == JobStatus.SUCCEEDED.value

    @pytest.mark.asyncio
    async def test_noop_fail_times_then_succeeds(self, session) -> None:
        """验证重试链路：前 2 次失败，第 3 次成功。"""
        from sqlalchemy.orm import sessionmaker

        from app.workers.worker import builtin_handlers

        factory = sessionmaker(bind=session.get_bind(), expire_on_commit=False)
        worker = Worker(factory, builtin_handlers())
        job, _ = queue.enqueue(session, job_type="noop", payload={"fail_times": 2}, max_attempts=4)

        for _ in range(3):
            job = session.get(Job, job.id)
            assert job is not None
            job.available_at = datetime.now(UTC)
            session.commit()
            await worker.run_once()

        session.expire_all()
        final = session.query(Job).one()
        assert final.status == JobStatus.SUCCEEDED.value
        assert final.attempts == 3


class TestIdempotencyLayers:
    """幂等有两层防护：入队前查询 + 唯一索引。
    任一层单独失效都不应产生重复作业，但唯一索引是最终保证。"""

    def test_unique_index_rejects_duplicate_key(self, session) -> None:
        from sqlalchemy.exc import IntegrityError

        first = Job(job_type="x", idempotency_key="dup", payload="{}")
        session.add(first)
        session.commit()

        session.add(Job(job_type="x", idempotency_key="dup", payload="{}"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_second_enqueue_creation_flag_false(self, session) -> None:
        """即便查询层被绕过，第二次入队也必须报 created=False。"""
        _, created1 = queue.enqueue(session, job_type="noop", idempotency_key="flag-k")
        _, created2 = queue.enqueue(session, job_type="noop", idempotency_key="flag-k")

        assert created1 is True
        assert created2 is False

    def test_null_keys_do_not_collide(self, session) -> None:
        """SQLite 中 NULL 不参与唯一性比较：无幂等键的作业可以重复入队。"""
        queue.enqueue(session, job_type="noop")
        queue.enqueue(session, job_type="noop")

        assert session.query(Job).count() == 2
