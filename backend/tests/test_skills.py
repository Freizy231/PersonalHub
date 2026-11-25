"""Skills 加载与 use_skill 工具测试（M8 第 3 项）。

核心约束：
1. Skill **只是一份文本**，加载过程不执行任何代码（无 import / eval / 子进程）。
2. 技能正文进入上下文时被标记为"参考方法而非指令"（§8.4）。
3. use_skill 只读内存，**不接受路径**，模型无法借它读取任意文件。
4. 一个写坏的技能文件不能拖垮其他技能或整个服务。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from app.capabilities import RiskLevel, ToolContext, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.capabilities.skills import (
    MAX_BODY_CHARS,
    SKILL_FILENAME,
    SkillError,
    SkillLibrary,
    build_catalog_block,
    build_skill,
    load_skill_directory,
    load_skills,
    parse_skill_file,
)
from app.capabilities.tools import build_use_skill, register_builtin_tools

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def write_skill(
    root: Path,
    directory: str,
    *,
    name: str = "测试技能",
    description: str = "用于测试",
    version: str | None = "1.0.0",
    body: str = "# 步骤\n1. 做这件事",
    raw: str | None = None,
) -> Path:
    """在 root 下写一个技能目录，返回该目录。"""
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        text = raw
    else:
        version_line = f"version: {version}\n" if version else ""
        text = f"---\nname: {name}\ndescription: {description}\n{version_line}---\n\n{body}\n"
    (path / SKILL_FILENAME).write_text(text, encoding="utf-8")
    return path


class TestParseFrontmatter:
    def test_parses_fields_and_body(self) -> None:
        text = "---\nname: 周报写作\ndescription: 按模板写\nversion: 2.1.0\n---\n\n# 正文\n"

        frontmatter, body = parse_skill_file(text)

        assert frontmatter == {"name": "周报写作", "description": "按模板写", "version": "2.1.0"}
        assert body == "# 正文"

    def test_version_is_optional(self) -> None:
        frontmatter, _ = parse_skill_file("---\nname: a\ndescription: b\n---\nx")

        assert frontmatter.get("version") is None

    def test_crlf_normalized(self) -> None:
        frontmatter, body = parse_skill_file("---\r\nname: a\r\ndescription: b\r\n---\r\n正文")

        assert frontmatter["name"] == "a"
        assert body == "正文"

    def test_comments_and_blank_lines_allowed(self) -> None:
        frontmatter, _ = parse_skill_file("---\n# 注释\nname: a\n\ndescription: b\n---\nx")

        assert frontmatter["name"] == "a"

    def test_colon_inside_value_is_kept(self) -> None:
        """描述里常有冒号（"步骤：先做 X"），只按第一个冒号切分。"""
        frontmatter, _ = parse_skill_file("---\nname: a\ndescription: 步骤：先做 X\n---\nx")

        assert frontmatter["description"] == "步骤：先做 X"

    @pytest.mark.parametrize(
        "raw",
        [
            "name: a\ndescription: b\n",
            "---\nname: a\ndescription: b\n",
            "---\nname a\n---\nx",
            "---\nname: a\ndescription: b\nunknown: c\n---\nx",
            "---\nname: a\nname: b\n---\nx",
            "---\nname: a\ndescription:\n---\nx",
            "---\nname: 'a'\ndescription: b\n---\nx",
            "---\nname: a\ndescription: [x, y]\n---\nx",
        ],
    )
    def test_invalid_frontmatter_rejected(self, raw: str) -> None:
        """猜错会让用户以为技能生效了，实际内容却不是他写的 —— 一律报错。"""
        with pytest.raises(SkillError):
            parse_skill_file(raw)

    def test_empty_file_rejected(self) -> None:
        with pytest.raises(SkillError):
            parse_skill_file("   \n")


class TestBuildSkill:
    def _build(self, **overrides):  # type: ignore[no-untyped-def]
        data = {
            "directory": "demo",
            "path": Path("/tmp/demo/SKILL.md"),
            "frontmatter": {"name": "演示", "description": "说明", "version": "1.0.0"},
            "body": "正文",
        }
        data.update(overrides)
        return build_skill(**data)

    def test_valid_skill(self) -> None:
        skill = self._build()

        assert skill.id == "demo"
        assert skill.version == "1.0.0"
        assert skill.directory_name == "demo"

    def test_missing_required_field(self) -> None:
        with pytest.raises(SkillError, match="缺少必需字段"):
            self._build(frontmatter={"name": "只有名字"})

    def test_invalid_directory_name(self) -> None:
        with pytest.raises(SkillError, match="目录名"):
            self._build(directory="Weekly Report")

    def test_invalid_version(self) -> None:
        with pytest.raises(SkillError, match="version"):
            self._build(frontmatter={"name": "a", "description": "b", "version": "v1"})

    def test_empty_body_rejected(self) -> None:
        with pytest.raises(SkillError, match="正文是空的"):
            self._build(body="   ")

    def test_oversized_body_rejected(self) -> None:
        with pytest.raises(SkillError, match="正文不能超过"):
            self._build(body="x" * (MAX_BODY_CHARS + 1))

    def test_name_too_long(self) -> None:
        with pytest.raises(SkillError, match="name"):
            self._build(frontmatter={"name": "长" * 81, "description": "b", "version": "1.0.0"})


class TestLoadFromDisk:
    def test_loads_valid_skill(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "weekly-report", name="周报写作")

        results = load_skills(tmp_path)

        assert len(results) == 1
        assert results[0].ok
        assert results[0].skill.name == "周报写作"  # type: ignore[union-attr]
        assert results[0].skill.id == "weekly-report"  # type: ignore[union-attr]

    def test_missing_root_returns_empty(self, tmp_path: Path) -> None:
        """用户还没建技能目录是正常状态，不是错误。"""
        assert load_skills(tmp_path / "nope") == []

    def test_directory_without_skill_file_is_reported_not_fatal(self, tmp_path: Path) -> None:
        (tmp_path / "empty-dir").mkdir()

        results = load_skills(tmp_path)

        assert len(results) == 1
        assert not results[0].ok
        assert "缺少 SKILL.md" in results[0].error  # type: ignore[operator]

    def test_broken_skill_does_not_block_others(self, tmp_path: Path) -> None:
        """一个写坏的文件不能拖垮整批加载。"""
        write_skill(tmp_path, "good-one", name="好的")
        write_skill(tmp_path, "bad-one", raw="没有 frontmatter")

        results = load_skills(tmp_path)

        by_dir = {r.directory: r for r in results}
        assert by_dir["good-one"].ok
        assert not by_dir["bad-one"].ok

    def test_hidden_directories_ignored(self, tmp_path: Path) -> None:
        write_skill(tmp_path, ".hidden", name="隐藏")
        write_skill(tmp_path, "visible", name="可见")

        assert [r.directory for r in load_skills(tmp_path)] == ["visible"]

    def test_non_utf8_file_reported(self, tmp_path: Path) -> None:
        path = tmp_path / "latin"
        path.mkdir()
        (path / SKILL_FILENAME).write_bytes(b"---\nname: \xff\xfe\n---\nx")

        result = load_skill_directory(path)

        assert not result.ok
        assert result.error is not None

    def test_error_message_names_the_problem(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "bad", raw="---\nname: a\ndescription: b\nextra: c\n---\nx")

        result = load_skill_directory(tmp_path / "bad")

        assert not result.ok
        assert "extra" in (result.error or "")


class TestSkillLibrary:
    def _library(self, tmp_path: Path) -> SkillLibrary:
        write_skill(tmp_path, "weekly-report", name="周报写作")
        write_skill(tmp_path, "paper", name="论文精读")
        return SkillLibrary(load_skills(tmp_path))

    def test_len_and_sorted_skills(self, tmp_path: Path) -> None:
        library = self._library(tmp_path)

        assert len(library) == 2
        assert [s.id for s in library.skills] == ["paper", "weekly-report"]

    def test_resolve_by_id_and_name(self, tmp_path: Path) -> None:
        library = self._library(tmp_path)

        assert library.resolve("weekly-report") is not None
        assert library.resolve("周报写作").id == "weekly-report"  # type: ignore[union-attr]

    def test_resolve_unknown_returns_none(self, tmp_path: Path) -> None:
        assert self._library(tmp_path).resolve("不存在") is None

    def test_resolve_empty_reference(self, tmp_path: Path) -> None:
        assert self._library(tmp_path).resolve("   ") is None

    def test_ambiguous_names_detected(self, tmp_path: Path) -> None:
        """重名不阻止加载，但按名称解析时必须能发现歧义。"""
        write_skill(tmp_path, "one", name="同名")
        write_skill(tmp_path, "two", name="同名")

        library = SkillLibrary(load_skills(tmp_path))

        assert library.ambiguous_names() == {"同名": ["one", "two"]}
        # 按 id 仍然明确。
        assert library.resolve("one").id == "one"  # type: ignore[union-attr]

    def test_failures_exposed(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "ok-one", name="好的")
        write_skill(tmp_path, "bad", raw="x")

        library = SkillLibrary(load_skills(tmp_path))

        assert [f.directory for f in library.failures] == ["bad"]


class TestCatalogBlock:
    def test_empty_library_yields_empty_block(self, tmp_path: Path) -> None:
        assert build_catalog_block(SkillLibrary([])) == ""

    def test_block_lists_id_name_description_only(self, tmp_path: Path) -> None:
        """目录里不能带正文：正文按需加载，否则等于"全部常驻"。"""
        write_skill(
            tmp_path, "weekly-report", name="周报写作", description="按模板写", body="秘密正文"
        )

        block = build_catalog_block(SkillLibrary(load_skills(tmp_path)))

        assert "weekly-report" in block
        assert "周报写作" in block
        assert "按模板写" in block
        assert "秘密正文" not in block

    def test_block_declares_content_is_not_instructions(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "x", name="甲")

        block = build_catalog_block(SkillLibrary(load_skills(tmp_path)))

        assert "不是系统指令" in block


class TestUseSkillTool:
    def _tool(self, tmp_path: Path):  # type: ignore[no-untyped-def]
        write_skill(tmp_path, "weekly-report", name="周报写作", body="# 周报结构\n1. 进展")
        return build_use_skill(SkillLibrary(load_skills(tmp_path)))

    def test_returns_body_by_id(self, tmp_path: Path) -> None:
        result = self._tool(tmp_path).handler({"skill": "weekly-report"}, CTX)

        assert result["body"] == "# 周报结构\n1. 进展"
        assert result["id"] == "weekly-report"
        # 明确标注来源与信任等级（§8.4）。
        assert result["source"] == "skill"
        assert result["trust"] == "user-provided-reference"

    def test_returns_body_by_name(self, tmp_path: Path) -> None:
        result = self._tool(tmp_path).handler({"skill": "周报写作"}, CTX)

        assert result["id"] == "weekly-report"

    def test_tool_is_read_only(self, tmp_path: Path) -> None:
        assert self._tool(tmp_path).risk is RiskLevel.READ

    def test_unknown_skill_lists_available(self, tmp_path: Path) -> None:
        from app.capabilities import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="weekly-report"):
            self._tool(tmp_path).handler({"skill": "不存在"}, CTX)

    def test_empty_skill_argument_rejected(self, tmp_path: Path) -> None:
        from app.capabilities import ToolExecutionError

        with pytest.raises(ToolExecutionError):
            self._tool(tmp_path).handler({"skill": "  "}, CTX)

    def test_schema_has_no_path_parameter(self, tmp_path: Path) -> None:
        """核心安全断言：模型无法通过这个工具读任意文件。"""
        schema = self._tool(tmp_path).parameters

        properties = schema["properties"]
        assert isinstance(properties, dict)
        assert set(properties) == {"skill"}

    def test_traversal_attempt_is_just_an_unknown_skill(self, tmp_path: Path) -> None:
        """即使模型传路径，也只会得到"没有这个技能"，不会读到文件。"""
        from app.capabilities import ToolExecutionError

        with pytest.raises(ToolExecutionError, match="没有名为"):
            self._tool(tmp_path).handler({"skill": "../../etc/passwd"}, CTX)

    @pytest.mark.asyncio
    async def test_through_broker(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register(self._tool(tmp_path))

        result = await ToolBroker(registry).execute("use_skill", '{"skill": "weekly-report"}', CTX)

        assert result.ok
        assert json.loads(result.content)["body"].startswith("# 周报结构")


class TestRegistration:
    def test_use_skill_registered_only_when_skills_exist(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        register_builtin_tools(registry, None, skills=SkillLibrary([]))  # type: ignore[arg-type]

        assert registry.get("use_skill") is None

    def test_use_skill_registered_with_skills(self, tmp_path: Path) -> None:
        write_skill(tmp_path, "x", name="甲")
        registry = ToolRegistry()
        register_builtin_tools(
            registry,
            None,
            skills=SkillLibrary(load_skills(tmp_path)),  # type: ignore[arg-type]
        )

        assert registry.get("use_skill") is not None


class TestNoCodeExecution:
    """Skill 只是文本。这一组测试防止将来有人往加载器里塞执行逻辑。"""

    def test_loading_does_not_import_or_eval(self, tmp_path: Path) -> None:
        sentinel = "ph_skill_should_never_run"
        write_skill(
            tmp_path,
            "sneaky",
            name="可疑",
            body=f"import os\nos.system('touch /tmp/{sentinel}')\n__import__('os')",
        )

        library = SkillLibrary(load_skills(tmp_path))

        assert len(library) == 1
        assert sentinel not in sys.modules
        assert not Path(f"/tmp/{sentinel}").exists()

    def test_loader_module_has_no_execution_primitives(self) -> None:
        """静态检查：加载器源码中不应出现 eval/exec/子进程调用。"""
        source = Path("app/capabilities/skills.py").read_text(encoding="utf-8")
        code_lines = [
            line
            for line in source.splitlines()
            if not line.strip().startswith("#") and '"""' not in line
        ]
        joined = "\n".join(code_lines)
        for primitive in ("eval(", "exec(", "subprocess", "os.system", "__import__"):
            assert primitive not in joined, f"技能加载器不应包含 {primitive}"


