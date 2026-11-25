"""迭代 3：应用创建流程的后端能力。

覆盖三件事：
1. `GET /apps/staging/{id}` 给出界面需要的**完整**摘要（含版本预测与改动幅度），
   且**不再**回传 HTML；
2. `GET /apps/staging` 能列出未安装的生成结果（关掉页面后仍可找回）；
3. 生成作业的阶段事件是可读的固定文案，不含用户内容。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.builder.diff import diff_counts

API = "/api/v1"

GOOD_APP = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待办清单</title></head>
<body>
<h1>待办清单</h1>
<ul id="list"></ul>
<script>
  const items = ["写周报", "复习 TypeScript"];
  document.getElementById('list').innerHTML =
    items.map((item) => '<li>' + item + '</li>').join('');
</script>
</body>
</html>
"""


class TestDiffCounts:
    def test_identical_content_has_no_changes(self) -> None:
        assert diff_counts("a\nb\n", "a\nb\n") == (0, 0)

    def test_replace_counts_as_add_and_remove(self) -> None:
        """用户看 diff 的直觉：换掉一行 = 删一行 + 加一行。"""
        assert diff_counts("a\nb\nc\n", "a\nX\nc\n") == (1, 1)

    def test_pure_addition(self) -> None:
        assert diff_counts("a\n", "a\nb\n") == (1, 0)

    def test_pure_deletion(self) -> None:
        assert diff_counts("a\nb\n", "a\n") == (0, 1)

    def test_repeated_lines_are_not_skipped_as_noise(self) -> None:
        """HTML 里大量重复的 `</div>` 不能被当成噪声跳过。

        默认的 autojunk 启发式会把高频行忽略，统计因此严重失真——
        这里固定用 autojunk=False。
        """
        old = "<div>\n</div>\n" * 30
        new = "<div>\n<p>新内容</p>\n</div>\n" * 30

        added, removed = diff_counts(old, new)

        assert added == 30
        assert removed == 0


@pytest.fixture()
def app_client(settings, db_path: Path, monkeypatch) -> TestClient:  # type: ignore[no-untyped-def]
    """复用 test_apps_api 的假 Provider 装配。"""
    from app.ai import StreamChunk
    from app.builder import jobs as builder_jobs
    from app.core.config import Settings
    from app.main import create_app

    requirement_json = json.dumps(
        {
            "title": "待办清单",
            "purpose": "展示一组待办事项",
            "elements": ["标题", "列表"],
            "capability": "static_display",
        },
        ensure_ascii=False,
    )

    class FakeProvider:
        name = "fake"
        calls: list = []  # type: ignore[type-arg]

        def __init__(self, scripts: list[str]) -> None:
            self.scripts = scripts

        async def stream_chat(self, messages, model):  # type: ignore[no-untyped-def]
            text = self.scripts.pop(0) if self.scripts else ""
            yield StreamChunk(delta=text)

    provider = FakeProvider([requirement_json, GOOD_APP])
    monkeypatch.setattr(builder_jobs, "build_provider", lambda s: provider, raising=False)
    monkeypatch.setattr("app.api.chat.build_provider", lambda s: provider)

    app = create_app(Settings(data_dir=settings.data_dir) if False else settings)
    app.state.fake_provider = provider
    with TestClient(app) as client:
        yield client


