"""进程插件重启恢复的回归测试（审计 P0-1）。

缺陷现象：`_bootstrap_plugins` 把所有已启用插件都交给 `register_plugin_tools`，
进程插件因此在重启后被当成**声明式**注册：

- 它没有 `composes`，于是 `risk=READ`；
- `READ` 不触发审批，一个"执行外部代码"的工具以只读身份出现在模型工具清单里；
- 执行时才因缺少 handler 而失败——这是偶然兜底，不是安全保证。

本测试固定的是：**重启后工具仍在，且风险级别必须仍是 WRITE**。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.capabilities import RiskLevel
from app.core.config import Settings
from app.main import create_app
from app.plugins.manifest import PLUGIN_MANIFEST_FILENAME

ENTRYPOINT = "main.py"
ECHO_SOURCE = (
    "import json, sys\n"
    "data = json.loads(sys.stdin.readline())\n"
    "text = data.get('arguments', {}).get('text', '')\n"
    "sys.stdout.write(json.dumps({'ok': True, 'content': text}))\n"
)


def _process_manifest() -> dict:
    return {
        "id": "com.example.echo",
        "name": "回声",
        "version": "1.0.0",
        "type": "process",
        "description": "回显输入",
        "entrypoint": ENTRYPOINT,
        "limits": {
            "memory_mb": 128,
            "cpu_seconds": 5,
            "timeout_seconds": 10,
            "max_processes": 32,
        },
        "tools": [
            {
                "name": "echo",
                "description": "回显文本",
                "input_schema": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ],
    }


def _install_process_plugin(settings: Settings, client: TestClient) -> None:
    source = settings.data_dir / "plugin-import" / "echo-plugin"
    source.mkdir(parents=True, exist_ok=True)
    (source / PLUGIN_MANIFEST_FILENAME).write_text(
        json.dumps(_process_manifest(), ensure_ascii=False), encoding="utf-8"
    )
    (source / ENTRYPOINT).write_text(ECHO_SOURCE, encoding="utf-8")

    installed = client.post("/api/v1/plugins/install", json={"directory": "echo-plugin"})
    assert installed.status_code == 200, installed.text
    enabled = client.post("/api/v1/plugins/com.example.echo/enable")
    assert enabled.status_code == 200, enabled.text


class TestProcessPluginRestart:
    def test_process_plugin_survives_restart_as_write(self, settings, db_path) -> None:
        """重启后：工具仍已登记，且 risk 仍是 WRITE（未被降级为 READ）。"""
        app = create_app(settings)
        with TestClient(app) as client:
            _install_process_plugin(settings, client)
            # 运行期登记走 register_process_plugin，应为 WRITE。
            runtime_tool = app.state.tool_registry.get("plugin__echo")
            assert runtime_tool is not None
            assert runtime_tool.risk is RiskLevel.WRITE

        # 模拟重启：同一个数据目录重新创建应用。
        restarted = create_app(settings)
        with TestClient(restarted) as client:
            tool = restarted.state.tool_registry.get("plugin__echo")

            assert tool is not None, "重启后进程插件工具消失了"
            assert tool.risk is RiskLevel.WRITE, "重启后进程插件被降级为 READ，会绕过审批"

            # 有审批通道时，调用必须经过审批；handler 才是真正的沙箱执行器。
            approvals: list[str] = []

            from app.capabilities import ToolContext
            from app.capabilities.broker import ToolBroker
            from app.policy import approval as approval_manager

            async def resolver(request):  # type: ignore[no-untyped-def]
                approvals.append(request.tool_name)
                # 与真实 API 一样：把决定落库，Broker 会再自行校验票据。
                with restarted.state.session_factory() as decision_session:
                    approval_manager.decide(
                        decision_session,
                        request.approval_id,
                        approved=True,
                        principal_id="local-user",
                    )
                return True

            broker = ToolBroker(
                restarted.state.tool_registry,
                session_factory=restarted.state.session_factory,
                approval_resolver=resolver,
            )
            ctx = ToolContext(workspace_id="default", conversation_id="c" * 32)
            result = asyncio.run(broker.execute("plugin__echo", '{"text": "hi"}', ctx))

        assert approvals == ["plugin__echo"], "进程插件在重启后没有触发审批"
        assert result.ok, result.error_code
        assert "hi" in result.content

    def test_startup_reports_process_tool(self, settings, db_path) -> None:
        """启动状态里必须列出该工具，界面据此显示"已启用"。"""
        app = create_app(settings)
        with TestClient(app) as client:
            _install_process_plugin(settings, client)

        restarted = create_app(settings)
        with TestClient(restarted):
            status = restarted.state.plugin_status
        assert "plugin__echo" in status["registered"]
        assert status["skipped"] == []


def test_entrypoint_file_is_not_required_to_exist_for_registration() -> None:
    """清单校验只要求相对路径合法性；文件缺失会在执行时明确失败。

    这条不是安全断言，而是记录当前边界：注册阶段不读插件目录内容，
    真正的沙箱执行由 `process.run_plugin` 负责，失败会返回结构化错误。
    """
    from app.plugins.manifest import parse_manifest

    manifest = parse_manifest(_process_manifest())
    assert manifest.entrypoint == ENTRYPOINT
    assert Path(manifest.entrypoint).suffix == ".py"
