"""Capability Manifest 与 Registry 测试（M8 第 1–2 项）。

重点覆盖：
1. Manifest 字段完整性（§7.2 清单）与不可信输入的严格校验。
2. **声明不等于授权**：Manifest 不携带 handler，也不参与任何授权判断。
3. 版本比较按数字而非字符串（1.10.0 > 1.9.0）。
4. 依赖缺失与依赖环必须被显式报告。
5. 只读 API 不泄漏任何运行时内容。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.capabilities.manifest import (
    CapabilityManifest,
    CapabilityRuntime,
    CapabilitySource,
    CapabilityType,
    ManifestError,
)
from app.capabilities.registry import (
    CapabilityRegistry,
    CapabilityRegistryError,
    manifest_from_tool,
    version_key,
)

API = "/api/v1"


def _manifest(**overrides):  # type: ignore[no-untyped-def]
    data = {
        "id": "builtin.demo",
        "name": "示例能力",
        "version": "1.0.0",
        "type": CapabilityType.TOOL,
    }
    data.update(overrides)
    return CapabilityManifest(**data)


class TestManifestValidation:
    def test_full_manifest_round_trips(self) -> None:
        manifest = _manifest(
            description="说明",
            input_schema={"type": "object", "properties": {}},
            output_schema={"type": "object"},
            runtime=CapabilityRuntime.MCP,
            permissions=("memory:read",),
            dependencies=("builtin.other",),
            compatibility=">=0.2.0",
            source=CapabilitySource.COMMUNITY,
            signature="sha256:abc",
        )

        rebuilt = CapabilityManifest.from_dict(manifest.to_dict())

        assert rebuilt == manifest

    def test_manifest_has_every_field_from_spec(self) -> None:
        """§7.2 清单的每个字段都必须在导出结构里出现。"""
        required = {
            "id",
            "name",
            "version",
            "type",
            "description",
            "input_schema",
            "output_schema",
            "runtime",
            "permissions",
            "dependencies",
            "compatibility",
            "source",
            "signature",
        }

        assert set(_manifest().to_dict()) == required

    @pytest.mark.parametrize("bad_id", ["", "NoNamespace", "Upper.Case", "a.b.c d", ".leading"])
    def test_invalid_id_rejected(self, bad_id: str) -> None:
        with pytest.raises(ManifestError):
            _manifest(id=bad_id)

    @pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "1.0.0.0", "", "latest"])
    def test_invalid_version_rejected(self, bad_version: str) -> None:
        with pytest.raises(ManifestError):
            _manifest(version=bad_version)

    def test_prerelease_version_accepted(self) -> None:
        assert _manifest(version="1.0.0-rc1").version == "1.0.0-rc1"

    def test_name_cannot_be_empty(self) -> None:
        with pytest.raises(ManifestError):
            _manifest(name="   ")

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(ManifestError):
            _manifest(dependencies=("builtin.demo",))

    def test_string_enums_are_normalized(self) -> None:
        """允许传字符串（安装包/JSON 的形态），但对象内必须是枚举。"""
        manifest = _manifest(type="tool", runtime="builtin", source="builtin")

        assert manifest.type is CapabilityType.TOOL
        assert manifest.runtime is CapabilityRuntime.BUILTIN
        assert manifest.source is CapabilitySource.BUILTIN

    def test_unknown_enum_value_rejected(self) -> None:
        with pytest.raises(ManifestError):
            _manifest(type="telepathy")


class TestManifestFromDict:
    """Matifest 来自安装包/社区下载 —— 属于不可信输入（§8.4）。"""

    def test_unknown_field_rejected(self) -> None:
        """静默忽略拼错的字段，会让用户以为设置生效了。"""
        with pytest.raises(ManifestError, match="未知字段"):
            CapabilityManifest.from_dict(
                {"id": "a.b", "name": "n", "version": "1.0.0", "type": "tool", "permisions": []}
            )

    def test_missing_type_rejected(self) -> None:
        with pytest.raises(ManifestError, match="缺少必需字段"):
            CapabilityManifest.from_dict({"id": "a.b", "name": "n", "version": "1.0.0"})

    def test_non_object_rejected(self) -> None:
        with pytest.raises(ManifestError):
            CapabilityManifest.from_dict([])  # type: ignore[arg-type]

    def test_permissions_must_be_string_list(self) -> None:
        with pytest.raises(ManifestError, match="字符串数组"):
            CapabilityManifest.from_dict(
                {
                    "id": "a.b",
                    "name": "n",
                    "version": "1.0.0",
                    "type": "tool",
                    "permissions": [1, 2],
                }
            )

    def test_schema_must_be_object(self) -> None:
        with pytest.raises(ManifestError, match="必须是对象"):
            CapabilityManifest.from_dict(
                {
                    "id": "a.b",
                    "name": "n",
                    "version": "1.0.0",
                    "type": "tool",
                    "input_schema": "not-an-object",
                }
            )


class TestNoExecutableContent:
    """核心安全断言：Manifest 只是描述，不能承载可执行内容。"""

    def test_manifest_has_no_handler_field(self) -> None:
        manifest = _manifest()

        assert not hasattr(manifest, "handler")
        assert "handler" not in manifest.to_dict()

    def test_manifest_from_tool_drops_handler(self) -> None:
        from app.capabilities.tools import build_create_task

        definition = build_create_task(None)  # type: ignore[arg-type]
        manifest = manifest_from_tool(definition)

        assert "handler" not in manifest.to_dict()
        assert "handler" not in repr(manifest)
        assert manifest.id == "builtin.create_task"
        assert manifest.name == "创建任务"

    def test_permissions_are_declared_not_granted(self) -> None:
        """Manifest 上的权限字串不参与 Policy 决策。

        这条测试锁定的是**设计边界**：如果将来有人让 Policy 读取
        manifest.permissions 直接放行，这里会提醒他先改规范。
        """
        from app.capabilities import RiskLevel, ToolDefinition
        from app.policy import Decision, PolicyRequest, evaluate

        definition = ToolDefinition(
            name="danger",
            description="写操作",
            parameters={"type": "object", "properties": {}},
            risk=RiskLevel.WRITE,
            handler=lambda args, ctx: {},
        )
        # 即使某能力的 Manifest 声明了 write 权限……
        manifest_from_tool(definition)
        # ……Policy 仍然要求审批（默认拒绝原则）。
        decision = evaluate(PolicyRequest("u", "default", definition, {}))

        assert decision.decision is Decision.REQUIRE_APPROVAL


class TestVersionOrdering:
    def test_numeric_not_lexicographic(self) -> None:
        assert version_key("1.10.0") > version_key("1.9.0")

    def test_prerelease_sorts_before_release(self) -> None:
        assert version_key("1.0.0-rc1") < version_key("1.0.0")

    def test_registry_returns_highest_version(self) -> None:
        registry = CapabilityRegistry()
        for version in ("1.9.0", "1.10.0", "1.2.0"):
            registry.register(_manifest(version=version))

        assert registry.get("builtin.demo").version == "1.10.0"  # type: ignore[union-attr]

    def test_versions_listed_ascending(self) -> None:
        registry = CapabilityRegistry()
        for version in ("1.10.0", "1.2.0"):
            registry.register(_manifest(version=version))

        assert [m.version for m in registry.versions("builtin.demo")] == ["1.2.0", "1.10.0"]


class TestRegistryRegistration:
    def test_duplicate_version_rejected(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest())

        with pytest.raises(CapabilityRegistryError):
            registry.register(_manifest())

    def test_replace_allows_overwrite(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(name="旧名"))
        registry.register(_manifest(name="新名"), replace=True)

        assert registry.get("builtin.demo").name == "新名"  # type: ignore[union-attr]

    def test_len_counts_ids_not_versions(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(version="1.0.0"))
        registry.register(_manifest(version="1.1.0"))

        assert len(registry) == 1

    def test_unregister_removes_all_versions(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(version="1.0.0"))
        registry.register(_manifest(version="1.1.0"))

        assert registry.unregister("builtin.demo") == 2
        assert registry.get("builtin.demo") is None

    def test_unregister_blocks_when_depended_on(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="base.lib", version="1.0.0"))
        registry.register(_manifest(id="app.top", version="1.0.0", dependencies=("base.lib",)))

        with pytest.raises(CapabilityRegistryError, match="依赖"):
            registry.unregister("base.lib")

    def test_unknown_capability_unregister_is_noop(self) -> None:
        assert CapabilityRegistry().unregister("nope.none") == 0


class TestDependencies:
    def test_missing_dependency_reported(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="app.top", dependencies=("base.lib", "base.util")))

        assert registry.missing_dependencies() == {"app.top": ("base.lib", "base.util")}

    def test_registration_order_does_not_matter(self) -> None:
        """先装本体后装依赖也要能用：登记时不校验依赖是否存在。"""
        registry = CapabilityRegistry()
        registry.register(_manifest(id="app.top", dependencies=("base.lib",)))
        registry.register(_manifest(id="base.lib"))

        assert registry.missing_dependencies() == {}

    def test_cycle_detected(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="a.one", dependencies=("b.two",)))
        registry.register(_manifest(id="b.two", dependencies=("a.one",)))

        cycle = registry.check_dependency_cycle()

        assert cycle
        assert "a.one" in cycle and "b.two" in cycle

    def test_no_cycle_for_diamond(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="d.base"))
        registry.register(_manifest(id="b.left", dependencies=("d.base",)))
        registry.register(_manifest(id="c.right", dependencies=("d.base",)))
        registry.register(_manifest(id="a.top", dependencies=("b.left", "c.right")))

        assert registry.check_dependency_cycle() == ()


class TestSearch:
    def test_search_matches_id_and_name_only(self) -> None:
        registry = CapabilityRegistry()
        registry.register(
            _manifest(id="builtin.alpha", name="甲", description="包含 keyword 的描述")
        )
        registry.register(_manifest(id="builtin.beta", name="关键词乙"))

        # 描述不参与匹配：自然语言模糊匹配会让检索变成噪声。
        assert [m.id for m in registry.search(query="keyword").items] == []
        assert [m.id for m in registry.search(query="关键词").items] == ["builtin.beta"]

    def test_search_is_case_insensitive(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="builtin.alpha"))

        assert registry.search(query="ALPHA").total == 1

    def test_filter_by_type_and_source(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(id="builtin.a", type=CapabilityType.TOOL))
        registry.register(_manifest(id="mcp.b", type=CapabilityType.MCP))

        assert [m.id for m in registry.search(type=CapabilityType.MCP).items] == ["mcp.b"]
        assert registry.search(source="community").total == 0

    def test_search_returns_latest_version_only(self) -> None:
        registry = CapabilityRegistry()
        registry.register(_manifest(version="1.0.0"))
        registry.register(_manifest(version="2.0.0"))

        assert registry.search().total == 1


class TestCapabilityApi:
    def test_lists_builtin_tools(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities").json()

        ids = {item["id"] for item in body["items"]}
        assert {"builtin.search_memories", "builtin.list_tasks", "builtin.create_task"} <= ids
        assert body["total"] == len(body["items"])
        # 必须明确告知"声明不是授权"。
        assert "不代表已被授权" in body["notice"]

    def test_response_never_contains_handler(self, client: TestClient) -> None:
        text = client.get(f"{API}/capabilities").text

        assert "handler" not in text
        assert "<function" not in text

    def test_get_single(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities/builtin.create_task").json()

        assert body["name"] == "创建任务"
        assert body["type"] == "tool"

    def test_missing_capability_404(self, client: TestClient) -> None:
        assert client.get(f"{API}/capabilities/nope.none").status_code == 404

    def test_versions_endpoint(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities/builtin.create_task/versions").json()

        assert body["total"] == 1
        assert body["items"][0]["version"] == "1.0.0"

    def test_versions_missing_404(self, client: TestClient) -> None:
        assert client.get(f"{API}/capabilities/nope.none/versions").status_code == 404

    def test_search_query_param(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities", params={"q": "task"}).json()

        assert {item["id"] for item in body["items"]} == {
            "builtin.create_task",
            "builtin.list_tasks",
        }

    def test_filter_by_type(self, client: TestClient) -> None:
        body = client.get(f"{API}/capabilities", params={"type": "tool"}).json()
        assert body["total"] >= 3

        empty = client.get(f"{API}/capabilities", params={"type": "plugin"}).json()
        assert empty["total"] == 0

    def test_invalid_type_422(self, client: TestClient) -> None:
        response = client.get(f"{API}/capabilities", params={"type": "telepathy"})

        assert response.status_code == 422
        assert "未知的能力类型" in response.json()["error"]["message"]

    def test_dependency_health_route_not_shadowed(self, client: TestClient) -> None:
        """/health/deps 不能被 /{capability_id} 吃掉。"""
        body = client.get(f"{API}/capabilities/health/deps").json()

        assert body["healthy"] is True
        assert body["cycle"] == []

    def test_dependency_health_exposes_missing_and_cycle(self, client: TestClient) -> None:
        registry = client.app.state.capability_registry  # type: ignore[attr-defined]
        # 用 2.0.0 避免与内建能力默认的 1.0.0 版本冲突。
        registry.register(_manifest(id="x.one", version="2.0.0", dependencies=("x.two", "x.gone")))
        registry.register(_manifest(id="x.two", version="2.0.0", dependencies=("x.one",)))

        body = client.get(f"{API}/capabilities/health/deps").json()

        assert body["healthy"] is False
        assert body["missing"] == {"x.one": ["x.gone"]}
        assert set(body["cycle"]) >= {"x.one", "x.two"}


class TestManifestFromTool:
    def test_rejects_name_that_cannot_become_id(self) -> None:
        from app.capabilities import RiskLevel, ToolDefinition

        definition = ToolDefinition(
            name="Bad Name",
            description="",
            parameters={},
            risk=RiskLevel.READ,
            handler=lambda a, c: {},
        )

        with pytest.raises(ManifestError):
            manifest_from_tool(definition)

    def test_permissions_reflect_risk_level(self) -> None:
        from app.capabilities.tools import build_create_task, build_list_tasks

        write = manifest_from_tool(build_create_task(None))  # type: ignore[arg-type]
        read = manifest_from_tool(build_list_tasks(None))  # type: ignore[arg-type]

        assert write.permissions == ("tool:create_task:write",)
        assert read.permissions == ("tool:list_tasks:read",)
