"""设置接口测试（AGENT.md §2.5 Settings 页面）。

最重要的一条：**密钥绝不能出现在响应里**。这是 §0.4 红线，
而且是"看起来没问题、一旦泄漏就不可挽回"的典型。

其余覆盖：配置缺失时给出可操作提示；数据概览计数正确；只读。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

API = "/api/v1"


class TestProviderView:
    def test_reports_configured_without_leaking_key(self, client: TestClient, monkeypatch) -> None:
        """配置了 Key 时，响应里只能有"是否已配置"与变量名，不能有值。"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-super-secret-value-123")

        response = client.get(f"{API}/settings")

        assert response.status_code == 200
        body = response.text
        assert "sk-super-secret-value-123" not in body
        data = response.json()["provider"]
        assert data["api_key_configured"] is True
        assert data["api_key_env"] == "DEEPSEEK_API_KEY"
        assert data["ready"] is True

    def test_missing_key_gives_actionable_hint(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

        data = client.get(f"{API}/settings").json()["provider"]

        assert data["ready"] is False
        assert data["api_key_configured"] is False
        # 用户要能照着提示修好，而不是只看到"不可用"。
        assert "DEEPSEEK_API_KEY" in data["hint"]

    def test_custom_key_env_name(self, settings, db_path, monkeypatch) -> None:
        """中转站用户会改密钥变量名（PH_API_KEY_ENV）。

        设置是**启动时**读环境变量的，所以要在创建应用之前设置——
        这里显式重建客户端，而不是依赖默认 fixture 的进程级配置。
        """
        from app.core.config import Settings
        from app.main import create_app

        monkeypatch.setenv("PH_API_KEY_ENV", "MY_RELAY_KEY")
        monkeypatch.setenv("MY_RELAY_KEY", "relay-secret")

        with TestClient(create_app(Settings(data_dir=settings.data_dir))) as client:
            response = client.get(f"{API}/settings")

        assert "relay-secret" not in response.text
        assert response.json()["provider"]["api_key_env"] == "MY_RELAY_KEY"

    def test_shows_model_and_base_url(self, client: TestClient) -> None:
        data = client.get(f"{API}/settings").json()["provider"]

        assert data["model"]
        assert "base_url" in data


class TestDataView:
    def test_reports_paths(self, client: TestClient, settings) -> None:
        data = client.get(f"{API}/settings").json()["data"]

        assert data["data_dir"] == str(settings.data_dir)
        assert data["db_path"].endswith("personal-hub.db")

    def test_does_not_list_directory_contents(self, client: TestClient) -> None:
        """数据目录里有什么不该由这个接口枚举。"""
        data = client.get(f"{API}/settings").json()["data"]

        assert set(data) == {"data_dir", "db_path", "db_size_bytes", "free_disk_bytes"}

    def test_db_size_is_a_number(self, client: TestClient) -> None:
        data = client.get(f"{API}/settings").json()["data"]

        assert data["db_size_bytes"] > 0
        assert data["free_disk_bytes"] >= 0


class TestMemoryPolicy:
    def test_states_current_behaviour(self, client: TestClient) -> None:
        policy = client.get(f"{API}/settings").json()["memory_policy"]

        assert policy["requires_approval"] is True
        assert policy["only_approved_injected"] is True
        assert policy["secret_never_injected"] is True
        assert policy["context_budget_chars"] > 0
        assert policy["note"]


class TestCounts:
    def test_counts_are_present(self, client: TestClient) -> None:
        counts = client.get(f"{API}/settings").json()["counts"]

        assert set(counts) == {
            "conversations",
            "messages",
            "memories_approved",
            "memories_proposed",
            "tasks",
            "apps",
        }
        assert all(value >= 0 for value in counts.values())

    def test_reflects_actual_data(self, client: TestClient) -> None:
        from app.models.memory import MemoryItem, MemoryStatus

        # 通过 API 建会话，通过 session 造记忆：都走真实的存储层。
        client.post(f"{API}/conversations", json={"title": "测试会话"})
        from app.storage.database import build_engine, build_session_factory

        engine = build_engine(client.app.state.settings.db_path)
        factory = build_session_factory(engine)
        with factory() as s:
            s.add(MemoryItem(content="已批准", fingerprint="a", status=MemoryStatus.APPROVED.value))
            s.add(MemoryItem(content="候选", fingerprint="b", status=MemoryStatus.PROPOSED.value))
            s.commit()
        engine.dispose()

        counts = client.get(f"{API}/settings").json()["counts"]

        assert counts["conversations"] == 1
        assert counts["memories_approved"] == 1
        assert counts["memories_proposed"] == 1


class TestRuntime:
    def test_version_and_limits(self, client: TestClient) -> None:
        runtime = client.get(f"{API}/settings").json()["runtime"]

        assert runtime["version"]
        # 用**实际监听地址**，不是配置默认值：TestClient 的 scope 给的是 testserver。
        assert runtime["host"] == "testserver"
        assert runtime["max_apps"] > 0
        assert runtime["max_versions_per_app"] > 0


class TestReadOnly:
    """配置只能通过环境变量 + 重启修改（本期设计）。"""

    @pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
    def test_no_write_endpoints(self, client: TestClient, method: str) -> None:
        # delete 不接受 body，其余方法可以带一个空 body。
        kwargs = {} if method == "delete" else {"json": {}}
        response = getattr(client, method)(f"{API}/settings", **kwargs)

        assert response.status_code in (404, 405)

    def test_does_not_expose_env_values(self, client: TestClient, monkeypatch) -> None:
        monkeypatch.setenv("SOME_SECRET_TOKEN", "do-not-leak")

        response = client.get(f"{API}/settings")

        assert "do-not-leak" not in response.text
