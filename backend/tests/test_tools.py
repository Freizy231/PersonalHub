"""search_memories 工具测试（M5）。

这个工具给模型提供了主动读取数据的能力，是 M5 里最需要谨慎的对象。
核心约束：只返回 APPROVED、只限当前工作空间。
"""

from __future__ import annotations

import json

import pytest

from app.capabilities import ToolContext, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.capabilities.tools import build_search_memories, register_builtin_tools
from app.models.memory import MemoryItem, MemoryStatus, content_fingerprint

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


@pytest.fixture()
def tool(session, monkeypatch):  # type: ignore[no-untyped-def]
    """构造在测试数据库上运行的工具。"""
    factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
    return build_search_memories(factory)


def _seed(session, content: str, status: str = MemoryStatus.APPROVED.value, **kwargs):  # type: ignore[no-untyped-def]
    item = MemoryItem(
        content=content,
        fingerprint=content_fingerprint(content),
        status=status,
        **kwargs,
    )
    session.add(item)
    session.commit()
    return item


class TestOnlyApprovedReturned:
    def test_approved_memory_is_returned(self, session, tool) -> None:
        _seed(session, "我偏好项目驱动学习")

        result = tool.handler({"query": "学习"}, CTX)

        assert result["count"] == 1
        assert result["memories"][0]["content"] == "我偏好项目驱动学习"

    def test_proposed_memory_is_never_returned(self, session, tool) -> None:
        """核心约束：未批准的候选绝不能通过工具泄漏给模型。"""
        _seed(session, "未确认的推测", status=MemoryStatus.PROPOSED.value)

        result = tool.handler({"query": ""}, CTX)

        assert result["count"] == 0

    def test_rejected_memory_is_never_returned(self, session, tool) -> None:
        _seed(session, "用户拒绝的事实", status=MemoryStatus.REJECTED.value)

        assert tool.handler({"query": ""}, CTX)["count"] == 0

    def test_deleted_memory_is_never_returned(self, session, tool) -> None:
        _seed(session, "已删除", status=MemoryStatus.DELETED.value)

        assert tool.handler({"query": ""}, CTX)["count"] == 0


class TestWorkspaceIsolation:
    def test_other_workspace_memory_hidden(self, session, tool) -> None:
        """不能通过工具绕过工作空间隔离（§8.2）。"""
        _seed(session, "别人的记忆", workspace_id="other")

        assert tool.handler({"query": ""}, CTX)["count"] == 0


class TestQueryBehaviour:
    def test_empty_query_returns_recent_memories(self, session, tool) -> None:
        for i in range(3):
            _seed(session, f"记忆 {i}")

        result = tool.handler({"query": ""}, CTX)

        assert result["count"] == 3

    def test_keyword_filters_results(self, session, tool) -> None:
        _seed(session, "我在学 TypeScript")
        _seed(session, "我喜欢喝茶")

        result = tool.handler({"query": "TypeScript"}, CTX)

        assert result["count"] == 1
        assert "TypeScript" in result["memories"][0]["content"]

    def test_multi_term_query_matches_any(self, session, tool) -> None:
        _seed(session, "我在学 Python")
        _seed(session, "我在学 Rust")

        result = tool.handler({"query": "Python Rust"}, CTX)

        assert result["count"] == 2

    def test_no_match_returns_empty(self, session, tool) -> None:
        _seed(session, "关于茶的偏好")

        assert tool.handler({"query": "量子物理"}, CTX)["count"] == 0

    def test_result_is_json_serialisable(self, session, tool) -> None:
        _seed(session, "可序列化测试")

        result = tool.handler({"query": ""}, CTX)

        json.dumps(result)  # 不抛异常即可


class TestLimits:
    def test_limit_is_respected(self, session, tool) -> None:
        for i in range(30):
            _seed(session, f"记忆 {i}")

        assert tool.handler({"query": "", "limit": 3}, CTX)["count"] == 3

    @pytest.mark.parametrize("bad_limit", [0, -5, 1000, "abc", True, None])
    def test_invalid_limit_falls_back_safely(self, session, tool, bad_limit) -> None:  # type: ignore[no-untyped-def]
        """非整数或越界的 limit 必须被夹紧，不能透传给查询。"""
        for i in range(25):
            _seed(session, f"记忆 {i}")

        result = tool.handler({"query": "", "limit": bad_limit}, CTX)

        assert 0 < result["count"] <= 20


class TestThroughBroker:
    @pytest.mark.asyncio
    async def test_broker_wraps_output_as_valid_json(self, session, tool) -> None:
        _seed(session, "经 Broker 调用")
        registry = ToolRegistry()
        registry.register(tool)

        result = await ToolBroker(registry).execute("search_memories", '{"query": ""}', CTX)

        assert result.ok
        assert json.loads(result.content)["count"] == 1

    def test_registry_exposes_spec_without_handler(self, session) -> None:
        factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
        registry = ToolRegistry()
        register_builtin_tools(registry, factory)

        specs = registry.specs_for_model()

        names = [s.name for s in specs]
        assert "search_memories" in names
        assert "create_task" in names
        assert "list_tasks" in names
        assert all(s.parameters["type"] == "object" for s in specs)

    def test_write_tools_are_marked_write(self, session) -> None:
        """M6 起允许写工具进入注册表，但必须正确标记 WRITE，
        否则 Policy Engine 不会要求审批（这是安全相关的断言）。"""
        from app.capabilities import RiskLevel

        factory = pytest.importorskip("sqlalchemy.orm").sessionmaker(bind=session.get_bind())
        registry = ToolRegistry()
        register_builtin_tools(registry, factory)

        by_name = {t.name: t for t in registry.all()}
        assert by_name["search_memories"].risk is RiskLevel.READ
        assert by_name["list_tasks"].risk is RiskLevel.READ
        assert by_name["create_task"].risk is RiskLevel.WRITE


class TestChineseMatching:
    """实测发现的检索缺陷：记忆写"我在学 TypeScript"，查询"学习"匹配不到。"""

    def test_two_char_query_matches_single_char_in_memory(self, session, tool) -> None:
        _seed(session, "我在学 TypeScript，偏好实战练习")

        assert tool.handler({"query": "学习"}, CTX)["count"] == 1

    def test_natural_language_question_matches(self, session, tool) -> None:
        _seed(session, "我在学 TypeScript，偏好实战练习")

        result = tool.handler({"query": "我喜欢什么样的学习方式"}, CTX)

        assert result["count"] == 1

    def test_stopwords_are_stripped(self) -> None:
        from app.capabilities.tools import _extract_terms

        terms = _extract_terms("我喜欢什么样的学习方式")

        assert "什么" not in terms
        assert "喜欢" not in terms
        assert "学习" in terms

    def test_unrelated_query_still_returns_empty(self, session, tool) -> None:
        _seed(session, "我在学 TypeScript")

        assert tool.handler({"query": "量子物理"}, CTX)["count"] == 0

    def test_english_matching_still_works(self, session, tool) -> None:
        _seed(session, "我在学 TypeScript")

        assert tool.handler({"query": "typescript"}, CTX)["count"] == 1
