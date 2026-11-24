"""应用接口测试（M9 第 4 步，生成改为后台作业后）。

全部使用假 Provider，不联网、不执行生成的代码。

重点：
1. **安全响应头**在返回应用内容的每个地方都存在（含预览）；
2. 前端不能注入 HTML：安装只读后端写在 staging 里的内容；
3. 失败路径不留下 staging 目录；
4. 安装、重复安装（新版本）、回滚、卸载的语义。

生成已经变成后台作业：`POST /apps/generate` 只入队并返回 job_id，
测试里需要等 Worker 跑完（`_wait_job`）。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai import ChatMessage, ProviderError, StreamChunk
from app.builder import jobs as builder_jobs
from app.core.config import Settings
from app.main import create_app

API = "/api/v1"

GOOD_APP = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待办清单</title></head>
<body><h1>我的待办</h1><ul><li>读书</li></ul></body>
</html>"""

REQUIREMENT_JSON = """{
  "title": "待办清单",
  "purpose": "展示一份固定的待办事项",
  "elements": ["标题", "待办列表"],
  "capability": "static_display"
}"""


class FakeProvider:
    """按脚本依次返回多段文本：第一次是需求 JSON，第二次是应用 HTML。"""

    name = "fake"

    def __init__(self, scripts: list[str], error: ProviderError | None = None) -> None:
        self.scripts = list(scripts)
        self.error = error
        self.calls: list[list[ChatMessage]] = []

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        model: str,
        tools=None,  # type: ignore[no-untyped-def]
    ) -> AsyncIterator[StreamChunk]:
        self.calls.append(list(messages))
        if self.error is not None:
            raise self.error
        text = self.scripts.pop(0) if self.scripts else ""
        yield StreamChunk(delta=text)


@pytest.fixture()
def app_client(settings: Settings, db_path: Path, monkeypatch) -> TestClient:
    provider = FakeProvider([REQUIREMENT_JSON, GOOD_APP])
    # 作业处理器里的 build_provider 在 create_app 时就绑定了，
    # 因此这里必须替换 builder.jobs 模块里那个名字。
    monkeypatch.setattr(builder_jobs, "build_provider", lambda s: provider, raising=False)
    monkeypatch.setattr("app.api.chat.build_provider", lambda s: provider)

    app = create_app(settings)
    app.state.fake_provider = provider
    with TestClient(app) as client:
        yield client


