"""错误处理与日志的回归测试。

这些用例针对实际调试中发现的两个缺陷：
1. 500 响应缺少 X-Request-ID 响应头。
2. 未处理异常没有任何服务端日志。

以及 AGENT.md §8.4 的硬性要求：内部细节不得泄漏给前端。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app


def _app_with_boom_route(data_dir: Path):
    # 必须注入临时数据目录：create_app() 会创建数据库文件，
    # 若使用默认配置就会写进用户真实数据目录（AGENT.md §8.6）。
    app = create_app(Settings(data_dir=data_dir))
    router = APIRouter()

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("内部秘密 password=hunter2 /home/private/path.py")

    app.include_router(router, prefix="/api/v1")
    return app


@pytest.fixture()
def boom_client(tmp_path: Path) -> Iterator[TestClient]:
    # raise_server_exceptions=False 让 TestClient 返回真实 500 响应而不是抛出异常。
    app = _app_with_boom_route(tmp_path / "errors-test-data")
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


class TestInternalError:
    def test_returns_500_with_structured_body(self, boom_client: TestClient) -> None:
        response = boom_client.get("/api/v1/boom")

        assert response.status_code == 500
        body = response.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert body["error"]["request_id"]

    def test_response_header_carries_request_id(self, boom_client: TestClient) -> None:
        response = boom_client.get("/api/v1/boom", headers={"X-Request-ID": "aaaabbbbccccdddd"})

        assert response.headers["X-Request-ID"] == "aaaabbbbccccdddd"
        assert response.json()["error"]["request_id"] == "aaaabbbbccccdddd"

    def test_header_and_body_request_id_match(self, boom_client: TestClient) -> None:
        response = boom_client.get("/api/v1/boom")

        assert response.headers["X-Request-ID"] == response.json()["error"]["request_id"]

    @pytest.mark.parametrize(
        "secret",
        ["hunter2", "/home/private", "Traceback", "RuntimeError", "password="],
    )
    def test_no_internal_detail_leaks(self, boom_client: TestClient, secret: str) -> None:
        assert secret not in boom_client.get("/api/v1/boom").text

    def test_exception_is_logged_with_request_id(self, boom_client: TestClient, caplog) -> None:
        caplog.set_level(logging.ERROR, logger="app.core.errors")

        response = boom_client.get("/api/v1/boom", headers={"X-Request-ID": "aaaabbbbccccdddd"})

        records = [r for r in caplog.records if r.name == "app.core.errors"]
        assert records, "未处理异常必须记录日志"
        record = records[-1]
        assert record.request_id == "aaaabbbbccccdddd"
        assert record.exc_info is not None, "必须包含堆栈信息供服务端排查"
        assert record.method == "GET"
        assert record.path == "/api/v1/boom"
        assert response.status_code == 500

    def test_log_formatter_emits_json_and_keeps_exception_out_of_response(
        self, boom_client: TestClient
    ) -> None:
        from app.core.logging import JsonFormatter

        record = logging.LogRecord(
            name="app.core.errors",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="未处理异常",
            args=(),
            exc_info=None,
        )
        record.request_id = "abc123"
        payload = json.loads(JsonFormatter().format(record))

        assert payload["level"] == "ERROR"
        assert payload["request_id"] == "abc123"
        assert payload["message"] == "未处理异常"
        assert "timestamp" in payload
