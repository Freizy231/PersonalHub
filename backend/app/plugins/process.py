"""进程插件（M8 第 6b 项，提案第 5 步）。

插件是一个可执行文件，在 **bubblewrap 沙箱**中运行。本模块负责
启动、通信、超时与清理；沙箱命令由 `sandbox.py` 构造。

通信协议：JSON-RPC 风格的单次请求/响应，通过 stdin 传入参数、stdout 返回结果。

    宿主 -> 插件（stdin，一行 JSON）
    {"action": "call", "tool": "do_it", "arguments": {...}}

    插件 -> 宿主（stdout，一行 JSON）
    {"ok": true, "content": "结果文本"}
    或 {"ok": false, "error": "可读的失败原因"}

为什么不用 MCP 那样的多轮协议：进程插件每次调用都启动一个全新沙箱，
调用结束即退出。没有长期进程，也就没有"停止"的中间态需要处理——
停止 = 杀掉正在运行的沙箱进程（`--die-with-parent` 保证不留孤儿）。

安全约束（全部来自 §8.5 与提案 §4）：

1. **沙箱不可用 → 拒绝运行**（绝不降级放行）。
2. 输出上限与内建工具一致；超时由墙钟计时器强制。
3. 输出解析失败 → 结构化错误，不把插件的任意文本直接塞给模型。
4. 不信任插件声称的任何东西；结果标注来源。
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.plugins.manifest import PluginLimits
from app.plugins.sandbox import SandboxUnavailable, build_command, detect_sandbox

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8_000
MAX_OUTPUT_BYTES = 64 * 1024

# 沙箱内可用的解释器。用系统 Python 而不是虚拟环境里的：
# 虚拟环境路径在沙箱里不存在（实测报 "No such file or directory"）。
SANDBOX_PYTHON = "/usr/bin/python3"


class ProcessPluginError(RuntimeError):
    """进程插件执行失败。message 面向用户，不含内部细节。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ProcessResult:
    text: str
    is_error: bool
    truncated: bool


def sandbox_available() -> tuple[bool, str]:
    return detect_sandbox()


def _resolve_python() -> str:
    """选择沙箱内的解释器。

    优先系统 Python；若不存在则拒绝（沙箱内无法运行脚本解释器）。
    """
    path = Path(SANDBOX_PYTHON)
    if not path.exists():
        raise ProcessPluginError(
            f"沙箱内缺少 Python 解释器（{SANDBOX_PYTHON}）。", code="PLUGIN_NO_PYTHON"
        )
    return str(path)


async def run_plugin(
    *,
    plugin_dir: Path,
    entrypoint: str,
    tool_name: str,
    arguments: dict[str, object],
    limits: PluginLimits,
) -> ProcessResult:
    """在沙箱中运行插件并返回结果。

    任何失败都收敛成带有明确 code 的异常，由调用方转成工具结果。
    """
    available, reason = detect_sandbox()
    if not available:
        # 关键：沙箱不可用时**拒绝运行**，而不是无沙箱执行。
        raise ProcessPluginError(reason, code="PLUGIN_SANDBOX_UNAVAILABLE")

    try:
        command = build_command(
            plugin_dir=plugin_dir,
            entrypoint=entrypoint,
            arguments=arguments,
            limits=limits,
        )
    except SandboxUnavailable as exc:
        raise ProcessPluginError(str(exc), code="PLUGIN_SANDBOX_UNAVAILABLE") from exc

    python = _resolve_python()
    # 用解释器执行插件入口：插件作者写 Python 脚本即可，不必关心可执行位。
    # `-I` 隔离模式：不读取用户 site-packages 与 PYTHONPATH，行为可预测。
    argv = (*command.argv, python, "-I", _entry_path(entrypoint))

    payload = json.dumps(
        {"action": "call", "tool": tool_name, "arguments": arguments}, ensure_ascii=False
    )

    try:
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=command.env,
            # 子进程放进独立进程组，停止时可以整组终止。
            start_new_session=False,  # bwrap 已用 --new-session
        )
    except OSError as exc:
        logger.warning("插件进程启动失败", extra={"error": type(exc).__name__})
        raise ProcessPluginError("无法启动插件进程。", code="PLUGIN_SPAWN_FAILED") from exc

    try:
        stdout, stderr = await asyncio.wait_for(
            process.communicate(payload.encode("utf-8")),
            timeout=limits.timeout_seconds,
        )
    except TimeoutError as exc:
        # 停止语义：杀掉进程（bwrap 会连带杀掉沙箱内进程）。
        await _terminate(process)
        raise ProcessPluginError(
            f"插件执行超时（{limits.timeout_seconds} 秒），已终止。", code="PLUGIN_TIMEOUT"
        ) from exc
    except asyncio.CancelledError:
        await _terminate(process)
        raise

    if process.returncode != 0:
        # 退出码 137 = 被 SIGKILL（通常是 RLIMIT_CPU / RLIMIT_AS 触发）。
        hint = "资源限制" if process.returncode == 137 else f"退出码 {process.returncode}"
        logger.info(
            "插件非正常退出",
            extra={"returncode": process.returncode, "stderr_bytes": len(stderr)},
        )
        raise ProcessPluginError(f"插件执行失败（{hint}）。", code="PLUGIN_EXITED")

    return _parse_output(stdout)