def _wait_job(client: TestClient, job_id: str, timeout: float = 15.0) -> dict:
    """轮询到作业结束。返回 JobDetail。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = client.get(f"{API}/jobs/{job_id}").json()
        if detail["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            return detail
        time.sleep(0.05)
    raise AssertionError(f"作业未在 {timeout} 秒内结束：{detail}")


def _submit(client: TestClient, request: str = "做一个待办清单") -> dict:
    response = client.post(f"{API}/apps/generate", json={"request": request})
    assert response.status_code == 202, response.text
    return response.json()


def _generate(client: TestClient, request: str = "做一个待办清单") -> dict:
    """提交并等到成功，返回 job 的 checkpoint（含 staging_id 等摘要）。"""
    accepted = _submit(client, request)
    detail = _wait_job(client, accepted["job_id"])
    assert detail["status"] == "SUCCEEDED", detail
    return {"staging_id": accepted["staging_id"], **detail["runs"][-1]["result"]}


class TestListEmpty:
    def test_no_apps(self, app_client: TestClient) -> None:
        assert app_client.get(f"{API}/apps").json()["items"] == []


class TestGenerateAccepted:
    """接口立即返回，生成在后台。"""

    def test_returns_202_with_job_id(self, app_client: TestClient) -> None:
        body = _submit(app_client)

        assert body["job_id"]
        assert body["staging_id"]
        assert body["status"] in ("PENDING", "RUNNING")
        assert body["job_url"].endswith(body["job_id"])

    def test_pending_response_is_fast(self, app_client: TestClient) -> None:
        """接口不做模型调用：耗时必须是毫秒级，而不是等生成完。"""
        started = time.perf_counter()

        _submit(app_client)

        assert time.perf_counter() - started < 1.0

    def test_job_reaches_succeeded(self, app_client: TestClient) -> None:
        accepted = _submit(app_client)

        detail = _wait_job(app_client, accepted["job_id"])

        assert detail["status"] == "SUCCEEDED"
        result = detail["runs"][-1]["result"]
        assert result["staging_id"] == accepted["staging_id"]
        assert result["title"] == "待办清单"

    def test_describe_request_tag(self, app_client: TestClient) -> None:
        accepted = _submit(app_client)

        _wait_job(app_client, accepted["job_id"])

        detail = app_client.get(f"{API}/jobs/{accepted['job_id']}").json()
        assert detail["job_type"] == "generate_app"


class TestGenerate:
    def test_generate_returns_staged_app(self, app_client: TestClient) -> None:
        body = _generate(app_client)

        assert body["staging_id"]
        assert body["app_id"].startswith("app-")
        assert body["title"] == "待办清单"

    def test_generated_app_is_not_installed_yet(self, app_client: TestClient) -> None:
        _generate(app_client)

        assert app_client.get(f"{API}/apps").json()["items"] == []

    def test_preview_content_has_security_headers(self, app_client: TestClient) -> None:
        body = _generate(app_client)

        response = app_client.get(f"{API}/apps/staging/{body['staging_id']}/content")

        assert response.status_code == 200
        assert response.text == GOOD_APP
        headers = response.headers
        csp = headers["content-security-policy"]
        assert "default-src 'none'" in csp
        assert "connect-src 'none'" in csp
        assert "form-action 'none'" in csp
        assert "frame-ancestors 'self'" in csp
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"
        assert headers["cross-origin-resource-policy"] == "same-origin"

    def test_requirement_out_of_scope_rejected_immediately(self, app_client: TestClient) -> None:
        """超范围在入队前就拒绝：省一次作业，也省一次模型调用。"""
        response = app_client.post(f"{API}/apps/generate", json={"request": "做一个能联网的工具"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "REQUIREMENT_OUT_OF_SCOPE"

    def test_generation_rejected_leaves_no_staging(
        self, app_client: TestClient, settings: Settings
    ) -> None:
        app_client.app.state.fake_provider.scripts = [
            REQUIREMENT_JSON,
            "<!doctype html><html><body><script>fetch('/x')</script></body></html>",
        ]

        accepted = _submit(app_client)
        detail = _wait_job(app_client, accepted["job_id"])

        assert detail["status"] == "FAILED"
        # 用户要能看到具体原因，且一次列全。
        assert "fetch" in (detail["last_error"] or "")
        assert detail["attempts"] == 1, "这类错误不该自动重试（重试也是同样结果）"
        staging = settings.builder_dir / "staging"
        assert not staging.exists() or list(staging.iterdir()) == []

    def test_empty_request_rejected_by_schema(self, app_client: TestClient) -> None:
        response = app_client.post(f"{API}/apps/generate", json={"request": ""})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_extra_fields_rejected(self, app_client: TestClient) -> None:
        """前端不能通过请求体塞进 HTML。"""
        response = app_client.post(
            f"{API}/apps/generate", json={"request": "做一个清单", "html": "<html></html>"}
        )

        assert response.status_code == 422

    def test_provider_error_marks_job_failed(self, app_client: TestClient) -> None:
        app_client.app.state.fake_provider.error = ProviderError(
            "模型繁忙", code="PROVIDER_RATE_LIMIT"
        )

        accepted = _submit(app_client)
        detail = _wait_job(app_client, accepted["job_id"])

        assert detail["status"] == "FAILED"
        assert "模型繁忙" in (detail["last_error"] or "")


class TestInstall:
    def test_install_creates_app(self, app_client: TestClient) -> None:
        staged = _generate(app_client)

        response = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install")

        assert response.status_code == 200, response.text
        app = response.json()["app"]
        assert app["name"] == "待办清单"
        assert app["version"] == "1.0.0"
        assert app["ok"] is True
        assert len(app_client.get(f"{API}/apps").json()["items"]) == 1

    def test_install_discards_staging(self, app_client: TestClient) -> None:
        staged = _generate(app_client)
        app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install")

        response = app_client.get(f"{API}/apps/staging/{staged['staging_id']}")

        assert response.status_code == 404

    def test_installed_content_has_same_headers(self, app_client: TestClient) -> None:
        staged = _generate(app_client)
        installed = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install").json()[
            "app"
        ]

        response = app_client.get(f"{API}/apps/{installed['id']}/content")

        assert response.status_code == 200
        assert "default-src 'none'" in response.headers["content-security-policy"]

    def test_second_install_becomes_new_version(self, app_client: TestClient) -> None:
        """同标题再生成，得到同一个 id，安装后是 1.0.1 而不是两个应用。"""
        first = _generate(app_client)
        app_client.post(f"{API}/apps/staging/{first['staging_id']}/install")

        app_client.app.state.fake_provider.scripts = [REQUIREMENT_JSON, GOOD_APP]
        second = _generate(app_client)
        response = app_client.post(f"{API}/apps/staging/{second['staging_id']}/install")

        app = response.json()["app"]
        assert app["version"] == "1.0.1"
        assert app["versions"] == ["1.0.0", "1.0.1"]
        assert len(app_client.get(f"{API}/apps").json()["items"]) == 1

    def test_unknown_staging_id(self, app_client: TestClient) -> None:
        response = app_client.post(f"{API}/apps/staging/nosuchid/install")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "APP_STAGING_NOT_FOUND"

    def test_invalid_staging_id_does_not_escape(self, app_client: TestClient) -> None:
        response = app_client.get(f"{API}/apps/staging/..%2F..%2Fetc/content")

        assert response.status_code in (404, 422)


class TestCancel:
    def test_cancel_queued_job(self, app_client: TestClient) -> None:
        """排队中的生成可以取消，且不会产出 staging。"""
        accepted = _submit(app_client)

        response = app_client.post(f"{API}/jobs/{accepted['job_id']}/cancel")

        # 可能已经被 Worker 领取（RUNNING 也能取消），也可能已结束。
        assert response.status_code in (200, 409)


class TestRollback:
    def test_rollback_to_previous(self, app_client: TestClient) -> None:
        first = _generate(app_client)
        app_id = app_client.post(f"{API}/apps/staging/{first['staging_id']}/install").json()["app"][
            "id"
        ]

        app_client.app.state.fake_provider.scripts = [
            REQUIREMENT_JSON,
            GOOD_APP.replace("读书", "跑步"),
        ]
        second = _generate(app_client)
        app_client.post(f"{API}/apps/staging/{second['staging_id']}/install")

        response = app_client.post(f"{API}/apps/{app_id}/rollback")

        assert response.status_code == 200
        assert response.json()["app"]["version"] == "1.0.0"
        assert "读书" in app_client.get(f"{API}/apps/{app_id}/content").text

    def test_rollback_with_single_version_conflicts(self, app_client: TestClient) -> None:
        staged = _generate(app_client)
        app_id = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install").json()[
            "app"
        ]["id"]

        response = app_client.post(f"{API}/apps/{app_id}/rollback")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "APP_NO_PREVIOUS"


class TestUninstall:
    def test_uninstall_removes_from_list_keeps_directory(
        self, app_client: TestClient, settings: Settings
    ) -> None:
        staged = _generate(app_client)
        app_id = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install").json()[
            "app"
        ]["id"]

        response = app_client.delete(f"{API}/apps/{app_id}")

        assert response.status_code == 200
        assert "保留" in response.json()["message"]
        assert app_client.get(f"{API}/apps").json()["items"] == []
        assert (settings.builder_dir / "apps" / f".{app_id}.uninstalled").is_dir()

    def test_uninstall_unknown(self, app_client: TestClient) -> None:
        assert app_client.delete(f"{API}/apps/nope").status_code == 404


class TestContentSecurity:
    """应用内容只能通过带安全头的接口取得。"""

    def test_content_type_is_html(self, app_client: TestClient) -> None:
        staged = _generate(app_client)

        response = app_client.get(f"{API}/apps/staging/{staged['staging_id']}/content")

        assert response.headers["content-type"].startswith("text/html")

    def test_no_download_endpoint(self, app_client: TestClient) -> None:
        """不提供下载：应用不能脱离沙箱环境被本地打开。"""
        staged = _generate(app_client)
        app_id = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install").json()[
            "app"
        ]["id"]

        for suffix in ("/download", "/file", "/raw.html"):
            assert app_client.get(f"{API}/apps/{app_id}{suffix}").status_code == 404

    def test_missing_app_content_is_404(self, app_client: TestClient) -> None:
        response = app_client.get(f"{API}/apps/nope/content")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "APP_NOT_FOUND"
