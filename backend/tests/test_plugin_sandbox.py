"""进程插件与沙箱测试（M8 第 6b 项）。

**集成测试会真实启动 bubblewrap 沙箱**，用"恶意插件"证明隔离有效：

- 试图读用户主目录 → 被拒绝
- 试图读数据库目录 / ~/.ssh → 不可见
- 试图连接外网 → 被阻断
- 死循环 → 被 RLIMIT_CPU 杀死
- 申请 2GB 内存 → 被 RLIMIT_AS 拒绝
- 试图写插件目录 → 只读

沙箱不可用的环境（CI、非 Linux）会跳过这些测试并打印原因，
但**绝不会**在没有沙箱的情况下"假装通过"。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.plugins.manifest import PluginLimits
from app.plugins.process import ProcessPluginError, run_plugin, sandbox_available
from app.plugins.sandbox import _compat_mounts, build_command, detect_sandbox

SANDBOX_OK, SANDBOX_REASON = sandbox_available()

requires_sandbox = pytest.mark.skipif(not SANDBOX_OK, reason=f"沙箱不可用：{SANDBOX_REASON}")


PROBE_SCRIPT = """
import json, os, socket, sys

payload = json.loads(sys.stdin.read() or "{}")
tool = payload.get("tool", "")
args = payload.get("arguments", {})

def reply(obj):
    print(json.dumps(obj, ensure_ascii=False))

if tool == "list_home":
    try:
        n = len(os.listdir(os.path.expanduser("~")))
        reply({"ok": True, "content": f"READ {n}"})
    except OSError as e:
        reply({"ok": True, "content": f"DENIED {type(e).__name__}"})
elif tool == "read_path":
    target = args.get("path", "")
    if os.path.exists(target):
        reply({"ok": True, "content": "VISIBLE"})
    else:
        reply({"ok": True, "content": "HIDDEN"})
elif tool == "network":
    try:
        socket.create_connection(("1.1.1.1", 443), timeout=2)
        reply({"ok": True, "content": "CONNECTED"})
    except OSError as e:
        reply({"ok": True, "content": f"BLOCKED {type(e).__name__}"})
elif tool == "write_plugin":
    try:
        open("/plugin/evil.txt", "w").write("x")
        reply({"ok": True, "content": "WRITTEN"})
    except OSError:
        reply({"ok": True, "content": "READONLY"})
elif tool == "write_work":
    try:
        open("/work/ok.txt", "w").write("x")
        reply({"ok": True, "content": "WRITABLE"})
    except OSError as e:
        reply({"ok": True, "content": f"FAILED {type(e).__name__}"})
elif tool == "env_secrets":
    marks = ("KEY", "TOKEN", "SECRET", "PASSWORD")
    found = [k for k in os.environ if any(m in k.upper() for m in marks)]
    reply({"ok": True, "content": json.dumps(found)})
elif tool == "spin":
    while True:
        pass
elif tool == "allocate":
        try:
            bytearray(2 * 1024 * 1024 * 1024)
            reply({"ok": True, "content": "ALLOCATED"})
        except MemoryError:
            reply({"ok": True, "content": "DENIED MemoryError"})
elif tool == "bad_output":
    print("this is not json at all")
elif tool == "reported_error":
    reply({"ok": False, "error": "插件自己报告失败"})
else:
    reply({"ok": True, "content": f"echo:{args.get('text','')}"})
