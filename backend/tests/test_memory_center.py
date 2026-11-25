"""记忆中心的迭代 2 能力：搜索、已删除视图、近似候选提示。

安全与产品约束：
1. 近似提示**只提示**，不自动合并、不改任何数据；
2. 已批准记忆不出相似提示（没有可执行动作）；
3. 搜索只在本工作空间内匹配；
4. 不指定状态列出时**不包含**已删除项。
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models.memory import (
    MemoryItem,
    MemoryStatus,
    Sensitivity,
    content_fingerprint,
)

API = "/api/v1"


def _add(session, content: str, *, status=MemoryStatus.PROPOSED, **kwargs):  # type: ignore[no-untyped-def]
    item = MemoryItem(
        content=content,
        fingerprint=content_fingerprint(content),
        status=status.value,
        sensitivity=kwargs.pop("sensitivity", Sensitivity.PERSONAL.value),
        **kwargs,
    )
    session.add(item)
    session.commit()
    return item


class TestMemorySearch:
    def test_search_by_content(self, client: TestClient, session) -> None:
        _add(session, "我在学 TypeScript")
        _add(session, "我不吃辣")

        body = client.get(f"{API}/memory", params={"q": "TypeScript"}).json()

        assert [item["content"] for item in body["items"]] == ["我在学 TypeScript"]
        assert body["total"] == 1
        assert body["query"] == "TypeScript"

    def test_search_echoes_query(self, client: TestClient, session) -> None:
        _add(session, "我在学 TypeScript")

        body = client.get(f"{API}/memory", params={"q": "  "}).json()

        # 全空白查询等同于不搜索。
        assert body["query"] == "  "
        assert body["total"] == 1

    def test_multiple_words_are_all_required(self, client: TestClient, session) -> None:
        _add(session, "我在学 TypeScript，并且喜欢番茄钟")
        _add(session, "我在学 TypeScript")

        body = client.get(f"{API}/memory", params={"q": "TypeScript 番茄"}).json()

        assert [item["content"] for item in body["items"]] == ["我在学 TypeScript，并且喜欢番茄钟"]

    def test_no_match_returns_empty(self, client: TestClient, session) -> None:
        _add(session, "我在学 TypeScript")

        body = client.get(f"{API}/memory", params={"q": "完全不相关"}).json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_search_is_workspace_scoped(self, client: TestClient, session) -> None:
        _add(session, "别人的秘密偏好", workspace_id="other")

        assert client.get(f"{API}/memory", params={"q": "秘密偏好"}).json()["total"] == 0

    def test_search_combined_with_status(self, client: TestClient, session) -> None:
        _add(session, "项目驱动学习", status=MemoryStatus.APPROVED)
        _add(session, "项目驱动学习（候选）")

        body = client.get(f"{API}/memory", params={"q": "项目驱动", "status": "APPROVED"}).json()

        assert [item["status"] for item in body["items"]] == ["APPROVED"]


class TestDeletedView:
    def test_default_list_excludes_deleted(self, client: TestClient, session) -> None:
        doomed = _add(session, "会被删除的")
        client.delete(f"{API}/memory/{doomed.id}")

        body = client.get(f"{API}/memory").json()

        assert body["items"] == []
        assert body["total"] == 0

    def test_deleted_does_not_hide_other_items(self, client: TestClient, session) -> None:
        doomed = _add(session, "会被删除的")
        kept = _add(session, "保留的")
        client.delete(f"{API}/memory/{doomed.id}")

        body = client.get(f"{API}/memory").json()

        assert [item["id"] for item in body["items"]] == [kept.id]

    def test_deleted_status_is_listable(self, client: TestClient, session) -> None:
        item = _add(session, "我删掉的一条")
        client.delete(f"{API}/memory/{item.id}")

        body = client.get(f"{API}/memory", params={"status": "DELETED"}).json()

        assert [entry["id"] for entry in body["items"]] == [item.id]

    def test_counts_do_not_double_count_deleted(self, client: TestClient, session) -> None:
        item = _add(session, "候选")
        _add(session, "已批准", status=MemoryStatus.APPROVED)
        client.delete(f"{API}/memory/{item.id}")

        body = client.get(f"{API}/memory").json()

        assert body["pending_count"] == 0
        assert body["total"] == 1


class TestSimilarityHint:
    def test_near_duplicate_candidates_are_flagged(self, client: TestClient, session) -> None:
        first = _add(session, "我习惯用项目驱动的方式学习")
        second = _add(session, "我习惯项目驱动学习")

        items = {item["id"]: item for item in client.get(f"{API}/memory").json()["items"]}

        assert [entry["id"] for entry in items[first.id]["similar"]] == [second.id]
        assert [entry["id"] for entry in items[second.id]["similar"]] == [first.id]
        assert items[first.id]["similar"][0]["score"] > 0

    def test_unrelated_candidates_are_not_flagged(self, client: TestClient, session) -> None:
        _add(session, "我在学 TypeScript")
        _add(session, "我不吃辣")

        items = client.get(f"{API}/memory").json()["items"]

        assert all(item["similar"] == [] for item in items)

    def test_hint_does_not_change_data(self, client: TestClient, session) -> None:
        """只提示，绝不自动合并或自动批准（核心产品约束）。"""
        _add(session, "我习惯用项目驱动的方式学习")
        _add(session, "我习惯项目驱动学习")

        client.get(f"{API}/memory")

        statuses = [item["status"] for item in client.get(f"{API}/memory").json()["items"]]
        assert statuses == ["PROPOSED", "PROPOSED"]
        assert client.get(f"{API}/memory").json()["total"] == 2

    def test_approved_memories_are_not_flagged(self, client: TestClient, session) -> None:
        approved = _add(session, "我习惯项目驱动学习", status=MemoryStatus.APPROVED)
        candidate = _add(session, "我习惯用项目驱动的方式学习")

        items = {item["id"]: item for item in client.get(f"{API}/memory").json()["items"]}

        # 已批准的不提示；候选只和同状态的候选比较，因此也不指向已批准的那条。
        assert items[approved.id]["similar"] == []
        assert items[candidate.id]["similar"] == []

    def test_hint_ignores_search_filter(self, client: TestClient, session) -> None:
        """搜索只命中一条时，更需要提醒"还有一条几乎一样的没被过滤出来"。"""
        first = _add(session, "我习惯用项目驱动的方式学习")
        _add(session, "我习惯项目驱动学习")

        body = client.get(f"{API}/memory", params={"q": "方式"}).json()

        assert [item["id"] for item in body["items"]] == [first.id]
        assert len(body["items"][0]["similar"]) == 1

    def test_hint_is_workspace_scoped(self, client: TestClient, session) -> None:
        _add(session, "我习惯项目驱动学习", workspace_id="other")
        mine = _add(session, "我习惯项目驱动学习")

        item = client.get(f"{API}/memory").json()["items"][0]

        assert item["id"] == mine.id
        assert item["similar"] == []
