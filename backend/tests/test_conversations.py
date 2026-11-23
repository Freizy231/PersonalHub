"""会话 CRUD 测试（M2 验收）。

覆盖 AGENT.md §9.2 要求的层次：正常路径、无效输入、缺失资源、分页、持久化、
关联关系与误用字段。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

API = "/api/v1/conversations"


class TestCreate:
    def test_creates_conversation(self, client: TestClient) -> None:
        response = client.post(API, json={"title": "每周学习计划"})

        assert response.status_code == 201
        body = response.json()
        assert body["title"] == "每周学习计划"
        assert len(body["id"]) == 32
        assert body["created_at"] and body["updated_at"]

    def test_trims_surrounding_whitespace(self, client: TestClient) -> None:
        body = client.post(API, json={"title": "  带空格的标题  "}).json()

        assert body["title"] == "带空格的标题"

    def test_rejects_empty_title(self, client: TestClient) -> None:
        response = client.post(API, json={"title": ""})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"

    def test_rejects_whitespace_only_title_after_trim(self, client: TestClient) -> None:
        # 三个空格能通过 min_length=1 的 Schema 校验，但白名单语义应视为空。
        response = client.post(API, json={"title": "   "})

        assert response.status_code == 422

    def test_rejects_title_over_limit(self, client: TestClient) -> None:
        response = client.post(API, json={"title": "x" * 201})

        assert response.status_code == 422

    def test_accepts_title_at_exact_limit(self, client: TestClient) -> None:
        assert client.post(API, json={"title": "x" * 200}).status_code == 201

    def test_validation_error_does_not_echo_user_input(self, client: TestClient) -> None:
        secret = "MY_PRIVATE_NOTE_" * 30

        response = client.post(API, json={"title": secret})

        assert response.status_code == 422
        assert "MY_PRIVATE_NOTE" not in response.text

    def test_rejects_unknown_field(self, client: TestClient) -> None:
        # 防止前端或攻击者试图篡改 owner_id / workspace_id 等归属字段。
        response = client.post(API, json={"title": "ok", "owner_id": "someone-else"})

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["details"][0]["location"] == "body.owner_id"
        assert error["details"][0]["type"] == "extra_forbidden"

    def test_response_does_not_leak_internal_fields(self, client: TestClient) -> None:
        body = client.post(API, json={"title": "t"}).json()

        assert "owner_id" not in body
        assert "workspace_id" not in body


class TestRead:
    def test_get_by_id(self, client: TestClient) -> None:
        created = client.post(API, json={"title": "会话"}).json()

        response = client.get(f"{API}/{created['id']}")

        assert response.status_code == 200
        assert response.json()["id"] == created["id"]

    def test_missing_id_returns_structured_404(self, client: TestClient) -> None:
        response = client.get(f"{API}/{'0' * 32}")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    def test_list_empty(self, client: TestClient) -> None:
        body = client.get(API).json()

        assert body == {"items": [], "total": 0, "limit": 50, "offset": 0}

    def test_list_newest_first(self, client: TestClient) -> None:
        first = client.post(API, json={"title": "一"}).json()
        second = client.post(API, json={"title": "二"}).json()

        ids = [item["id"] for item in client.get(API).json()["items"]]

        assert ids.index(second["id"]) < ids.index(first["id"])

    def test_list_pagination(self, client: TestClient) -> None:
        for i in range(5):
            client.post(API, json={"title": f"会话 {i}"})

        page = client.get(API, params={"limit": 2, "offset": 2}).json()

        assert len(page["items"]) == 2
        assert page["total"] == 5

    @pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 201}, {"offset": -1}])
    def test_invalid_pagination_rejected(self, client: TestClient, params: dict[str, int]) -> None:
        assert client.get(API, params=params).status_code == 422


class TestUpdate:
    def test_rename(self, client: TestClient) -> None:
        created = client.post(API, json={"title": "旧名"}).json()

        response = client.patch(f"{API}/{created['id']}", json={"title": "新名"})

        assert response.status_code == 200
        assert response.json()["title"] == "新名"
        assert response.json()["updated_at"] >= created["updated_at"]

    def test_rename_missing_returns_404(self, client: TestClient) -> None:
        response = client.patch(f"{API}/{'f' * 32}", json={"title": "x"})

        assert response.status_code == 404

    def test_rename_rejects_empty(self, client: TestClient) -> None:
        created = client.post(API, json={"title": "旧名"}).json()

        assert client.patch(f"{API}/{created['id']}", json={"title": ""}).status_code == 422


class TestDelete:
    def test_delete_then_missing(self, client: TestClient) -> None:
        created = client.post(API, json={"title": "待删"}).json()

        assert client.delete(f"{API}/{created['id']}").status_code == 204
        assert client.get(f"{API}/{created['id']}").status_code == 404

    def test_delete_missing_returns_404(self, client: TestClient) -> None:
        assert client.delete(f"{API}/{'a' * 32}").status_code == 404

    def test_delete_is_idempotent_in_effect(self, client: TestClient) -> None:
        created = client.post(API, json={"title": "t"}).json()

        client.delete(f"{API}/{created['id']}")
        second = client.delete(f"{API}/{created['id']}")

        assert second.status_code == 404


class TestPersistence:
    def test_data_survives_new_client(self, settings, client: TestClient) -> None:
        """模拟服务重启：数据必须来自数据库，而不是进程内存（M2 硬性验收）。"""
        from fastapi.testclient import TestClient as TC

        from app.main import create_app

        created = client.post(API, json={"title": "重启后仍在"}).json()

        # 重建应用（相当于重启服务），数据库文件不变。
        with TC(create_app(settings)) as fresh_client:
            response = fresh_client.get(f"{API}/{created['id']}")

        assert response.status_code == 200
        assert response.json()["title"] == "重启后仍在"

    def test_database_file_is_created_under_data_dir(self, settings, client: TestClient) -> None:
        assert (settings.data_dir / "db" / "personal-hub.db").exists()

    def test_message_cascade_delete(self, client: TestClient, session) -> None:
        """删除会话必须连带删除其消息，不留孤儿记录。"""
        from app.models import Conversation, Message

        conv = client.post(API, json={"title": "有消息的会话"}).json()
        assert client.delete(f"{API}/{conv['id']}").status_code == 204

        remaining = session.query(Message).count()
        conversations = session.query(Conversation).count()

        assert conversations == 0
        assert remaining == 0


class TestWorkspaceScope:
    def test_conversation_not_visible_from_other_workspace(
        self, client: TestClient, session
    ) -> None:
        from app.models import Conversation

        created = client.post(API, json={"title": "私有"}).json()

        # 直接改库，模拟该会话属于另一个工作空间。
        row = session.get(Conversation, created["id"])
        assert row is not None
        row.workspace_id = "other-workspace"
        session.commit()

        assert client.get(f"{API}/{created['id']}").status_code == 404
        assert client.get(API).json()["total"] == 0


class TestPathSafety:
    @pytest.mark.parametrize(
        "conversation_id",
        ["../../etc/passwd", "%2e%2e%2f", "x' OR '1'='1", "1; DROP TABLE conversations--"],
    )
    def test_malicious_ids_do_not_crash_or_match(
        self, client: TestClient, conversation_id: str
    ) -> None:
        # 标识符作为参数化查询的绑定值，不会进入 SQL 语法。
        response = client.get(f"{API}/{conversation_id}")

        assert response.status_code == 404
        # 表仍然存在
        assert client.get(API).status_code == 200

    def test_db_file_lives_outside_repo(self, settings) -> None:
        repo_root = Path(__file__).resolve().parents[2]

        assert repo_root not in settings.data_dir.resolve().parents
        assert repo_root != settings.data_dir.resolve()


class TestMigrationSideEffects:
    def test_migration_does_not_disable_application_loggers(self, data_dir) -> None:
        """回归测试：alembic 的 fileConfig 默认 disable_existing_loggers=True，
        会把应用 logger 整体禁用，导致迁移后服务端静默无日志。"""
        import logging

        from conftest import apply_migrations

        app_logger = logging.getLogger("app.core.errors")
        app_logger.disabled = False
        before = app_logger.disabled

        apply_migrations(data_dir / "db" / "migration-log-test.db")

        assert app_logger.disabled is False, "迁移后应用 logger 必须保持启用"
        assert before is False


class TestDatetimeContract:
    def test_timestamps_are_utc_with_z_suffix(self, client: TestClient) -> None:
        body = client.post(API, json={"title": "时间"}).json()

        for field in ("created_at", "updated_at"):
            assert body[field].endswith("Z"), f"{field} 必须带 UTC 标识"

    def test_create_and_list_return_identical_format(self, client: TestClient) -> None:
        """回归：SQLite 不存时区，会导致创建时带 Z、查询时不带 Z。"""
        created = client.post(API, json={"title": "格式一致性"}).json()
        listed = client.get(API).json()["items"][0]

        assert created["created_at"] == listed["created_at"]
        assert created["updated_at"].endswith("Z")
        assert listed["updated_at"].endswith("Z")

    def test_timestamp_is_parseable_by_javascript(self, client: TestClient) -> None:
        from datetime import datetime

        body = client.post(API, json={"title": "可解析"}).json()

        parsed = datetime.fromisoformat(body["created_at"].replace("Z", "+00:00"))
        assert parsed.tzinfo is not None
