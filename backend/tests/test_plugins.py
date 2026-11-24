"""声明式插件测试（M8 第 6–7 项，本轮只覆盖 declarative）。

核心安全断言：
1. 插件不含可执行代码，清单里没有任何能执行的东西。
2. 组合工具的风险 = 所组合工具的最高风险；含写操作就需要审批。
3. **批准外层不等于批准内层**：每个内部写操作仍各自经过 Broker。
4. 插件不能通过重名冒充内建工具。
5. 安装时的文件操作防路径逃逸。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.capabilities import RiskLevel, ToolContext, ToolDefinition, ToolRegistry
from app.capabilities.broker import ToolBroker
from app.plugins.declarative import (
    build_composed_tool,
    manifest_for_plugin_tool,
    register_plugin_tools,
    reset_current_broker,
    set_current_broker,
)
from app.plugins.manifest import (
    PLUGIN_MANIFEST_FILENAME,
    PluginManifest,
    PluginManifestError,
    PluginType,
    load_manifest,
    parse_manifest,
)
from app.plugins.registry import PluginError, PluginRegistry

CTX = ToolContext(workspace_id="default", conversation_id="c" * 32)


def _tool_data(**overrides):  # type: ignore[no-untyped-def]
    data = {
        "name": "make_todo",
        "description": "把文本拆成待办",
        "input_schema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        "composes": ["builtin.create_task"],
    }
    data.update(overrides)
    return data


def _manifest_data(**overrides):  # type: ignore[no-untyped-def]
    data = {
        "id": "com.example.notes",
        "name": "笔记助手",
        "version": "1.0.0",
        "type": "declarative",
        "description": "把笔记整理成待办",
        "permissions": ["task:write"],
        "tools": [_tool_data()],
    }
    data.update(overrides)
    return data


def write_plugin(root: Path, directory: str, data: dict | None = None) -> Path:  # type: ignore[type-arg]
    path = root / directory
    path.mkdir(parents=True, exist_ok=True)
    (path / PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(data if data is not None else _manifest_data(), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


class TestManifestValidation:
    def test_valid_declarative_manifest(self) -> None:
        manifest = parse_manifest(_manifest_data())

        assert manifest.id == "com.example.notes"
        assert manifest.type is PluginType.DECLARATIVE
        assert len(manifest.tools) == 1

    def test_manifest_has_no_executable_content(self) -> None:
        """核心断言：本期插件清单里不可能有可执行字段。"""
        manifest = parse_manifest(_manifest_data())

        assert not hasattr(manifest, "entrypoint")
        assert not hasattr(manifest, "handler")
        tool = manifest.tools[0]
        assert not hasattr(tool, "entrypoint")

    @pytest.mark.parametrize(
        "bad_id", ["", "notes", "Com.Example", "com..notes", "com.example.notes.x.y.z.w"]
    )
    def test_invalid_id(self, bad_id: str) -> None:
        with pytest.raises(PluginManifestError, match="插件 id"):
            parse_manifest(_manifest_data(id=bad_id))

    @pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", ""])
    def test_invalid_version(self, bad_version: str) -> None:
        with pytest.raises(PluginManifestError, match="version"):
            parse_manifest(_manifest_data(version=bad_version))

    def test_process_type_rejected_with_explanation(self) -> None:
        """不静默忽略：用户必须知道自己的进程插件没有被支持。"""
        with pytest.raises(PluginManifestError, match="只支持 declarative"):
            parse_manifest(_manifest_data(type="process"))

    def test_entrypoint_rejected(self) -> None:
        with pytest.raises(PluginManifestError, match="entrypoint"):
            parse_manifest(_manifest_data(entrypoint="run.sh"))

    def test_limits_rejected(self) -> None:
        with pytest.raises(PluginManifestError, match="entrypoint / limits"):
            parse_manifest(_manifest_data(limits={"memory_mb": 256}))

    def test_unknown_field_rejected(self) -> None:
        with pytest.raises(PluginManifestError, match="未知字段"):
            parse_manifest(_manifest_data(permisions=[]))

    def test_missing_required_field(self) -> None:
        data = _manifest_data()
        del data["type"]
        with pytest.raises(PluginManifestError, match="缺少必需字段"):
            parse_manifest(data)

    def test_tools_required(self) -> None:
        with pytest.raises(PluginManifestError, match="至少要声明一个工具"):
            parse_manifest(_manifest_data(tools=[]))

    def test_composes_required(self) -> None:
        with pytest.raises(PluginManifestError, match="必须声明 composes"):
            parse_manifest(_manifest_data(tools=[_tool_data(composes=[])]))

    @pytest.mark.parametrize(
        "bad_compose",
        [
            "create_task",  # 没有命名空间
            "mcp.fake.echo",  # 引用 MCP 工具
            "plugin.other.tool",  # 引用其他插件
            "builtin.Create_Task",  # 大写
            "builtin.",  # 空名
            "../etc/passwd",
        ],
    )
    def test_composes_must_reference_builtin_tools(self, bad_compose: str) -> None:
        """只允许组合内建工具：插件不能借 composes 形成隐式授权链。"""
        with pytest.raises(PluginManifestError, match="只能引用内建工具"):
            parse_manifest(_manifest_data(tools=[_tool_data(composes=[bad_compose])]))

    def test_duplicate_tool_names_rejected(self) -> None:
        with pytest.raises(PluginManifestError, match="重复"):
            parse_manifest(_manifest_data(tools=[_tool_data(), _tool_data()]))

    def test_too_many_tools(self) -> None:
        tools = [_tool_data(name=f"t{i}") for i in range(25)]
        with pytest.raises(PluginManifestError, match="最多声明"):
            parse_manifest(_manifest_data(tools=tools))

    def test_deeply_nested_schema_rejected(self) -> None:
        """没有深度限制的 Schema 是廉价的 DoS 入口。"""
        deep: dict = {"type": "object"}
        cursor = deep
        for _ in range(20):
            cursor["properties"] = {"x": {"type": "object"}}
            cursor = cursor["properties"]["x"]

        with pytest.raises(PluginManifestError, match="嵌套过深"):
            parse_manifest(_manifest_data(tools=[_tool_data(input_schema=deep)]))

    def test_self_dependency_rejected(self) -> None:
        with pytest.raises(PluginManifestError, match="不能依赖自身"):
            parse_manifest(_manifest_data(dependencies=["com.example.notes"]))

    def test_load_from_disk(self, tmp_path: Path) -> None:
        path = write_plugin(tmp_path, "com.example.notes")

        manifest = load_manifest(path)

        assert manifest.id == "com.example.notes"

    def test_missing_manifest_file(self, tmp_path: Path) -> None:
        (tmp_path / "empty").mkdir()

        with pytest.raises(PluginManifestError, match="缺少 plugin.json"):
            load_manifest(tmp_path / "empty")

    def test_invalid_json_reports_position(self, tmp_path: Path) -> None:
        path = tmp_path / "p"
        path.mkdir()
        (path / PLUGIN_MANIFEST_FILENAME).write_text("{invalid", encoding="utf-8")

        with pytest.raises(PluginManifestError, match="第 1 行"):
            load_manifest(path)


def _builtin_tool(name: str, risk: RiskLevel, handler=None):  # type: ignore[no-untyped-def]
    return ToolDefinition(
        name=name,
        description=f"{name} 工具",
        parameters={
            "type": "object",
            "properties": {"title": {"type": "string"}, "text": {"type": "string"}},
        },
        risk=risk,
        handler=handler or (lambda args, ctx: {"ok": True, "title": args.get("title")}),
    )


class TestCompositionRisk:
    def _manifest(self, composes: list[str]) -> PluginManifest:
        return parse_manifest(
            _manifest_data(
                tools=[
                    _tool_data(composes=composes, input_schema={"type": "object", "properties": {}})
                ]
            )
        )

    def test_read_only_composition_is_read(self) -> None:
        registry = ToolRegistry()
        registry.register(_builtin_tool("list_tasks", RiskLevel.READ))
        manifest = self._manifest(["builtin.list_tasks"])

        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)

        assert definition.risk is RiskLevel.READ

    def test_any_write_makes_composition_write(self) -> None:
        """只要组合里有写操作，整个工具就是 WRITE。"""
        registry = ToolRegistry()
        registry.register(_builtin_tool("list_tasks", RiskLevel.READ))
        registry.register(_builtin_tool("create_task", RiskLevel.WRITE))
        manifest = self._manifest(["builtin.list_tasks", "builtin.create_task"])

        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)

        assert definition.risk is RiskLevel.WRITE


class TestCompositionExecution:
    @pytest.mark.asyncio
    async def test_inner_calls_go_through_broker(self) -> None:
        """核心：内部每个调用都必须经 Broker，而不是直接调 handler。"""
        executed: list[str] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            executed.append("ran")
            return {"ok": True}

        registry = ToolRegistry()
        registry.register(_builtin_tool("create_task", RiskLevel.WRITE, handler))
        manifest = parse_manifest(_manifest_data())
        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)

        # 无审批通道的 Broker -> 内部写操作必被拒绝
        inner = ToolBroker(registry)
        token = set_current_broker(inner)
        try:
            result = await definition.handler({"title": "测试"}, CTX)
        finally:
            reset_current_broker(token)

        # 内部写操作因缺少审批通道被拒绝，且 handler 从未执行。
        assert result["status"] == "denied"
        assert executed == []

    @pytest.mark.asyncio
    async def test_approving_outer_does_not_approve_inner(self, session) -> None:
        """批准外层插件工具后，内部写操作仍各自要求审批。

        这是与"批一次执行多个写操作"最本质的区别：用户批准的是
        "调用这个插件工具"，而不是"静默执行它内部的所有写操作"。
        """
        from sqlalchemy.orm import sessionmaker

        from app.policy import approval as am

        approvals_seen: list[str] = []
        factory = sessionmaker(bind=session.get_bind())

        registry = ToolRegistry()
        registry.register(_builtin_tool("create_task", RiskLevel.WRITE))
        manifest = parse_manifest(_manifest_data())

        async def inner_resolver(request):  # type: ignore[no-untyped-def]
            approvals_seen.append(request.tool_name)
            am.decide(session, request.approval_id, approved=True, principal_id="local-user")
            return True

        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)
        registry.register(definition)

        outer_approvals: list[str] = []

        async def outer_resolver(request):  # type: ignore[no-untyped-def]
            outer_approvals.append(request.tool_name)
            am.decide(session, request.approval_id, approved=True, principal_id="local-user")
            return True

        inner_broker = ToolBroker(
            registry, session_factory=factory, approval_resolver=inner_resolver
        )
        token = set_current_broker(inner_broker)
        try:
            broker = ToolBroker(registry, session_factory=factory, approval_resolver=outer_resolver)
            result = await broker.execute("plugin__make_todo", '{"title": "任务"}', CTX)
        finally:
            reset_current_broker(token)

        assert result.ok
        # 两次审批：外层插件工具 + 内层 create_task。
        assert outer_approvals == ["plugin__make_todo"]
        assert approvals_seen == ["create_task"]

    @pytest.mark.asyncio
    async def test_stops_after_failed_step(self) -> None:
        """某一步被拒绝后不再继续：避免产生用户没预期的部分结果。"""
        from app.capabilities import ToolExecutionError

        registry = ToolRegistry()

        def failing(args, ctx):  # type: ignore[no-untyped-def]
            raise ToolExecutionError("失败了")

        registry.register(_builtin_tool("create_task", RiskLevel.READ, failing))
        registry.register(_builtin_tool("list_tasks", RiskLevel.READ))
        manifest = parse_manifest(
            _manifest_data(
                tools=[
                    _tool_data(
                        composes=["builtin.create_task", "builtin.list_tasks"],
                        input_schema={"type": "object", "properties": {}},
                    )
                ]
            )
        )

        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)
        token = set_current_broker(ToolBroker(registry))
        try:
            result = await definition.handler({}, CTX)
        finally:
            reset_current_broker(token)

        assert result["status"] == "error"
        assert len(result["steps"]) == 1, "失败后不应继续执行后续步骤"

    @pytest.mark.asyncio
    async def test_arguments_filtered_per_inner_tool(self) -> None:
        """外层参数只把内部工具声明过的字段传下去。"""
        received: list[dict] = []

        def handler(args, ctx):  # type: ignore[no-untyped-def]
            received.append(args)
            return {"ok": True}

        registry = ToolRegistry()
        registry.register(_builtin_tool("create_task", RiskLevel.READ, handler))
        manifest = parse_manifest(
            _manifest_data(
                tools=[
                    _tool_data(
                        input_schema={
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "extra": {"type": "string"},
                            },
                        }
                    )
                ]
            )
        )

        definition = build_composed_tool(manifest, manifest.tools[0], registry=registry)
        token = set_current_broker(ToolBroker(registry))
        try:
            await definition.handler({"title": "a", "extra": "b"}, CTX)
        finally:
            reset_current_broker(token)

        assert received == [{"title": "a"}], "未知字段不应传给内部工具"

    def test_missing_dependency_skipped(self) -> None:
        """composes 引用不存在的内建工具时，跳过该工具并报告。"""
        registry = ToolRegistry()
        manifest = parse_manifest(
            _manifest_data(tools=[_tool_data(composes=["builtin.nonexistent"])])
        )

        result = register_plugin_tools(manifest, registry=registry)

        assert result.registered == ()
        assert "不存在" in result.skipped[0][1]


class TestNameCollision:
    def test_plugin_cannot_shadow_builtin_tool(self) -> None:
        """插件不能通过重名冒充内建工具。"""
        registry = ToolRegistry()
        registry.register(_builtin_tool("create_task", RiskLevel.WRITE))
        # 插件的工具名恰好也叫 plugin__create_task？这里验证的是
        # 即使插件试图占用已存在的工具名也会被拒绝。
        registry.register(
            ToolDefinition(
                name="plugin__make_todo",
                description="已被占用",
                parameters={"type": "object", "properties": {}},
                risk=RiskLevel.READ,
                handler=lambda a, c: {},
            )
        )
        manifest = parse_manifest(_manifest_data())

        result = register_plugin_tools(manifest, registry=registry)

        assert result.registered == ()
        assert "已被占用" in result.skipped[0][1]


class TestCapabilityManifest:
    def test_manifest_lists_composed_tools(self) -> None:
        manifest = parse_manifest(_manifest_data())

        capability = manifest_for_plugin_tool(manifest, manifest.tools[0])

        assert capability.id == "plugin.com.example.notes.make_todo"
        assert capability.type.value == "plugin"
        assert capability.permissions == ("composes:builtin.create_task",)


class TestRegistryLifecycle:
    def test_install_list_enable_uninstall(self, tmp_path: Path) -> None:
        source = write_plugin(tmp_path / "src", "com.example.notes")
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()

        installed = registry.install_from_directory(source)

        assert installed.id == "com.example.notes"
        assert installed.enabled is False, "安装后默认不启用"
        assert [p.id for p in registry.list()] == ["com.example.notes"]

        enabled = registry.set_enabled("com.example.notes", True)
        assert enabled.enabled is True

        message = registry.uninstall("com.example.notes")
        assert "保留" in message
        # 目录被保留（用户决定的策略），但改名为不可扫描的形式，
        # 否则保留的目录会被当作"已安装但未启用"再次列出，与"已卸载"矛盾。
        assert (tmp_path / "plugins" / ".com.example.notes.uninstalled").is_dir()
        assert not (tmp_path / "plugins" / "com.example.notes").exists()
        assert [p for p in registry.list()] == []

    def test_state_persists_across_reload(self, tmp_path: Path) -> None:
        source = write_plugin(tmp_path / "src", "com.example.notes")
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()
        registry.install_from_directory(source, enabled=True)

        reloaded = PluginRegistry(root=tmp_path / "plugins")
        reloaded.load()

        assert reloaded.get("com.example.notes").enabled is True  # type: ignore[union-attr]

    def test_double_install_rejected(self, tmp_path: Path) -> None:
        source = write_plugin(tmp_path / "src", "com.example.notes")
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()
        registry.install_from_directory(source)

        with pytest.raises(PluginError, match="已安装"):
            registry.install_from_directory(source)

    def test_symlink_in_source_rejected(self, tmp_path: Path) -> None:
        """符号链接可能指向数据目录之外（例如 ~/.ssh）。"""
        source = write_plugin(tmp_path / "src", "com.example.notes")
        (source / "link").symlink_to(Path.home())
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()

        with pytest.raises(PluginError, match="符号链接"):
            registry.install_from_directory(source)

    def test_broken_manifest_cannot_be_enabled(self, tmp_path: Path) -> None:
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()
        path = registry.root / "com.example.broken"
        path.mkdir(parents=True)
        (path / PLUGIN_MANIFEST_FILENAME).write_text("{}", encoding="utf-8")

        with pytest.raises(PluginError, match="清单有问题"):
            registry.set_enabled("com.example.broken", True)

    def test_broken_manifest_is_never_enabled(self, tmp_path: Path) -> None:
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()
        path = registry.root / "com.example.broken"
        path.mkdir(parents=True)
        (path / PLUGIN_MANIFEST_FILENAME).write_text("{bad", encoding="utf-8")

        plugins = registry.list()

        assert len(plugins) == 1
        assert not plugins[0].ok
        assert plugins[0].enabled is False

    def test_corrupted_state_file_means_nothing_enabled(self, tmp_path: Path) -> None:
        """状态文件损坏时保守处理：全部视为未启用。"""
        source = write_plugin(tmp_path / "src", "com.example.notes")
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()
        registry.install_from_directory(source, enabled=True)
        (tmp_path / "plugins" / "state.json").write_text("not json", encoding="utf-8")

        reloaded = PluginRegistry(root=tmp_path / "plugins")
        reloaded.load()

        assert reloaded.get("com.example.notes").enabled is False  # type: ignore[union-attr]

    def test_uninstall_missing_plugin(self, tmp_path: Path) -> None:
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()

        with pytest.raises(PluginError, match="不存在"):
            registry.uninstall("com.example.nope")

    def test_install_requires_directory(self, tmp_path: Path) -> None:
        registry = PluginRegistry(root=tmp_path / "plugins")
        registry.load()

        with pytest.raises(PluginError, match="必须是一个目录"):
            registry.install_from_directory(tmp_path / "nope")

    def test_install_rejects_oversized_directory(self, tmp_path: Path) -> None:
        from app.plugins import registry as registry_module

        source = write_plugin(tmp_path / "src", "com.example.big")
        (source / "blob.bin").write_bytes(b"x" * 1024)
        monkeypatch_limit = registry_module.MAX_PLUGIN_DIR_BYTES
        registry_module.MAX_PLUGIN_DIR_BYTES = 512
        try:
            registry = PluginRegistry(root=tmp_path / "plugins")
            registry.load()
            with pytest.raises(PluginError, match="过大"):
                registry.install_from_directory(source)
        finally:
            registry_module.MAX_PLUGIN_DIR_BYTES = monkeypatch_limit


class TestPluginApi:
    """API 层：安装、启用、禁用、卸载，以及路径限制。"""

    def _write_import(self, settings, name: str = "notes-plugin") -> None:
        import_root = settings.data_dir / "plugin-import" / name
        import_root.mkdir(parents=True, exist_ok=True)
        import_root.joinpath(PLUGIN_MANIFEST_FILENAME).write_text(
            json.dumps(_manifest_data(), ensure_ascii=False), encoding="utf-8"
        )

    def test_empty_list(self, client) -> None:
        body = client.get("/api/v1/plugins").json()

        assert body["total"] == 0
        assert "声明式" in body["notice"]

    def test_preview_shows_risks(self, client, settings) -> None:
        self._write_import(settings)

        body = client.get("/api/v1/plugins/preview", params={"directory": "notes-plugin"}).json()

        assert body["id"] == "com.example.notes"
        assert body["tools"][0]["risk_hint"] == "需要确认"
        assert any("声明了权限" in w for w in body["warnings"])
        assert any("写操作" in w for w in body["warnings"])

    def test_install_preview_install_enable_disable_uninstall(self, client, settings) -> None:
        self._write_import(settings)

        installed = client.post(
            "/api/v1/plugins/install", json={"directory": "notes-plugin"}
        ).json()
        assert installed["id"] == "com.example.notes"
        assert installed["enabled"] is False

        enabled = client.post("/api/v1/plugins/com.example.notes/enable").json()
        assert enabled["enabled"] is True

        disabled = client.post("/api/v1/plugins/com.example.notes/disable").json()
        assert disabled["enabled"] is False

        result = client.delete("/api/v1/plugins/com.example.notes").json()
        assert "保留" in result["message"]
        assert client.get("/api/v1/plugins").json()["total"] == 0

    def test_enabled_plugin_registers_tool(self, client, settings) -> None:
        self._write_import(settings)
        client.post("/api/v1/plugins/install", json={"directory": "notes-plugin"})

        client.post("/api/v1/plugins/com.example.notes/enable")

        capabilities = client.get("/api/v1/capabilities", params={"type": "plugin"}).json()
        assert capabilities["total"] == 1
        assert capabilities["items"][0]["id"] == "plugin.com.example.notes.make_todo"

    def test_disable_removes_tool(self, client, settings) -> None:
        self._write_import(settings)
        client.post("/api/v1/plugins/install", json={"directory": "notes-plugin"})
        client.post("/api/v1/plugins/com.example.notes/enable")

        client.post("/api/v1/plugins/com.example.notes/disable")

        capabilities = client.get("/api/v1/capabilities", params={"type": "plugin"}).json()
        assert capabilities["total"] == 0

    @pytest.mark.parametrize("bad", ["../etc", "/etc/passwd", ".hidden", "a/b", ".."])
    def test_path_escape_rejected(self, client, settings, bad: str) -> None:
        """一个 HTTP 请求不能把机器上任意目录"装"进系统。"""
        response = client.get("/api/v1/plugins/preview", params={"directory": bad})

        assert response.status_code == 422

    def test_missing_source_404(self, client) -> None:
        response = client.get("/api/v1/plugins/preview", params={"directory": "nope"})

        assert response.status_code == 404
        assert "导入目录" in response.json()["error"]["message"]

    def test_invalid_manifest_reported(self, client, settings) -> None:
        target = settings.data_dir / "plugin-import" / "broken"
        target.mkdir(parents=True)
        (target / PLUGIN_MANIFEST_FILENAME).write_text('{"id": "bad"}', encoding="utf-8")

        response = client.get("/api/v1/plugins/preview", params={"directory": "broken"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PLUGIN_MANIFEST_INVALID"

    def test_enable_missing_plugin_404(self, client) -> None:
        assert client.post("/api/v1/plugins/com.example.nope/enable").status_code == 404


class TestPluginChatIntegration:
    """端到端：启用后的插件工具必须出现在模型的工具清单里。"""

    def test_plugin_tool_reaches_model(self, settings, db_path) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app

        import_root = settings.data_dir / "plugin-import" / "notes-plugin"
        import_root.mkdir(parents=True, exist_ok=True)
        (import_root / PLUGIN_MANIFEST_FILENAME).write_text(
            json.dumps(_manifest_data(), ensure_ascii=False), encoding="utf-8"
        )

        app = create_app(settings)
        with TestClient(app) as client:
            client.post("/api/v1/plugins/install", json={"directory": "notes-plugin"})
            client.post("/api/v1/plugins/com.example.notes/enable")

            names = [tool.name for tool in app.state.tool_registry.all()]

        assert "plugin__make_todo" in names