def _entry_path(entrypoint: str) -> str:
    from app.plugins.sandbox import PLUGIN_MOUNT

    return PLUGIN_MOUNT + "/" + entrypoint.replace("\\", "/")


async def _terminate(process: asyncio.subprocess.Process) -> None:
    """终止插件进程并等待回收，避免僵尸进程。"""
    if process.returncode is not None:
        return
    try:
        process.kill()
    except ProcessLookupError:  # pragma: no cover - 已自行退出
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except TimeoutError:  # pragma: no cover - 极少发生
        logger.warning("插件进程未能及时回收")


def _parse_output(stdout: bytes) -> ProcessResult:
    """解析插件输出。

    插件是不可信的：只接受约定的 JSON 形状，其余一律当作错误，
    绝不把任意文本原样交给模型（否则插件可以伪造"系统消息"）。
    """
    if len(stdout) > MAX_OUTPUT_BYTES:
        return ProcessResult(text="插件输出超过上限，已拒绝。", is_error=True, truncated=True)

    text = stdout.decode("utf-8", errors="replace").strip()
    if not text:
        return ProcessResult(text="插件没有返回任何内容。", is_error=True, truncated=False)

    # 允许插件在 JSON 之前打印日志：取最后一个能解析为 JSON 的行。
    parsed: dict | None = None
    for line in reversed(text.splitlines()):
        candidate = line.strip()
        if not candidate.startswith("{"):
            continue
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            parsed = data
            break

    if parsed is None:
        return ProcessResult(
            text="插件返回的内容不是约定的 JSON 格式。", is_error=True, truncated=False
        )

    if parsed.get("ok") is False:
        error = str(parsed.get("error", "插件报告失败但没有说明原因。"))
        return ProcessResult(text=error[:MAX_RESULT_CHARS], is_error=True, truncated=False)

    content = parsed.get("content", "")
    if not isinstance(content, (str, int, float, bool)):
        content = json.dumps(content, ensure_ascii=False)

    rendered = str(content)
    truncated = False
    if len(rendered) > MAX_RESULT_CHARS:
        rendered = rendered[:MAX_RESULT_CHARS] + "\n…（内容超过上限已截断）"
        truncated = True

    return ProcessResult(
        text=rendered or "（插件返回了空内容）", is_error=False, truncated=truncated
    )


def probe_interpreter() -> tuple[bool, str]:
    """启动前检查：沙箱内的解释器是否可用。"""
    if not Path(SANDBOX_PYTHON).exists():
        return False, (f"未找到系统 Python（{SANDBOX_PYTHON}）。进程插件需要它来运行插件脚本。")
    return True, ""


__all__ = [
    "ProcessPluginError",
    "ProcessResult",
    "probe_interpreter",
    "run_plugin",
    "sandbox_available",
]
