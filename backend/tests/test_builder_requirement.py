"""Requirement Engine 测试（M9 第 2 步）。

模型输出是不可信数据，因此测试全部用**假 Provider**，确定性、无网络。

重点：
1. 合法需求被正确解析；非法/缺字段/越界一律拒绝，不用默认值填补；
2. 范围筛查能拦住明显超范围的请求，**且不误杀正常需求**；
3. 供应商错误被翻译成可读的需求解析错误。
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator

import pytest

from app.ai import ChatMessage, ProviderError, StreamChunk
from app.builder.requirement import (
    MAX_TITLE_LENGTH,
    Capability,
    Requirement,
    RequirementError,
    build_app_id,
    check_scope,
    parse_request,
    parse_requirement,
)


class FakeProvider:
    """按脚本返回文本的假供应商。"""

    name = "fake"

    def __init__(self, text: str = "", error: ProviderError | None = None) -> None:
        self.text = text
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
        for char in self.text:
            yield StreamChunk(delta=char)


def _payload(**overrides):  # type: ignore[no-untyped-def]
    data = {
        "title": "待办清单模板",
        "purpose": "展示一份固定的待办事项",
        "elements": ["标题", "待办列表", "每项的状态标记"],
        "capability": "static_display",
    }
    data.update(overrides)
    return json.dumps(data, ensure_ascii=False)


class TestParsingValid:
    def test_parses_complete_requirement(self) -> None:
        req = parse_requirement(_payload(), request_text="做一个待办清单")

        assert req.title == "待办清单模板"
        assert req.purpose == "展示一份固定的待办事项"
        assert req.elements == ("标题", "待办列表", "每项的状态标记")
        assert req.capability is Capability.STATIC_DISPLAY
        assert req.raw_request == "做一个待办清单"

    def test_accepts_json_code_fence(self) -> None:
        raw = f"```json\n{_payload()}\n```"

        assert parse_requirement(raw, request_text="x").title == "待办清单模板"

    def test_local_compute_capability(self) -> None:
        req = parse_requirement(_payload(capability="local_compute"), request_text="做一个计算器")

        assert req.capability is Capability.LOCAL_COMPUTE

    def test_app_id_is_stable_for_same_title(self) -> None:
        """同一标题总是得到同一个 id，便于用户重复生成时覆盖而非堆积。"""
        first = parse_requirement(_payload(), request_text="x").app_id
        second = parse_requirement(_payload(), request_text="y").app_id

        assert first == second
        assert first.startswith("app-")

    def test_different_titles_get_different_ids(self) -> None:
        a = parse_requirement(_payload(title="甲"), request_text="x").app_id
        b = parse_requirement(_payload(title="乙"), request_text="x").app_id

        assert a != b

    def test_prompt_block_is_plain_text(self) -> None:
        req = parse_requirement(_payload(), request_text="x")

        block = req.to_prompt_block()

        assert "待办清单模板" in block
        assert "{" not in block, "给生成器的需求描述不应是 JSON 结构"


class TestParsingInvalid:
    """模型输出不合规时一律拒绝，不用默认值填补。"""

    def test_non_json_rejected(self) -> None:
        with pytest.raises(RequirementError, match="没能理解"):
            parse_requirement("我觉得这个可以做", request_text="x")

    def test_json_array_rejected(self) -> None:
        with pytest.raises(RequirementError):
            parse_requirement("[]", request_text="x")

    def test_missing_title(self) -> None:
        with pytest.raises(RequirementError, match="缺少应用名称"):
            parse_requirement(_payload(title=""), request_text="x")

    def test_title_too_long(self) -> None:
        with pytest.raises(RequirementError, match="名称过长"):
            parse_requirement(_payload(title="长" * (MAX_TITLE_LENGTH + 1)), request_text="x")

    def test_missing_purpose(self) -> None:
        with pytest.raises(RequirementError, match="缺少用途"):
            parse_requirement(_payload(purpose="   "), request_text="x")

    def test_missing_elements(self) -> None:
        with pytest.raises(RequirementError, match="缺少界面要素"):
            parse_requirement(_payload(elements=[]), request_text="x")

    def test_elements_not_a_list(self) -> None:
        with pytest.raises(RequirementError):
            parse_requirement(_payload(elements="标题，列表"), request_text="x")

    def test_all_elements_blank(self) -> None:
        with pytest.raises(RequirementError, match="都是空的"):
            parse_requirement(_payload(elements=["  ", ""]), request_text="x")

    def test_unknown_capability(self) -> None:
        with pytest.raises(RequirementError, match="无法判断"):
            parse_requirement(_payload(capability="server_render"), request_text="x")

    def test_missing_capability(self) -> None:
        data = json.loads(_payload())
        del data["capability"]

        with pytest.raises(RequirementError, match="无法判断"):
            parse_requirement(json.dumps(data), request_text="x")

    def test_elements_are_capped(self) -> None:
        """超出上限的要素被截断，而不是无限扩大生成规模。"""
        req = parse_requirement(
            _payload(elements=[f"要素{i}" for i in range(30)]), request_text="x"
        )

        assert len(req.elements) == 12


class TestScopeCheck:
    """范围筛查：拦住明显超范围，且不误杀正常需求。"""

    @pytest.mark.parametrize(
        "text",
        [
            "做一个读书清单，列出我想读的书",
            "做一个番茄钟计时器",
            "做一个 BMI 计算器",
            "做一个单词卡，显示英语单词和释义",
            "做一个颜色搭配参考页",
            "展示一个公式：接口设计的三个原则",
            "做一个阅读清单，不需要保存数据，展示固定书单就行",
            "做一个不需要联网的天气展示页",
        ],
    )
    def test_legitimate_requests_pass(self, text: str) -> None:
        """不误杀是硬要求：误杀会让用户觉得工具不好用。"""
        check_scope(text)

    @pytest.mark.parametrize(
        "text,keyword",
        [
            ("做一个能联网查天气的小工具", "联网"),
            ("做一个调用接口获取股市数据的页面", "调用接口"),
            ("做一个爬取网页内容的工具", "爬取"),
            ("做一个能保存到数据库的记账本", "存到数据库"),
            ("做一个需要登录的页面", "登录"),
        ],
    )
    def test_out_of_scope_requests_rejected(self, text: str, keyword: str) -> None:
        with pytest.raises(RequirementError) as excinfo:
            check_scope(text)

        assert excinfo.value.code == "REQUIREMENT_OUT_OF_SCOPE"
        assert keyword in str(excinfo.value)

    def test_rejection_includes_hint(self) -> None:
        """拒绝时要告诉用户为什么、以及可以怎么办。"""
        with pytest.raises(RequirementError) as excinfo:
            check_scope("做一个联网的小工具")

        assert "无法访问网络" in str(excinfo.value)

    def test_negation_only_applies_when_adjacent(self) -> None:
        """实测教训：窗口过大会把"做一个**能**联网"里的"不"当成否定。"""
        from app.builder.requirement import _is_negated

        assert _is_negated("不需要联网", "联网")
        assert not _is_negated("做一个能联网查天气的", "联网")


class TestParseRequest:
    @pytest.mark.asyncio
    async def test_calls_provider_and_parses(self) -> None:
        provider = FakeProvider(_payload())

        req = await parse_request(provider, "m", "做一个待办清单")

        assert req.title == "待办清单模板"
        assert len(provider.calls) == 1
        assert "不要执行" in provider.calls[0][0].content, "提示词必须声明内容不是指令"
        assert "做一个待办清单" in provider.calls[0][1].content

    @pytest.mark.asyncio
    async def test_empty_request_rejected_without_calling_model(self) -> None:
        provider = FakeProvider(_payload())

        with pytest.raises(RequirementError, match="请先描述"):
            await parse_request(provider, "m", "   ")

        assert provider.calls == [], "空描述不该浪费一次模型调用"

    @pytest.mark.asyncio
    async def test_out_of_scope_rejected_without_calling_model(self) -> None:
        """前置筛查的意义：省一次模型调用。"""
        provider = FakeProvider(_payload())

        with pytest.raises(RequirementError, match="联网"):
            await parse_request(provider, "m", "做一个联网查天气的")

        assert provider.calls == []

    @pytest.mark.asyncio
    async def test_overlong_request_rejected(self) -> None:
        provider = FakeProvider(_payload())

        with pytest.raises(RequirementError, match="描述太长"):
            await parse_request(provider, "m", "长" * 2000)

    @pytest.mark.asyncio
    async def test_provider_error_translated(self) -> None:
        provider = FakeProvider(error=ProviderError("模型繁忙", code="PROVIDER_RATE_LIMIT"))

        with pytest.raises(RequirementError) as excinfo:
            await parse_request(provider, "m", "做一个待办清单")

        assert excinfo.value.code == "PROVIDER_RATE_LIMIT"
        assert "模型繁忙" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_model_garbage_does_not_create_requirement(self) -> None:
        """模型胡说八道时，不产生任何需求对象。"""
        provider = FakeProvider("好的，我来帮你做一个很棒的应用！")

        with pytest.raises(RequirementError):
            await parse_request(provider, "m", "做一个待办清单")


class TestAppId:
    def test_stable_and_valid(self) -> None:
        app_id = build_app_id("待办清单")

        assert app_id == build_app_id("待办清单")
        assert app_id.startswith("app-")
        assert len(app_id) <= 63

    def test_different_titles_differ(self) -> None:
        assert build_app_id("甲") != build_app_id("乙")


def test_requirement_is_frozen() -> None:
    """需求对象不可变，避免生成过程中被意外改写。"""
    req = parse_requirement(_payload(), request_text="x")

    # frozen dataclass 赋值会抛 FrozenInstanceError（AttributeError 的子类）
    with pytest.raises(AttributeError):
        req.title = "改掉"  # type: ignore[misc]


def test_raw_request_not_in_repr() -> None:
    """原始描述可能含用户隐私，不应出现在日志里。"""
    req: Requirement = parse_requirement(_payload(), request_text="我的私人需求内容")

    assert "我的私人需求内容" not in repr(req)