"""


@pytest.fixture()
def plugin_dir(tmp_path: Path) -> Path:
    path = tmp_path / "probe-plugin"
    path.mkdir()
    (path / "run.py").write_text(PROBE_SCRIPT, encoding="utf-8")
    return path


@pytest.fixture()
def limits() -> PluginLimits:
    return PluginLimits(memory_mb=256, cpu_seconds=5, timeout_seconds=20, max_processes=32)


async def _call(plugin_dir: Path, tool: str, limits: PluginLimits, **arguments):  # type: ignore[no-untyped-def]
    return await run_plugin(
        plugin_dir=plugin_dir,
        entrypoint="run.py",
        tool_name=tool,
        arguments=arguments,
        limits=limits,
    )


class TestSandboxDetection:
    def test_detection_returns_actionable_reason(self) -> None:
        available, reason = detect_sandbox()

        assert isinstance(available, bool)
        if not available:
            # 不可用时必须给出可操作的原因，而不是空字符串。
            assert reason.strip()
            assert "bubblewrap" in reason or "prlimit" in reason or "bwrap" in reason

    def test_compat_mounts_follow_host_layout(self) -> None:
        """兼容路径按宿主真实布局重建：符号链接就建链接，真实目录就绑定。"""
        mounts = _compat_mounts()

        # /bin 在绝大多数 Linux 上是符号链接；重建结果里应该有它的处理。
        if Path("/bin").exists():
            assert any("/bin" in arg for arg in mounts)

    def test_command_never_uses_shell(self, plugin_dir: Path, limits: PluginLimits) -> None:
        """参数以 argv 列表传递，绝不经过 shell 解释。"""
        command = build_command(
            plugin_dir=plugin_dir,
            entrypoint="run.py",
            arguments={"text": "a; rm -rf /"},
            limits=limits,
        )

        assert isinstance(command.argv, tuple)
        # 恶意内容只出现在环境变量（JSON）里，不作为 shell 命令的一部分。
        assert not any("rm -rf" in arg for arg in command.argv)
        assert json.loads(command.env["PLUGIN_ARGS"])["text"] == "a; rm -rf /"

    def test_command_rejects_entrypoint_escape(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        from app.plugins.sandbox import SandboxUnavailable

        with pytest.raises((SandboxUnavailable, ValueError)):
            build_command(
                plugin_dir=plugin_dir,
                entrypoint="../outside.py",
                arguments={},
                limits=limits,
            )


@requires_sandbox
class TestSandboxIsolation:
    """真实沙箱集成测试。每一条都对应 §8.5 的一项要求。"""

    @pytest.mark.asyncio
    async def test_normal_call_works(self, plugin_dir: Path, limits: PluginLimits) -> None:
        result = await _call(plugin_dir, "echo", limits, text="你好")

        assert not result.is_error
        assert result.text == "echo:你好"

    @pytest.mark.asyncio
    async def test_cannot_read_home_directory(self, plugin_dir: Path, limits: PluginLimits) -> None:
        result = await _call(plugin_dir, "list_home", limits)

        assert result.text.startswith("DENIED"), f"插件读到了用户主目录：{result.text}"

    @pytest.mark.asyncio
    async def test_cannot_see_database_or_ssh(self, plugin_dir: Path, limits: PluginLimits) -> None:
        database = Path.home() / ".local" / "share" / "personal-hub" / "db"
        ssh = Path.home() / ".ssh"

        for target in (database, ssh):
            result = await _call(plugin_dir, "read_path", limits, path=str(target))
            assert result.text == "HIDDEN", f"插件看见了 {target}"

    @pytest.mark.asyncio
    async def test_network_is_blocked(self, plugin_dir: Path, limits: PluginLimits) -> None:
        result = await _call(plugin_dir, "network", limits)

        assert result.text.startswith("BLOCKED"), f"插件能连外网：{result.text}"

    @pytest.mark.asyncio
    async def test_plugin_directory_is_read_only(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        result = await _call(plugin_dir, "write_plugin", limits)

        assert result.text == "READONLY"
        # 宿主侧确认文件确实没有被写出来。
        assert not (plugin_dir / "evil.txt").exists()

    @pytest.mark.asyncio
    async def test_work_directory_is_writable(self, plugin_dir: Path, limits: PluginLimits) -> None:
        result = await _call(plugin_dir, "write_work", limits)

        assert result.text == "WRITABLE"

    @pytest.mark.asyncio
    async def test_environment_has_no_secrets(
        self, plugin_dir: Path, limits: PluginLimits, monkeypatch
    ) -> None:
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-leak")
        monkeypatch.setenv("MY_SECRET_TOKEN", "secret-value")

        result = await _call(plugin_dir, "env_secrets", limits)

        assert json.loads(result.text) == [], f"密钥环境变量泄漏：{result.text}"

    @pytest.mark.asyncio
    async def test_cpu_limit_kills_runaway_process(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        with pytest.raises(ProcessPluginError) as excinfo:
            await _call(plugin_dir, "spin", limits)

        # CPU 限制触发时进程被 SIGKILL（退出码 137）。
        assert excinfo.value.code == "PLUGIN_EXITED"
        assert "资源限制" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_memory_limit_denies_allocation(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        result = await _call(plugin_dir, "allocate", limits)

        assert result.text.startswith("DENIED"), f"插件成功申请了 2GB：{result.text}"


class TestOutputHandling:
    @pytest.mark.asyncio
    async def test_non_json_output_is_rejected(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        """插件输出不可信：非约定 JSON 一律拒绝，不把任意文本交给模型。"""
        result = await _call(plugin_dir, "bad_output", limits)

        assert result.is_error
        assert "不是约定的 JSON" in result.text

    @pytest.mark.asyncio
    async def test_plugin_reported_error_is_surfaced(
        self, plugin_dir: Path, limits: PluginLimits
    ) -> None:
        result = await _call(plugin_dir, "reported_error", limits)

        assert result.is_error
        assert "插件自己报告失败" in result.text


class TestSandboxUnavailableIsRefusal:
    """核心安全断言：沙箱不可用时必须**拒绝执行**，而不是无沙箱运行。"""

    @pytest.mark.asyncio
    async def test_refuses_when_sandbox_missing(
        self, plugin_dir: Path, limits: PluginLimits, monkeypatch
    ) -> None:
        import app.plugins.process as process_module

        monkeypatch.setattr(
            process_module, "detect_sandbox", lambda: (False, "沙箱不可用（测试模拟）")
        )

        with pytest.raises(ProcessPluginError) as excinfo:
            await _call(plugin_dir, "echo", limits, text="x")

        assert excinfo.value.code == "PLUGIN_SANDBOX_UNAVAILABLE"
        assert "沙箱不可用" in str(excinfo.value)

    @pytest.mark.asyncio
    async def test_timeout_terminates_process(
        self, plugin_dir: Path, limits: PluginLimits, monkeypatch
    ) -> None:
        """墙钟超时：一个不占 CPU 但永不返回的插件也必须被终止。"""
        slow = plugin_dir / "slow.py"
        slow.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")

        tiny = PluginLimits(memory_mb=256, cpu_seconds=5, timeout_seconds=2, max_processes=32)
        with pytest.raises(ProcessPluginError) as excinfo:
            await run_plugin(
                plugin_dir=plugin_dir,
                entrypoint="slow.py",
                tool_name="x",
                arguments={},
                limits=tiny,
            )

        assert excinfo.value.code in {"PLUGIN_TIMEOUT", "PLUGIN_EXITED"}


class TestRiskClassification:
    """进程插件一律需要确认——与它声明了什么无关。"""

    def test_process_tool_is_write(self, tmp_path: Path) -> None:
        from app.capabilities import RiskLevel, ToolRegistry
        from app.plugins.declarative import build_process_tool
        from app.plugins.manifest import parse_manifest

        manifest = parse_manifest(
            {
                "id": "com.example.echo",
                "name": "回声",
                "version": "1.0.0",
                "type": "process",
                "entrypoint": "run.py",
                "limits": {"memory_mb": 128},
                "tools": [
                    {"name": "echo", "description": "回显", "input_schema": {"type": "object"}}
                ],
            }
        )
        definition = build_process_tool(manifest, manifest.tools[0], plugin_dir=tmp_path)

        assert definition.risk is RiskLevel.WRITE
        # 注册表里不存在时不应该被发现
        assert ToolRegistry().get("plugin__echo") is None

    def test_preview_labels_process_tool_as_needing_confirmation(self, client, settings) -> None:
        """曾经的缺陷：进程插件没有 composes，被标成"只读"。"""
        import json as json_module

        target = settings.data_dir / "plugin-import" / "proc"
        target.mkdir(parents=True)
        (target / "run.py").write_text("print('{}')", encoding="utf-8")
        (target / "plugin.json").write_text(
            json_module.dumps(
                {
                    "id": "com.example.proc",
                    "name": "进程插件",
                    "version": "1.0.0",
                    "type": "process",
                    "entrypoint": "run.py",
                    "limits": {"memory_mb": 128},
                    "tools": [
                        {"name": "t", "description": "d", "input_schema": {"type": "object"}}
                    ],
                }
            ),
            encoding="utf-8",
        )

        body = client.get("/api/v1/plugins/preview", params={"directory": "proc"}).json()

        assert "执行外部代码" in body["tools"][0]["risk_hint"]
        assert any("沙箱" in w for w in body["warnings"])


class TestManifestProcessRules:
    def test_process_tool_does_not_require_composes(self) -> None:
        """进程插件自带实现，不需要组合任何工具。"""
        from app.plugins.manifest import parse_manifest

        manifest = parse_manifest(
            {
                "id": "com.example.proc",
                "name": "进程",
                "version": "1.0.0",
                "type": "process",
                "entrypoint": "run.py",
                "limits": {"memory_mb": 128},
                "tools": [{"name": "t", "description": "d", "input_schema": {"type": "object"}}],
            }
        )

        assert manifest.tools[0].composes == ()

    def test_process_tool_with_composes_rejected(self) -> None:
        from app.plugins.manifest import PluginManifestError, parse_manifest

        with pytest.raises(PluginManifestError, match="不应声明 composes"):
            parse_manifest(
                {
                    "id": "com.example.proc",
                    "name": "进程",
                    "version": "1.0.0",
                    "type": "process",
                    "entrypoint": "run.py",
                    "limits": {"memory_mb": 128},
                    "tools": [
                        {
                            "name": "t",
                            "description": "d",
                            "composes": ["builtin.list_tasks"],
                            "input_schema": {"type": "object"},
                        }
                    ],
                }
            )
