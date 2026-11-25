"""用户任务接口测试（`/api/v1/user-tasks`，D-013）。

覆盖迭代 1 的验收要点：
1. 用户任务有列表/完成/重开/删除的最小闭环；
2. 完成状态持久化（刷新后仍为 DONE）；
3. 与作业（`/api/v1/jobs`）是两套数据，互不混入；
4. workspace 隔离与 404 不区分"不存在 / 无权访问"；
5. 创建来源可追溯到审批 id。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.job import Job
from app.models.task import Task, TaskStatus

API = "/api/v1"


def _add_task(session, *, title: str = "学习 TypeScript", status=TaskStatus.OPEN, **kwargs):  # type: ignore[no-untyped-def]
    task = Task(title=title, status=status.value, **kwargs)
    session.add(task)
    session.commit()
    return task


class TestUserTaskApi:
    def test_list_empty(self, client: TestClient) -> None:
        response = client.get(f"{API}/user-tasks")

        assert response.status_code == 200
        body = response.json()
        assert body["items"] == []
        assert body["total"] == 0
        assert body["open_count"] == 0

    def test_list_returns_open_tasks_first(self, client: TestClient, session) -> None:
        _add_task(session, title="已完成的事", status=TaskStatus.DONE)
        _add_task(session, title="还没做的事")

        body = client.get(f"{API}/user-tasks").json()

        assert [item["title"] for item in body["items"]] == ["还没做的事", "已完成的事"]
        assert body["total"] == 2
        assert body["open_count"] == 1

    def test_filter_by_status(self, client: TestClient, session) -> None:
        _add_task(session, title="A")
        _add_task(session, title="B", status=TaskStatus.DONE)

        body = client.get(f"{API}/user-tasks", params={"status": "DONE"}).json()

        assert [item["title"] for item in body["items"]] == ["B"]
        assert body["open_count"] == 1  # 统计口径与筛选无关，始终是未完成数

    def test_invalid_status_is_rejected(self, client: TestClient) -> None:
        response = client.get(f"{API}/user-tasks", params={"status": "RUNNING"})

        assert response.status_code == 422

    def test_complete_then_persists(self, client: TestClient, session) -> None:
        task = _add_task(session)

        done = client.post(f"{API}/user-tasks/{task.id}/complete")
        assert done.status_code == 200
        assert done.json()["status"] == "DONE"

        # "刷新"：重新读取列表，状态必须仍然是 DONE（服务端为准）。
        body = client.get(f"{API}/user-tasks").json()
        assert body["items"][0]["status"] == "DONE"
        assert body["open_count"] == 0

    def test_reopen(self, client: TestClient, session) -> None:
        task = _add_task(session, status=TaskStatus.DONE)

        reopened = client.post(f"{API}/user-tasks/{task.id}/reopen")

        assert reopened.status_code == 200
        assert reopened.json()["status"] == "OPEN"

    def test_delete(self, client: TestClient, session) -> None:
        task = _add_task(session)

        removed = client.delete(f"{API}/user-tasks/{task.id}")

        assert removed.status_code == 204
        assert client.get(f"{API}/user-tasks").json()["total"] == 0

    def test_unknown_id_returns_404(self, client: TestClient) -> None:
        for method, path in (
            ("post", f"{API}/user-tasks/does-not-exist/complete"),
            ("post", f"{API}/user-tasks/does-not-exist/reopen"),
            ("delete", f"{API}/user-tasks/does-not-exist"),
        ):
            response = getattr(client, method)(path)
            assert response.status_code == 404

    def test_workspace_isolation(self, client: TestClient, session) -> None:
        other = _add_task(session, title="别人的任务", workspace_id="other")

        # 既不出现在列表里，也不能被改动。
        assert client.get(f"{API}/user-tasks").json()["total"] == 0
        assert client.post(f"{API}/user-tasks/{other.id}/complete").status_code == 404
        assert client.delete(f"{API}/user-tasks/{other.id}").status_code == 404

    def test_created_via_approval_is_exposed(self, client: TestClient, session) -> None:
        _add_task(session, created_via_approval_id="a" * 32)

        item = client.get(f"{API}/user-tasks").json()["items"][0]

        assert item["created_via_approval_id"] == "a" * 32


class TestUserTasksAreNotJobs:
    """D-013 的核心区分：两套实体、两套接口，不能混在一起。"""

    def test_creating_a_job_does_not_create_a_user_task(self, client: TestClient, session) -> None:
        from app.workers import queue

        queue.enqueue(session, job_type="noop")

        assert client.get(f"{API}/user-tasks").json()["total"] == 0
        assert client.get(f"{API}/jobs").json()["total"] == 1

    def test_jobs_endpoint_does_not_show_user_tasks(self, client: TestClient, session) -> None:
        _add_task(session)

        assert client.get(f"{API}/jobs").json()["total"] == 0

    def test_job_status_cannot_be_used_on_user_tasks(self, client: TestClient, session) -> None:
        """作业状态（RUNNING 等）不是用户任务的合法取值。"""
        _add_task(session)

        response = client.get(f"{API}/user-tasks", params={"status": "RUNNING"})

        assert response.status_code == 422

    def test_user_task_status_is_never_running_or_failed(self, client: TestClient, session) -> None:
        _add_task(session)

        item = client.get(f"{API}/user-tasks").json()["items"][0]

        assert item["status"] in {"OPEN", "DONE"}
        # 用户任务永远不该出现作业的状态（D-013）。
        assert item["status"] not in {"PENDING", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED"}

        # 作业表里确实能出现这些状态，两张表的状态机完全不同。
        other_job = Job(job_type="noop", payload="{}", status="RUNNING")
        session.add(other_job)
        session.commit()
        assert client.get(f"{API}/jobs").json()["items"][0]["status"] == "RUNNING"
