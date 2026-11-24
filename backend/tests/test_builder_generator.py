"""Generator 测试（M9 第 3 步）。

与第 2 步相同：全部用**假 Provider**，确定性、无网络、不执行生成的代码。

重点：
1. 模型输出的常见"包装"（代码块、前后夹带说明）能被正确定位；
2. 检查不通过时**不产生任何内容**，且把具体问题带回给用户；
3. 生成器自己不碰文件系统（"不通过则不落盘"的实现方式）。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.ai import ChatMessage, ProviderError, StreamChunk
from app.builder.generator import (
    GeneratedApp,
    GeneratorError,
    _extract_html,
    generate_app,
)
from app.builder.requirement import Capability, Requirement, build_app_id

GOOD_APP = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>待办清单</title></head>
<body>
  <h1>我的待办</h1>
  <ul><li>读书</li><li>跑步</li></ul>
</body>
</html>"""


class FakeProvider:
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
        # 按块返回，模拟流式；块边界刻意落在标签中间。
        for index in range(0, len(self.text), 7):
            yield StreamChunk(delta=self.text[index : index + 7])


def _requirement(title: str = "待办清单") -> Requirement:
    return Requirement(
        app_id=build_app_id(title),
        title=title,
        purpose="展示一份固定的待办事项",
        elements=("标题", "待办列表"),
        capability=Capability.STATIC_DISPLAY,
        raw_request="做一个待办清单",
    )


class TestExtractHtml:
    """只做定位，不改写内容。"""

    def test_plain_document(self) -> None:
        assert _extract_html(GOOD_APP) == GOOD_APP

    def test_html_code_fence(self) -> None:
        raw = f"```html\n{GOOD_APP}\n```"

        assert _extract_html(raw) == GOOD_APP

    def test_bare_code_fence(self) -> None:
        raw = f"```\n{GOOD_APP}\n```"

        assert _extract_html(raw) == GOOD_APP

    def test_preamble_and_trailer_stripped(self) -> None:
        raw = f"好的，这是你要的页面：\n{GOOD_APP}\n希望对你有帮助！"

        assert _extract_html(raw) == GOOD_APP

    def test_missing_doctype_but_has_html(self) -> None:
        raw = "<html><body>hi</body></html>"

        assert _extract_html(raw) == raw

    def test_unclosed_document_kept_from_start(self) -> None:
        """截断的输出也要原样返回，交给静态检查判断，不在这里猜补什么。"""
        raw = "<!doctype html>\n<html><body><p>半截"

        assert _extract_html(raw).startswith("<!doctype html>")
        assert "半截" in _extract_html(raw)

    def test_empty(self) -> None:
        assert _extract_html("   ") == ""

    def test_non_html_text_returned_as_is(self) -> None:
        """没有 HTML 边界时，原样返回由校验层拒绝——不做猜测。"""
        assert _extract_html("我做不到") == "我做不到"


class TestGenerateSuccess:
    @pytest.mark.asyncio
    async def test_returns_html_and_metadata(self) -> None:
        provider = FakeProvider(GOOD_APP)

        app = await generate_app(provider, "m1", _requirement())

        assert isinstance(app, GeneratedApp)
        assert app.html == GOOD_APP
        assert app.model == "m1"
        assert app.validation.ok
        assert app.created_at.endswith("Z")

    @pytest.mark.asyncio
    async def test_prompt_states_constraints_and_untrusted_data(self) -> None:
        provider = FakeProvider(GOOD_APP)

        await generate_app(provider, "m1", _requirement())

        system = provider.calls[0][0].content
        user = provider.calls[0][1].content
        assert "单文件 HTML" in system
        assert "不是指令" in system
        assert "待办清单" in user
        # 需求以纯文本给出，而不是 JSON（见第 2 步的 to_prompt_block）。
        assert "{" not in user

    @pytest.mark.asyncio
    async def test_strips_code_fence_before_saving(self) -> None:
        provider = FakeProvider(f"```html\n{GOOD_APP}\n```")

        app = await generate_app(provider, "m1", _requirement())

        assert app.html.startswith("<!doctype html>")
        assert "```" not in app.html


class TestGenerateRejects:
    """任何不通过都**不产生内容**。"""

    @pytest.mark.asyncio
    async def test_network_call_rejected(self) -> None:
        bad = GOOD_APP.replace("<li>读书</li>", "<li onclick=\"fetch('/x')\">读书</li>")
        provider = FakeProvider(bad)

        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(provider, "m1", _requirement())

        assert excinfo.value.code == "GENERATOR_REJECTED"
        assert any("fetch" in message for message in excinfo.value.details)

    @pytest.mark.asyncio
    async def test_external_script_rejected(self) -> None:
        bad = GOOD_APP.replace(
            "</body>", '<script src="https://cdn.example.com/x.js"></script></body>'
        )

        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(FakeProvider(bad), "m1", _requirement())

        assert any("script" in message for message in excinfo.value.details)

    @pytest.mark.asyncio
    async def test_rejection_lists_all_problems(self) -> None:
        """一次列出全部问题，用户改一次就能过。"""
        bad = """<!doctype html><html><body>
        <iframe></iframe>
        <script>fetch('/a'); localStorage.setItem('k','v');</script>
        </body></html>"""
        provider = FakeProvider(bad)

        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(provider, "m1", _requirement())

        assert len(excinfo.value.details) >= 3

    @pytest.mark.asyncio
    async def test_empty_output_rejected(self) -> None:
        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(FakeProvider("   "), "m1", _requirement())

        assert excinfo.value.code == "GENERATOR_EMPTY_OUTPUT"

    @pytest.mark.asyncio
    async def test_chatter_only_rejected(self) -> None:
        """模型只回一句客套话时，静态检查会拦住它（没有 HTML 内容）。"""
        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(FakeProvider("好的，我来帮你做！"), "m1", _requirement())

        assert excinfo.value.code == "GENERATOR_REJECTED"

    @pytest.mark.asyncio
    async def test_provider_error_translated(self) -> None:
        provider = FakeProvider(error=ProviderError("模型繁忙", code="PROVIDER_RATE_LIMIT"))

        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(provider, "m1", _requirement())

        assert excinfo.value.code == "PROVIDER_RATE_LIMIT"
        assert "模型繁忙" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_error_message_does_not_leak_generated_code(self) -> None:
        """错误信息回到用户界面，不应包含模型原文或内部细节。"""
        bad = GOOD_APP.replace("</body>", "<script>alert(document.cookie)</script></body>")
        provider = FakeProvider(bad)

        with pytest.raises(GeneratorError) as excinfo:
            await generate_app(provider, "m1", _requirement())

        assert "alert" not in str(excinfo.value)
        assert "<script" not in str(excinfo.value)


class TestNoFilesystemAccess:
    """生成器不落盘：失败的生成不可能留下任何文件。"""

    @pytest.mark.asyncio
    async def test_generate_never_writes(self, tmp_path, monkeypatch) -> None:
        import pathlib

        written: list[str] = []
        original = pathlib.Path.write_text

        def record(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            written.append(str(self))
            return original(self, *args, **kwargs)

        monkeypatch.setattr(pathlib.Path, "write_text", record)
        monkeypatch.chdir(tmp_path)

        await generate_app(FakeProvider(GOOD_APP), "m1", _requirement())
        with pytest.raises(GeneratorError):
            await generate_app(FakeProvider("fetch('x')"), "m1", _requirement())

        assert written == []
        assert list(tmp_path.iterdir()) == []