class TestSkillIntegrationWithChat:
    """端到端：技能目录必须真的进入对话上下文，正文按需加载。"""

    @pytest.mark.usefixtures("db_path")
    def test_catalog_injected_into_system_messages(self, settings, tmp_path: Path) -> None:
        from collections.abc import AsyncIterator

        from fastapi.testclient import TestClient

        import app.api.chat as chat_module
        from app.ai import ChatMessage, StreamChunk
        from app.core.config import Settings
        from app.main import create_app

        write_skill(settings.skills_dir, "weekly-report", name="周报写作", description="按模板写")
        write_skill(settings.skills_dir, "paper", name="论文精读", description="拆解论文")

        sent: list[list[ChatMessage]] = []

        class Recorder:
            name = "recorder"

            async def stream_chat(self, messages, model, tools=None) -> AsyncIterator[StreamChunk]:  # type: ignore[no-untyped-def]
                sent.append(list(messages))
                yield StreamChunk(delta="好的")

        monkeypatch_provider = Recorder
        settings_with_key = Settings(data_dir=settings.data_dir)
        app = create_app(settings_with_key)
        app_module = chat_module
        original = app_module.build_provider
        app_module.build_provider = lambda _s: monkeypatch_provider()  # type: ignore[assignment]
        try:
            import os

            os.environ.setdefault("DEEPSEEK_API_KEY", "sk-test")
            with TestClient(app) as client:
                cid = client.post("/api/v1/conversations", json={"title": "t"}).json()["id"]
                client.post(
                    "/api/v1/chat/stream",
                    json={"conversation_id": cid, "content": "帮我写周报"},
                )
        finally:
            app_module.build_provider = original  # type: ignore[assignment]

        system_messages = [m.content for m in sent[0] if m.role == "system"]
        joined = "\n".join(system_messages)
        assert "weekly-report" in joined
        assert "论文精读" in joined
        # 目录里不能出现正文
        assert "# 步骤" not in joined

    def test_use_skill_tool_available_to_model(self, settings, db_path, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        write_skill(settings.skills_dir, "weekly-report", name="周报写作")
        app = create_app(settings)
        with TestClient(app):
            names = [tool.name for tool in app.state.tool_registry.all()]

        assert "use_skill" in names

    def test_broken_skill_does_not_prevent_startup(self, settings, db_path, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        write_skill(settings.skills_dir, "good", name="好的")
        write_skill(settings.skills_dir, "bad", raw="坏的")
        app = create_app(settings)

        with TestClient(app) as client:
            assert client.get("/api/v1/health").status_code == 200
            capabilities = client.get("/api/v1/capabilities").json()

        ids = {item["id"] for item in capabilities["items"]}
        assert "skill.good" in ids
        assert "skill.bad" not in ids

    def test_skills_listed_as_capabilities(self, settings, db_path, tmp_path: Path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        write_skill(settings.skills_dir, "weekly-report", name="周报写作", description="按模板写")
        app = create_app(settings)

        with TestClient(app) as client:
            body = client.get("/api/v1/capabilities", params={"type": "skill"}).json()

        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == "skill.weekly-report"
        assert item["name"] == "周报写作"
        # 技能不执行任何操作，不应声明权限。
        assert item["permissions"] == []
