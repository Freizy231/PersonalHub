"""M1 后端骨架测试。

覆盖 AGENT.md §9.2 要求的最小层次：
正常路径、缺失资源、无效方法、响应结构与 request_id 追踪。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app

API = "/api/v1"


class TestHealth:
    def test_returns_ok(self, client: TestClient) -> None:
        response = client.get(f"{API}/health")

        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ok"
        assert body["service"] == "personal-hub-backend"
        assert body["version"]
        assert body["timestamp"].endswith("+00:00")

    def test_response_has_request_id_header(self, client: TestClient) -> None:
        response = client.get(f"{API}/health")

        assert response.status_code == 200
        assert len(response.headers["X-Request-ID"]) == 32

    def test_incoming_valid_request_id_is_echoed(self, client: TestClient) -> None:
        response = client.get(f"{API}/health", headers={"X-Request-ID": "abcdef1234567890"})

        assert response.headers["X-Request-ID"] == "abcdef1234567890"

    def test_invalid_request_id_is_replaced(self, client: TestClient) -> None:
        # 过长、含空格等非法值必须被丢弃，防止日志注入。
        response = client.get(f"{API}/health", headers={"X-Request-ID": "bad id with spaces"})

        assert response.headers["X-Request-ID"] != "bad id with spaces"
        assert len(response.headers["X-Request-ID"]) == 32

    def test_health_does_not_require_database(self, client: TestClient) -> None:
        # M1 阶段没有任何数据库；能返回 200 即证明健康检查无持久化依赖。
        assert client.get(f"{API}/health").status_code == 200


class TestErrors:
    def test_unknown_path_returns_structured_404(self, client: TestClient) -> None:
        response = client.get(f"{API}/does-not-exist")

        assert response.status_code == 404
        body = response.json()
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["message"]
        assert body["error"]["request_id"]

    def test_wrong_method_returns_structured_405(self, client: TestClient) -> None:
        response = client.post(f"{API}/health")

        assert response.status_code == 405
        assert response.json()["error"]["code"] == "METHOD_NOT_ALLOWED"

    def test_error_body_has_no_internal_detail(self, client: TestClient) -> None:
        response = client.get(f"{API}/does-not-exist")

        text = response.text
        # 不得泄漏堆栈、文件路径或源码位置。
        for leak in ("Traceback", "site-packages", '.py"', "/home/"):
            assert leak not in text

    def test_error_response_does_not_include_allow_origin_for_foreign_origin(
        self, client: TestClient
    ) -> None:
        response = client.get(
            f"{API}/health",
            headers={"Origin": "https://evil.example"},
        )

        assert "access-control-allow-origin" not in response.headers


class TestCorsWhitelist:
    def test_allowed_origin_is_echoed(self, client: TestClient) -> None:
        response = client.get(f"{API}/health", headers={"Origin": "http://127.0.0.1:5173"})

        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"

    def test_custom_whitelist_from_settings(self) -> None:
        settings = Settings(allowed_origins=("http://127.0.0.1:6000",))
        with TestClient(create_app(settings)) as client:
            allowed = client.get(f"{API}/health", headers={"Origin": "http://127.0.0.1:6000"})
            denied = client.get(f"{API}/health", headers={"Origin": "http://127.0.0.1:5173"})

        assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:6000"
        assert "access-control-allow-origin" not in denied.headers

    def test_wildcard_origin_is_never_returned(self, client: TestClient) -> None:
        for origin in ("http://evil.example", "null", "http://127.0.0.1:9999"):
            response = client.get(f"{API}/health", headers={"Origin": origin})
            assert response.headers.get("access-control-allow-origin") != "*"


class TestConfig:
    def test_defaults_bind_loopback(self) -> None:
        settings = Settings()

        assert settings.host == "127.0.0.1"
        assert settings.port == 8000

    def test_invalid_port_env_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PH_PORT", "not-a-number")

        with pytest.raises(ValueError, match="PH_PORT"):
            Settings.from_env()

    def test_env_origins_parsed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("PH_ALLOWED_ORIGINS", "http://a.test, http://b.test ,")

        settings = Settings.from_env()

        assert settings.allowed_origins == ("http://a.test", "http://b.test")

    def test_settings_are_immutable(self) -> None:
        settings = Settings()
        with pytest.raises(AttributeError):
            settings.host = "0.0.0.0"  # type: ignore[misc]
