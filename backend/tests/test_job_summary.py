"""作业列表的输入摘要与阶段（迭代 1：把内部作业翻译成用户可读信息）。

两条硬约束：
1. **不返回 payload 原文**：它可能含完整用户描述，列表只给一行截断摘要。
2. 已结束的作业不显示"当前阶段"，避免把最后一次中间事件误读成仍在进行。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.models.job import Job
from app.models.task_run import TaskEvent
from app.workers import queue

API = "/api/v1"


def _add_job(session, *, job_type="generate_app", payload=None, status="PENDING", **kwargs):  # type: ignore[no-untyped-def]
    job = Job(
        job_type=job_type,
        payload=json.dumps(payload or {}, ensure_ascii=False),
        status=status,
        **kwargs,
    )
    session.add(job)
    session.commit()
    return job


class TestJobSummary:
    def test_generate_app_summary_is_truncated_request(self, client: TestClient, session) -> None:
        long_request = "做一个番茄钟计时器，" + "很长的描述" * 60
        _add_job(session, payload={"request": long_request, "staging_id": "s1"})

        item = client.get(f"{API}/jobs").json()["items"][0]

        assert item["summary"].startswith("做一个番茄钟计时器")
        assert item["summary"].endswith("…")
        assert len(item["summary"]) <= 121
        # 原始 payload 不返回（staging_id 等内部字段同样不外泄）。
        assert "payload" not in item
        assert "staging_id" not in json.dumps(item)

    def test_whitespace_is_collapsed(self, client: TestClient, session) -> None:
        _add_job(session, payload={"request": "  做  一个\n\t清单 "})

        item = client.get(f"{API}/jobs").json()["items"][0]

        assert item["summary"] == "做 一个 清单"

    def test_unknown_job_type_has_no_summary(self, client: TestClient, session) -> None:
        """白名单之外的作业类型不展示任何 payload 内容。"""
        _add_job(session, job_type="secret_migration", payload={"request": "不要展示我"})

        item = client.get(f"{API}/jobs").json()["items"][0]

        assert item["summary"] == ""

    def test_malformed_payload_does_not_break_list(self, client: TestClient, session) -> None:
        job = Job(job_type="generate_app", payload="{not json", status="PENDING")
        session.add(job)
        session.commit()

        response = client.get(f"{API}/jobs")

        assert response.status_code == 200
        assert response.json()["items"][0]["summary"] == ""


class TestJobStage:
    def test_running_job_shows_latest_stage(self, client: TestClient, session) -> None:
        job = _add_job(session, status="RUNNING")
        session.add(TaskEvent(job_id=job.id, event_type="started", message="开始"))
        session.add(TaskEvent(job_id=job.id, event_type="progress", message="generating"))
        session.commit()

        item = client.get(f"{API}/jobs").json()["items"][0]

        assert item["stage"] == "progress"
        assert item["stage_message"] == "generating"

    def test_finished_job_has_no_stage(self, client: TestClient, session) -> None:
        job = _add_job(session, status="SUCCEEDED")
        session.add(TaskEvent(job_id=job.id, event_type="progress", message="generating"))
        session.commit()

        item = client.get(f"{API}/jobs").json()["items"][0]

        assert item["stage"] is None
        assert item["stage_message"] is None

    def test_stage_not_mixed_between_jobs(self, client: TestClient, session) -> None:
        first = _add_job(session, status="RUNNING")
        second = _add_job(session, status="RUNNING")
        session.add(TaskEvent(job_id=first.id, event_type="started", message="A"))
        session.add(TaskEvent(job_id=second.id, event_type="progress", message="B"))
        session.commit()

        items = {item["id"]: item for item in client.get(f"{API}/jobs").json()["items"]}

        assert items[first.id]["stage_message"] == "A"
        assert items[second.id]["stage_message"] == "B"

    def test_detail_also_reports_stage(self, client: TestClient, session) -> None:
        job, _ = queue.enqueue(session, job_type="noop")

        body = client.get(f"{API}/jobs/{job.id}").json()

        assert body["stage"] == "enqueued"
        assert body["summary"] == ""