def _generate(client: TestClient) -> dict:  # type: ignore[type-arg]
    import time

    accepted = client.post(
        f"{API}/apps/generate", json={"request": "做一个待办清单", "force": True}
    ).json()
    deadline = time.time() + 15
    while time.time() < deadline:
        detail = client.get(f"{API}/jobs/{accepted['job_id']}").json()
        if detail["status"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            assert detail["status"] == "SUCCEEDED", detail
            return {
                "staging_id": accepted["staging_id"],
                "job_id": accepted["job_id"],
                **detail["runs"][-1]["result"],
            }
        time.sleep(0.05)
    raise AssertionError("作业未结束")


class TestStagingSummary:
    def test_returns_full_metadata(self, app_client: TestClient) -> None:
        generated = _generate(app_client)

        body = app_client.get(f"{API}/apps/staging/{generated['staging_id']}").json()

        assert body["title"] == "待办清单"
        assert body["purpose"] == "展示一组待办事项"
        assert body["elements"] == ["标题", "列表"]
        assert body["capability"] == "static_display"
        assert body["app_id"].startswith("app-")
        assert body["size_bytes"] > 0

    def test_does_not_return_html(self, app_client: TestClient) -> None:
        """内容只经 `/content`（带 CSP）下发，不经过 JSON。"""
        generated = _generate(app_client)

        body = app_client.get(f"{API}/apps/staging/{generated['staging_id']}").json()

        assert "html" not in body
        assert "<!doctype" not in json.dumps(body)

    def test_first_install_is_not_an_update(self, app_client: TestClient) -> None:
        generated = _generate(app_client)

        body = app_client.get(f"{API}/apps/staging/{generated['staging_id']}").json()

        assert body["is_update"] is False
        assert body["current_version"] is None
        assert body["next_version"] == "1.0.0"
        assert body["change_stats"] is None
        assert body["change_reason"] == ""

    def test_version_prediction_after_install(self, app_client: TestClient) -> None:
        """装过一版后再次生成：预览阶段就要告诉用户"会变成 1.0.1"。

        实测缺口：原先前端只能在点安装**之后**才知道版本号。
        """
        first = _generate(app_client)
        app_client.post(f"{API}/apps/staging/{first['staging_id']}/install")

        app_client.app.state.fake_provider.scripts = [
            json.dumps(
                {
                    "title": "待办清单",
                    "purpose": "展示一组待办事项",
                    "elements": ["标题", "列表"],
                    "capability": "static_display",
                },
                ensure_ascii=False,
            ),
            GOOD_APP,
        ]
        second = _generate(app_client)

        body = app_client.get(f"{API}/apps/staging/{second['staging_id']}").json()

        assert body["current_version"] == "1.0.0"
        assert body["next_version"] == "1.0.1"

    def test_unknown_staging_is_404(self, app_client: TestClient) -> None:
        response = app_client.get(f"{API}/apps/staging/nosuchid123")

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "APP_STAGING_NOT_FOUND"

    def test_invalid_staging_id_does_not_escape(self, app_client: TestClient) -> None:
        response = app_client.get(f"{API}/apps/staging/..%2F..%2Fetc")

        assert response.status_code == 404


class TestChangeSuggestion:
    """`propose_app_change` 写入的 staging 必须在预览里被识别为"修改建议"。"""

    def test_update_is_marked_with_base_version_and_stats(
        self, app_client: TestClient, settings
    ) -> None:
        first = _generate(app_client)
        installed = app_client.post(f"{API}/apps/staging/{first['staging_id']}/install").json()
        app_id = installed["app"]["id"]

        store = app_client.app.state.app_store
        # 修改后的内容必须真的与已安装版本不同，才能验证差异统计。
        new_html = GOOD_APP.replace("<h1>待办清单</h1>", "<h1>待办清单</h1>\n<p>共 2 项</p>")
        staging_id = "a" * 32
        store.write_staging(staging_id, new_html)
        store.write_staging_meta(
            staging_id,
            {
                "staging_id": staging_id,
                "app_id": app_id,
                "title": "待办清单",
                "purpose": "加上条目计数",
                "elements": [],
                "capability": "",
                "generated_at": "2026-11-25T00:00:00Z",
                "base_version": installed["app"]["version"],
                "change_reason": "加上条目计数",
            },
        )

        body = app_client.get(f"{API}/apps/staging/{staging_id}").json()

        assert body["is_update"] is True
        assert body["base_version"] == installed["app"]["version"]
        assert body["change_reason"] == "加上条目计数"
        assert body["current_version"] == installed["app"]["version"]
        # 统计基于**已安装版本**与暂存内容的真实差异。
        assert body["change_stats"]["added_lines"] == 1
        assert body["change_stats"]["removed_lines"] == 0

    def test_update_for_missing_app_falls_back_to_install(self, app_client: TestClient) -> None:
        """应用已被卸载时，残留的修改建议不能自称"更新"。"""
        store = app_client.app.state.app_store
        staging_id = "b" * 32
        store.write_staging(staging_id, GOOD_APP)
        store.write_staging_meta(
            staging_id,
            {
                "staging_id": staging_id,
                "app_id": "app-doesnotexist",
                "title": "幽灵应用",
                "purpose": "",
                "elements": [],
                "capability": "",
                "base_version": "1.0.0",
                "change_reason": "改点什么",
                "generated_at": "2026-11-25T00:00:00Z",
            },
        )

        body = app_client.get(f"{API}/apps/staging/{staging_id}").json()

        assert body["is_update"] is False
        assert body["change_stats"] is None
        assert body["next_version"] == "1.0.0"


class TestStagingList:
    def test_lists_pending_staging(self, app_client: TestClient) -> None:
        generated = _generate(app_client)

        body = app_client.get(f"{API}/apps/staging").json()

        assert [item["staging_id"] for item in body["items"]] == [generated["staging_id"]]
        assert body["total"] == 1

    def test_install_removes_it_from_the_list(self, app_client: TestClient) -> None:
        generated = _generate(app_client)
        app_client.post(f"{API}/apps/staging/{generated['staging_id']}/install")

        assert app_client.get(f"{API}/apps/staging").json()["items"] == []

    def test_discard_removes_it_from_the_list(self, app_client: TestClient) -> None:
        generated = _generate(app_client)
        app_client.delete(f"{API}/apps/staging/{generated['staging_id']}")

        assert app_client.get(f"{API}/apps/staging").json()["items"] == []

    def test_newest_first(self, app_client: TestClient) -> None:
        first = _generate(app_client)
        app_client.app.state.fake_provider.scripts = [
            json.dumps(
                {
                    "title": "第二个应用",
                    "purpose": "另一个",
                    "elements": ["标题"],
                    "capability": "static_display",
                },
                ensure_ascii=False,
            ),
            GOOD_APP,
        ]
        second = _generate(app_client)

        items = app_client.get(f"{API}/apps/staging").json()["items"]

        assert [item["staging_id"] for item in items] == [
            second["staging_id"],
            first["staging_id"],
        ]

    def test_does_not_include_html(self, app_client: TestClient) -> None:
        _generate(app_client)

        body = json.dumps(app_client.get(f"{API}/apps/staging").json())

        assert "<!doctype" not in body

    def test_empty_when_nothing_pending(self, app_client: TestClient) -> None:
        body = app_client.get(f"{API}/apps/staging").json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_skips_directories_without_content(self, app_client: TestClient, settings) -> None:
        """写了一半就中断的目录不能被当成可预览的结果。"""
        staging_dir = settings.builder_dir / "staging" / ("c" * 32)
        staging_dir.mkdir(parents=True, exist_ok=True)
        (staging_dir / "requirement.json").write_text("{}", encoding="utf-8")

        assert app_client.get(f"{API}/apps/staging").json()["items"] == []

    def test_skips_invalid_directory_names(self, app_client: TestClient, settings) -> None:
        """目录名不合法的一律跳过，不让它进入响应。"""
        staging_root = settings.builder_dir / "staging"
        bad = staging_root / "UPPERCASE_NAME"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "requirement.json").write_text("{}", encoding="utf-8")
        (bad / "index.html").write_text(GOOD_APP, encoding="utf-8")

        assert app_client.get(f"{API}/apps/staging").json()["items"] == []


class TestStageEvents:
    def test_progress_events_are_readable_and_free_of_user_content(
        self, app_client: TestClient
    ) -> None:
        """阶段事件是固定文案，不含**需求原文**（§7.4）。

        成功事件会带应用标题（`已生成「待办清单」`），那是给用户看的标签、
        界面上本来就显示；这里约束的是**用户输入的原始描述**不进事件。
        """
        generated = _generate(app_client)

        detail = app_client.get(f"{API}/jobs/{generated['job_id']}").json()
        messages = [event["message"] for event in detail["events"]]

        assert "正在生成页面" in messages
        assert "已通过安全检查" in messages
        # 原始需求「做一个待办清单」不该出现在任何事件里。
        assert all("做一个待办清单" not in message for message in messages)
