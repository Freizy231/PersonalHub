"""迭代 2：长会话分页（`has_more`）与错误可重试性（`retryable`）。

两条都是为了不在界面上说谎：
- `has_more` 让"加载更早"按钮只在**确实还有**时出现；
- `retryable` 让重试入口只在"原样再发一次可能成功"时出现。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from app.api.chat import RETRYABLE_ERROR_CODES, is_retryable
from app.models.conversation import Conversation
from app.models.message import Message

API = "/api/v1"


def _conversation(session, title="长会话") -> str:  # type: ignore[no-untyped-def]
    conversation = Conversation(title=title)
    session.add(conversation)
    session.commit()
    return conversation.id


def _messages(session, conversation_id: str, count: int) -> None:  # type: ignore[no-untyped-def]
    for index in range(count):
        session.add(Message(conversation_id=conversation_id, role="user", content=f"第 {index} 条"))
    session.commit()


class TestHasMore:
    def test_false_when_everything_fits(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 3)

        body = client.get(f"{API}/conversations/{cid}/messages", params={"limit": 10}).json()

        assert body["has_more"] is False
        assert body["total"] == 3

    def test_true_when_older_messages_exist(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 25)

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "offset": 0, "order": "desc"},
        ).json()

        assert len(body["items"]) == 10
        assert body["has_more"] is True
        # 取到的是最新的 10 条，且仍按时间正序返回。
        assert body["items"][-1]["content"] == "第 24 条"
        assert body["items"][0]["content"] == "第 15 条"

    def test_becomes_false_on_last_page(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 25)

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "offset": 20, "order": "desc"},
        ).json()

        assert len(body["items"]) == 5
        assert body["has_more"] is False
        assert body["items"][0]["content"] == "第 0 条"

    def test_exact_boundary_is_false(self, client: TestClient, session) -> None:
        """恰好一页时不能说"还有更多"——否则按钮点下去是空的。"""
        cid = _conversation(session)
        _messages(session, cid, 10)

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "offset": 0, "order": "desc"},
        ).json()

        assert body["has_more"] is False

    def test_paging_back_has_no_gaps_or_duplicates(self, client: TestClient, session) -> None:
        """逐页往回取，拼起来必须正好是全部消息，不重不漏。"""
        cid = _conversation(session)
        _messages(session, cid, 25)

        collected: list[str] = []
        offset = 0
        for _ in range(10):
            body = client.get(
                f"{API}/conversations/{cid}/messages",
                params={"limit": 10, "offset": offset, "order": "desc"},
            ).json()
            collected = [item["content"] for item in body["items"]] + collected
            offset += len(body["items"])
            if not body["has_more"]:
                break

        assert collected == [f"第 {index} 条" for index in range(25)]

    def test_empty_conversation(self, client: TestClient, session) -> None:
        cid = _conversation(session)

        body = client.get(f"{API}/conversations/{cid}/messages").json()

        assert body["items"] == []
        assert body["has_more"] is False

    def test_unknown_conversation_is_404(self, client: TestClient) -> None:
        assert client.get(f"{API}/conversations/nope/messages").status_code == 404


class TestRetryable:
    def test_transient_errors_are_retryable(self) -> None:
        for code in (
            "PROVIDER_TIMEOUT",
            "PROVIDER_UNREACHABLE",
            "PROVIDER_RATE_LIMIT",
            "PROVIDER_UNAVAILABLE",
            "STREAM_INTERRUPTED",
        ):
            assert is_retryable(code), code

    def test_configuration_errors_are_not_retryable(self) -> None:
        """没配 Key、地址错：重发一百次也一样，不能让用户点一个必然失败的按钮。"""
        for code in (
            "PROVIDER_NOT_CONFIGURED",
            "PROVIDER_AUTH",
            "PROVIDER_NOT_FOUND",
            "PROVIDER_BAD_REQUEST",
            "INVALID_ARGUMENTS",
        ):
            assert not is_retryable(code), code

    def test_unknown_or_missing_code_is_not_retryable(self) -> None:
        assert not is_retryable("SOMETHING_NEW")
        assert not is_retryable(None)
        assert not is_retryable("")

    def test_code_set_is_frozen(self) -> None:
        assert isinstance(RETRYABLE_ERROR_CODES, frozenset)


class TestErrorEventShape:
    """错误事件必须自带 retryable，前端不该自己维护第二份分类表。"""

    def test_error_event_includes_retryable(self) -> None:
        from app.api.chat import _error_event

        frame = _error_event("PROVIDER_RATE_LIMIT", "请求过于频繁，请稍后重试。")
        data = json.loads(frame.split("data:", 1)[1])

        assert data["code"] == "PROVIDER_RATE_LIMIT"
        assert data["retryable"] is True

    def test_configuration_error_is_not_retryable(self) -> None:
        from app.api.chat import _error_event

        frame = _error_event("PROVIDER_NOT_CONFIGURED", "请先配置 DEEPSEEK_API_KEY")
        data = json.loads(frame.split("data:", 1)[1])

        assert data["retryable"] is False


class TestCursorPaging:
    """游标分页（`before`）。迭代 2 选择它的原因就是 offset 会错位。"""

    def test_returns_page_before_anchor(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 25)
        newest = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc"},
        ).json()["items"]
        anchor = newest[0]["id"]

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc", "before": anchor},
        ).json()

        assert [item["content"] for item in body["items"]] == [
            f"第 {index} 条" for index in range(5, 15)
        ]
        assert body["has_more"] is True

    def test_cursor_is_stable_when_new_messages_arrive(self, client: TestClient, session) -> None:
        """核心：新消息写入后，同一个游标仍然返回同一页。

        offset 在这一步会错位（它从尾部数），这正是改用游标的原因。
        """
        cid = _conversation(session)
        _messages(session, cid, 25)
        first_page = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc"},
        ).json()["items"]
        anchor = first_page[0]["id"]

        # 用户在这期间又发了 3 条消息。
        _messages(session, cid, 3)

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc", "before": anchor},
        ).json()

        assert [item["content"] for item in body["items"]] == [
            f"第 {index} 条" for index in range(5, 15)
        ]
        # 与第一页没有重叠。
        assert (
            set(item["id"] for item in body["items"]) & set(item["id"] for item in first_page)
            == set()
        )

    def test_full_backfill_is_complete_and_ordered(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 37)

        collected: list[str] = []
        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc"},
        ).json()
        collected = [item["content"] for item in body["items"]] + collected
        while body["has_more"]:
            anchor = body["items"][0]["id"]
            body = client.get(
                f"{API}/conversations/{cid}/messages",
                params={"limit": 10, "order": "desc", "before": anchor},
            ).json()
            collected = [item["content"] for item in body["items"]] + collected

        assert collected == [f"第 {index} 条" for index in range(37)]

    def test_last_page_reports_no_more(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 12)
        newest = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc"},
        ).json()["items"]

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc", "before": newest[0]["id"]},
        ).json()

        assert len(body["items"]) == 2
        assert body["has_more"] is False

    def test_unknown_cursor_returns_empty(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 5)

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc", "before": "does-not-exist"},
        ).json()

        assert body["items"] == []
        assert body["has_more"] is False

    def test_cursor_from_other_conversation_is_ignored(self, client: TestClient, session) -> None:
        """别的会话的消息 id 不能当游标用（否则等于跨会话探测存在性）。"""
        cid = _conversation(session)
        other = _conversation(session, title="另一个")
        _messages(session, cid, 5)
        _messages(session, other, 5)
        foreign = client.get(f"{API}/conversations/{other}/messages").json()["items"][0]["id"]

        body = client.get(
            f"{API}/conversations/{cid}/messages",
            params={"limit": 10, "order": "desc", "before": foreign},
        ).json()

        assert body["items"] == []

    def test_before_requires_desc_order(self, client: TestClient, session) -> None:
        cid = _conversation(session)
        _messages(session, cid, 3)
        any_id = client.get(f"{API}/conversations/{cid}/messages").json()["items"][0]["id"]

        response = client.get(
            f"{API}/conversations/{cid}/messages", params={"order": "asc", "before": any_id}
        )

        assert response.status_code == 400

    def test_cursor_paging_is_workspace_scoped(self, client: TestClient, session) -> None:
        from app.models.conversation import Conversation

        other = Conversation(title="别人的", workspace_id="other")
        session.add(other)
        session.commit()
        _messages(session, other.id, 5)

        assert (
            client.get(
                f"{API}/conversations/{other.id}/messages", params={"order": "desc"}
            ).status_code
            == 404
        )


class TestNotConfiguredIsNotRetryable:
    """没配 Key 时接口返回 503。

    这是真实缺陷的回归：前端把"503 且没有结构化错误体"当成后端未启动
    （可重试）。如果服务端不显式说明 `retryable=false`，界面会给一个
    点了必然失败的重试按钮，用户会以为系统坏了。
    """

    def _client_without_key(self, settings, db_path):  # type: ignore[no-untyped-def]
        from fastapi.testclient import TestClient

        from app.core.config import Settings
        from app.main import create_app

        bare = Settings(
            data_dir=settings.data_dir,
            provider="openai-compatible",
            provider_base_url="http://127.0.0.1:9",
            api_key_env="PH_TEST_DEFINITELY_UNSET",
        )
        return TestClient(create_app(bare))

    def test_returns_structured_error_with_retryable_false(self, settings, db_path) -> None:
        client = self._client_without_key(settings, db_path)
        with client:
            cid = client.post(f"{API}/conversations", json={"title": "测试"}).json()["id"]

            response = client.post(
                f"{API}/chat/stream",
                json={"conversation_id": cid, "content": "你好"},
            )

        assert response.status_code == 503
        body = response.json()
        assert body["error"]["code"] == "PROVIDER_NOT_CONFIGURED"
        # 关键：显式声明不可重试，前端不能靠状态码猜。
        assert body["error"]["retryable"] is False
        # 文案给出可操作的信息，且不含密钥本身。
        assert "PH_TEST_DEFINITELY_UNSET" in body["error"]["message"]
        assert "sk-" not in body["error"]["message"]

    def test_error_without_retryable_omits_the_field(self) -> None:
        """没有表态的接口不返回该字段，前端按不可重试处理（安全默认）。"""
        from app.core.errors import error_body

        body = error_body("SOME_CODE", "文案", "rid")

        assert "retryable" not in body["error"]
