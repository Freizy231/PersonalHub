"""MCP Server 配置（M8 第 4 项，见 docs/PROPOSAL_MCP.md）。

配置放在数据目录的 `mcp/servers.json`，**不进数据库**：
它是"要不要连某个外部服务"的本地决定，与用户业务数据性质不同，
混进数据库会让备份/导出/迁移都面临"带不带外部连接配置"的问题。

安全设计（每一条都对应提案 §4 的信任模型）：

- **默认关闭**：每个 Server 必须显式 `"enabled": true` 才会连接。
  装了就等于开了，是外部能力最典型的误用方式。
- **风险级别由本地决定**：用户批准的是 `一律 WRITE`（提案待定问题 3），
  配置里**不接受** `risk` 字段覆盖，避免"把别人的工具降级成只读"。
- **命令与参数严格校验**：`command` 必须是绝对路径或白名单里的可执行名；
  `args` 不接受空字节；工作目录固定在数据目录下的 `mcp/`。
- 文件本身可能被用户手改错，因此读取失败要给出**可操作**的错误，
  而不是让服务起不来。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

MCP_CONFIG_FILENAME = "servers.json"

# Server 名会成为工具名的前缀（mcp__<server>__<tool>），
# 因此只允许模型与命令行都安全的字符。
_SERVER_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")

# 允许直接使用的可执行名（PATH 中查找）。
# 刻意不给"任意命令"：配置写错时 `command` 串就会被 sh 解释，
# 一个手滑的分号可能变成一条意料之外的命令。
_ALLOWED_COMMANDS = frozenset({"npx", "uvx", "node", "python3", "python"})

MAX_SERVERS = 20
MAX_ARGS = 32
MAX_ARG_LENGTH = 2_000
MAX_TIMEOUT_SECONDS = 300
DEFAULT_TIMEOUT_SECONDS = 30


class McpConfigError(ValueError):
    """配置不合法。message 面向用户，必须指出是哪个 Server 的哪个字段。"""


class Transport(StrEnum):
    STDIO = "stdio"
    HTTP = "http"


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """单个 MCP Server 的本地配置。

    `enabled` 默认为 False：用户必须显式打开。这一条无法通过配置绕过。
    """

    name: str
    transport: Transport
    # stdio 传输使用
    command: str = ""
    args: tuple[str, ...] = ()
    # http 传输使用
    url: str = ""
    # 仅 http 传输可用的静态 Bearer Token；stdio 不使用。
    token_env: str = ""
    enabled: bool = False
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    # 仅用于界面展示的说明，不参与任何决策。
    description: str = ""

    def __post_init__(self) -> None:
        if isinstance(self.transport, str) and not isinstance(self.transport, Transport):
            try:
                object.__setattr__(self, "transport", Transport(self.transport))
            except ValueError as exc:
                options = " / ".join(item.value for item in Transport)
                raise McpConfigError(
                    f"Server {self.name} 的 transport 取值无效："
                    f"{self.transport!r}（可选：{options}）"
                ) from exc
        if isinstance(self.args, list):
            object.__setattr__(self, "args", tuple(self.args))
        validate_server(self)


@dataclass(frozen=True, slots=True)
class McpSettings:
    """整份配置。加载失败时 `errors` 非空，但**仍然返回可用的 Server**。"""

    servers: tuple[McpServerConfig, ...] = ()
    errors: tuple[str, ...] = field(default_factory=tuple)

    @property
    def enabled_servers(self) -> tuple[McpServerConfig, ...]:
        return tuple(server for server in self.servers if server.enabled)

    def get(self, name: str) -> McpServerConfig | None:
        for server in self.servers:
            if server.name == name:
                return server
        return None


def validate_server(server: McpServerConfig) -> None:
    """逐字段校验。任何一项不合法都抛 McpConfigError。"""
    if not _SERVER_NAME_PATTERN.match(server.name):
        raise McpConfigError(
            f"Server 名 {server.name!r} 不合法：只能用小写字母、数字、下划线、连字符，"
            "以字母开头，最长 32 个字符。"
        )

    if server.transport is Transport.STDIO:
        if not server.command:
            raise McpConfigError(f"stdio 类型的 Server {server.name} 必须提供 command。")
        _validate_command(server)
        if server.url:
            raise McpConfigError(f"stdio 类型的 Server {server.name} 不应提供 url。")
    elif server.transport is Transport.HTTP:
        if not server.url:
            raise McpConfigError(f"http 类型的 Server {server.name} 必须提供 url。")
        _validate_url(server.url, server.name)
        if server.command or server.args:
            raise McpConfigError(f"http 类型的 Server {server.name} 不应提供 command/args。")

    if len(server.args) > MAX_ARGS:
        raise McpConfigError(f"Server {server.name} 的 args 不能超过 {MAX_ARGS} 项。")
    for arg in server.args:
        if not isinstance(arg, str):
            raise McpConfigError(f"Server {server.name} 的 args 必须全部是字符串。")
        if "\x00" in arg:
            raise McpConfigError(f"Server {server.name} 的 args 不能包含空字节。")
        if len(arg) > MAX_ARG_LENGTH:
            raise McpConfigError(
                f"Server {server.name} 的单个 arg 不能超过 {MAX_ARG_LENGTH} 个字符。"
            )

    if not 1 <= server.timeout_seconds <= MAX_TIMEOUT_SECONDS:
        raise McpConfigError(
            f"Server {server.name} 的 timeout_seconds 必须在 1–{MAX_TIMEOUT_SECONDS} 之间。"
        )


def _validate_command(server: McpServerConfig) -> None:
    command = server.command
    if "\x00" in command:
        raise McpConfigError(f"Server {server.name} 的 command 不能包含空字节。")

    path = Path(command)
    if path.is_absolute():
        # 绝对路径：必须是存在的文件，且不允许目录（避免把目录当程序）。
        if not path.exists():
            raise McpConfigError(f"Server {server.name} 的 command 不存在：{command}")
        if path.is_dir():
            raise McpConfigError(
                f"Server {server.name} 的 command 是目录，不是可执行文件：{command}"
            )
        return

    # 非绝对路径：只允许白名单里的可执行名，避免配置里的特殊字符被解释。
    if command not in _ALLOWED_COMMANDS:
        allowed = " / ".join(sorted(_ALLOWED_COMMANDS))
        raise McpConfigError(
            f"Server {server.name} 的 command {command!r} 不在允许列表中（{allowed}），"
            "也未使用绝对路径。请写完整路径，或使用允许的命令名。"
        )


def _validate_url(url: str, name: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise McpConfigError(f"Server {name} 的 url 必须是 http 或 https：{url}")
    if not parsed.hostname:
        raise McpConfigError(f"Server {name} 的 url 缺少主机名：{url}")
    if "\x00" in url:
        raise McpConfigError(f"Server {name} 的 url 不能包含空字节。")


def parse_settings(data: dict[str, Any]) -> McpSettings:
    """解析配置字典。

    单个 Server 出错**不影响其他 Server**：用户手写配置时很容易写错一处，
    如果整份配置因此失效，他会以为"什么都没生效"却不知道哪里错了。
    因此逐个解析，把错误收集到 `errors` 里一起返回。
    """
    if not isinstance(data, dict):
        raise McpConfigError("配置文件顶层必须是一个对象。")

    unknown = set(data) - {"servers"}
    if unknown:
        raise McpConfigError(f"配置含有未知字段：{', '.join(sorted(unknown))}")

    raw_servers = data.get("servers", [])
    if not isinstance(raw_servers, list):
        raise McpConfigError("servers 必须是数组。")
    if len(raw_servers) > MAX_SERVERS:
        raise McpConfigError(f"Server 数量不能超过 {MAX_SERVERS} 个。")

    servers: list[McpServerConfig] = []
    errors: list[str] = []
    seen: set[str] = set()

    for index, entry in enumerate(raw_servers):
        try:
            server = _parse_server(entry, index)
        except McpConfigError as exc:
            errors.append(str(exc))
            continue
        if server.name in seen:
            # 重名会让工具前缀冲突，必须拒绝第二个而不是覆盖。
            errors.append(f"Server 名重复：{server.name}（后一个已忽略）")
            continue
        seen.add(server.name)
        servers.append(server)

    return McpSettings(servers=tuple(servers), errors=tuple(errors))


def _parse_server(entry: Any, index: int) -> McpServerConfig:
    if not isinstance(entry, dict):
        raise McpConfigError(f"第 {index + 1} 个 Server 配置必须是对象。")

    # risk 字段被刻意拒绝：MCP 工具一律按 WRITE 处理（提案待定问题 3）。
    # 必须在"未知字段"检查之前判断，否则用户只会看到一句泛泛的
    # "含有未知字段：risk"，不知道这是刻意的设计而不是笔误。
    if "risk" in entry:
        raise McpConfigError(
            f"Server {entry.get('name', index + 1)} 不支持指定 risk："
            "MCP 工具一律按 WRITE 处理，每次调用都需要你确认。"
        )

    allowed = {
        "name",
        "transport",
        "command",
        "args",
        "url",
        "token_env",
        "enabled",
        "timeout_seconds",
        "description",
    }
    unknown = set(entry) - allowed
    if unknown:
        raise McpConfigError(f"第 {index + 1} 个 Server 含有未知字段：{', '.join(sorted(unknown))}")

    name = entry.get("name")
    if not isinstance(name, str) or not name.strip():
        raise McpConfigError(f"第 {index + 1} 个 Server 缺少 name。")

    transport = entry.get("transport")
    if not isinstance(transport, str):
        raise McpConfigError(f"Server {name} 缺少 transport。")

    args = entry.get("args", [])
    if not isinstance(args, list):
        raise McpConfigError(f"Server {name} 的 args 必须是数组。")

    enabled = entry.get("enabled", False)
    if not isinstance(enabled, bool):
        raise McpConfigError(f"Server {name} 的 enabled 必须是 true 或 false。")

    timeout = entry.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        raise McpConfigError(f"Server {name} 的 timeout_seconds 必须是整数。")

    for key in ("command", "url", "token_env", "description"):
        if key in entry and not isinstance(entry[key], str):
            raise McpConfigError(f"Server {name} 的 {key} 必须是字符串。")

    return McpServerConfig(
        name=name.strip(),
        transport=Transport(transport),
        command=str(entry.get("command", "")).strip(),
        args=tuple(args),
        url=str(entry.get("url", "")).strip(),
        token_env=str(entry.get("token_env", "")).strip(),
        enabled=enabled,
        timeout_seconds=timeout,
        description=str(entry.get("description", "")).strip(),
    )


def load_settings(config_path: Path) -> McpSettings:
    """从磁盘读取配置。

    文件不存在 = 没有配置任何 MCP Server（正常状态，不是错误）。
    文件损坏 = 返回空配置 + 一条错误，服务照常启动：
    外部能力的配置问题不应该让本地 AI 环境起不来。
    """
    if not config_path.is_file():
        return McpSettings()

    try:
        text = config_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("读取 MCP 配置失败", extra={"error": type(exc).__name__})
        return McpSettings(errors=("无法读取 MCP 配置文件。",))

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # 指出行列，用户才找得到错在哪。
        return McpSettings(
            errors=(f"MCP 配置文件不是合法 JSON（第 {exc.lineno} 行第 {exc.colno} 列）：{exc.msg}",)
        )

    try:
        return parse_settings(data)
    except McpConfigError as exc:
        return McpSettings(errors=(str(exc),))
