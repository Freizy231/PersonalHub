"""应用接口测试（M9 第 4 步）。

全部使用假 Provider，不联网、不执行生成的代码。

重点：
1. **安全响应头**在返回应用内容的每个地方都存在（含预览）；
2. 前端不能注入 HTML：安装只读后端写在 staging 里的内容；
3. 失败路径不留下 staging 目录；
4. 安装、重复安装（新版本）、回滚、卸载的语义。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.ai import ChatMessage, ProviderError, StreamChunk
from app.api import apps as apps_module
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
    monkeypatch.setattr(apps_module, "build_provider", lambda s: provider)

    app = create_app(settings)
    app.state.fake_provider = provider
    with TestClient(app) as client:
        yield client


def _generate(client: TestClient, request: str = "做一个待办清单") -> dict:
    response = client.post(f"{API}/apps/generate", json={"request": request})
    assert response.status_code == 200, response.text
    return response.json()


class TestListEmpty:
    def test_no_apps(self, app_client: TestClient) -> None:
        assert app_client.get(f"{API}/apps").json() == []


class TestGenerate:
    def test_generate_returns_staged_app(self, app_client: TestClient) -> None:
        body = _generate(app_client)

        assert body["staging_id"]
        assert body["title"] == "待办清单"
        assert body["capability"] == "static_display"
        assert body["elements"] == ["标题", "待办列表"]
        assert body["size_bytes"] > 0

    def test_generated_app_is_not_installed_yet(self, app_client: TestClient) -> None:
        _generate(app_client)

        assert app_client.get(f"{API}/apps").json() == []

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
        assert headers["x-content-type-options"] == "nosniff"
        assert headers["cache-control"] == "no-store"
        assert headers["cross-origin-resource-policy"] == "same-origin"

    def test_requirement_out_of_scope_rejected(self, app_client: TestClient) -> None:
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

        response = app_client.post(f"{API}/apps/generate", json={"request": "做一个待办清单"})

        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "GENERATOR_REJECTED"
        # 具体原因要告诉用户，且一次列出。
        assert "fetch" in body["message"]
        staging = settings.builder_dir / "staging"
        assert not staging.exists() or list(staging.iterdir()) == []

    def test_empty_request_rejected_by_schema(self, app_client: TestClient) -> None:
        response = app_client.post(f"{API}/apps/generate", json={"request": ""})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_extra_fields_rejected(self, app_client: TestClient) -> None:
        response = app_client.post(
            f"{API}/apps/generate", json={"request": "做一个清单", "html": "<html></html>"}
        )

        assert response.status_code == 422

    def test_provider_not_configured_is_clear(
        self, settings: Settings, db_path: Path, monkeypatch
    ) -> None:
        from app.ai import ProviderConfigurationError

        def explode(_settings):  # type: ignore[no-untyped-def]
            raise ProviderConfigurationError("缺少密钥")

        monkeypatch.setattr(apps_module, "build_provider", explode)
        with TestClient(create_app(settings)) as client:
            response = client.post(f"{API}/apps/generate", json={"request": "做一个清单"})

        assert response.status_code == 503
        assert response.json()["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
        assert "缺少密钥" in response.json()["error"]["message"]


class TestInstall:
    def test_install_creates_app(self, app_client: TestClient) -> None:
        staged = _generate(app_client)

        response = app_client.post(f"{API}/apps/staging/{staged['staging_id']}/install")

        assert response.status_code == 200, response.text
        app = response.json()["app"]
        assert app["name"] == "待办清单"
        assert app["version"] == "1.0.0"
        assert app["ok"] is True
        assert len(app_client.get(f"{API}/apps").json()) == 1

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
        assert len(app_client.get(f"{API}/apps").json()) == 1

    def test_unknown_staging_id(self, app_client: TestClient) -> None:
        response = app_client.post(f"{API}/apps/staging/nosuchid/install")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "APP_STAGING_NOT_FOUND"

    def test_invalid_staging_id_does_not_escape(self, app_client: TestClient) -> None:
        response = app_client.get(f"{API}/apps/staging/..%2F..%2Fetc/content")

        assert response.status_code in (404, 422)


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
        assert app_client.get(f"{API}/apps").json() == []
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
