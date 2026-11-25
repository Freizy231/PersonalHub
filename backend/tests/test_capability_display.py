"""迭代 3 第 1–2 项：能力列表的统一展示。

三条约束：

1. 类型筛选只列**实际存在**的类型——写死清单会留下永远空的选项；
2. 能力描述是给用户看的**纯文本**，不依赖 Markdown 星号强调
   （能力页按纯文本渲染，`**完整**` 会原样露出）；
3. 端点的路由顺序：`/types` 不能被 `/{capability_id}` 吃掉。
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

API = "/api/v1"


class TestTypeList:
    def test_lists_only_existing_types(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities/types").json()

        types = {item["type"] for item in body["items"]}
        # 内建工具一定存在。
        assert "tool" in types
        # 从来没有工作流能力：它不该出现在筛选里。
        assert "workflow" not in types

    def test_counts_match_the_listing(self, client: TestClient) -> None:
        types = client.get(f"{API}/capabilities/types").json()["items"]

        for item in types:
            listing = client.get(f"{API}/capabilities", params={"type": item["type"]}).json()
            assert listing["total"] == item["count"], item["type"]

    def test_total_is_sum_of_counts(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities/types").json()

        assert body["total"] == sum(item["count"] for item in body["items"])

    def test_order_is_stable(self, client: TestClient) -> None:
        first = [item["type"] for item in client.get(f"{API}/capabilities/types").json()["items"]]
        second = [item["type"] for item in client.get(f"{API}/capabilities/types").json()["items"]]

        assert first == second

    def test_types_route_is_not_shadowed_by_id_route(self, client: TestClient) -> None:
        """回归：`/types` 若注册在 `/{capability_id}` 之后会被当成 id 查，返回 404。"""
        response = client.get(f"{API}/capabilities/types")

        assert response.status_code == 200
        assert "items" in response.json()

    def test_installed_skill_adds_its_type(self, client: TestClient, settings) -> None:
        skill_dir = settings.skills_dir / "weekly-report"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: 周报\ndescription: 按模板写周报\nversion: 1.0.0\n---\n\n正文\n",
            encoding="utf-8",
        )

        # 技能在启动时加载，这里直接确认注册表能反映出来。
        registry = client.app.state.capability_registry
        from app.capabilities.registry import manifest_from_skill
        from app.capabilities.skills import SkillLibrary, load_skills

        library = SkillLibrary(load_skills(settings.skills_dir))
        for skill in library.skills:
            registry.register(manifest_from_skill(skill))

        types = {item["type"] for item in client.get(f"{API}/capabilities/types").json()["items"]}
        assert "skill" in types


class TestDescriptionsArePlainText:
    """能力描述应能直接显示在页面上，不需要 Markdown 渲染。"""

    def test_no_markdown_emphasis_in_capability_descriptions(self, client: TestClient) -> None:
        items = client.get(f"{API}/capabilities").json()["items"]

        offenders = [
            item["id"]
            for item in items
            if "**" in item["description"] or "**" in json.dumps(item["input_schema"])
        ]
        assert offenders == [], f"这些能力的描述里有 Markdown 星号：{offenders}"

    def test_app_tool_description_is_plain(self) -> None:
        from pathlib import Path

        from app.builder.staging import AppStore
        from app.capabilities.app_tools import build_propose_app_change

        definition = build_propose_app_change(AppStore(root=Path("/tmp")))

        assert "**" not in definition.description
        assert "**" not in json.dumps(definition.parameters, ensure_ascii=False)

    def test_no_markdown_in_impact_text(self, client: TestClient) -> None:
        """影响说明会原样显示在审批卡片上，同样不能用 Markdown。"""
        from pathlib import Path

        from app.builder.staging import AppStore
        from app.capabilities.app_tools import build_propose_app_change
        from app.capabilities.tools import build_create_task

        for definition in (
            build_create_task(None),  # type: ignore[arg-type]
            build_propose_app_change(AppStore(root=Path("/tmp"))),
        ):
            assert "**" not in definition.impact, definition.name


class TestRiskVisibility:
    """界面需要告诉用户"这个能力会不会要求确认"。

    风险级别不在 Manifest 里（Manifest 是描述，不参与决策），
    但工具本身声明了 risk。这里确认内建写工具确实是 WRITE——
    界面据此显示「需要确认」，而不是把写操作画成只读。
    """

    def test_builtin_write_tool_is_marked_write(self) -> None:
        from app.capabilities import RiskLevel
        from app.capabilities.tools import build_create_task

        assert build_create_task(None).risk is RiskLevel.WRITE  # type: ignore[arg-type]

    def test_builtin_read_tools_are_read(self) -> None:
        from app.capabilities import RiskLevel
        from app.capabilities.tools import build_list_tasks, build_search_memories

        assert build_list_tasks(None).risk is RiskLevel.READ  # type: ignore[arg-type]
        assert build_search_memories(None).risk is RiskLevel.READ  # type: ignore[arg-type]
