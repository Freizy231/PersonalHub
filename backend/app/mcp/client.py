"""MCP Client（M8 第 4 项，提案第 2 步）。

职责：连接 MCP Server、列举工具、调用工具。**不做授权判断**——
授权由 Tool Broker / Policy 完成，这里只负责"把话说出去、把结果拿回来"。

设计取舍：

1. **懒连接**：服务启动时不连任何 Server。外部依赖不可用不应该让
   本地 AI 环境起不来；也避免用户没在用的 Server 占着子进程。
2. **每次调用一个会话**：MCP 的 stdio 传输与 asyncio 任务绑定很紧，
   连接生命周期跨请求管理容易在事件循环边界上出错（实测这类问题
   表现是"偶发卡死"，很难排查）。MCP 协议本身允许每次重新初始化，
   因此选择"每次调用新建会话"换取确定性。代价是每次调用多一次握手，
   对本地 Server 是毫秒级。
3. **所有异常收敛成 McpCallError**：调用方（Broker）需要的是结构化结果，
   而不是 SDK 的异常类型。
4. **不记录工具参数与返回值到日志**：它们可能含用户数据（§7.4）。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.mcp.config import McpServerConfig, Transport

logger = logging.getLogger(__name__)

# 单次工具调用的结果文本上限。与内建工具的 MAX_RESULT_CHARS 保持一致，
# 避免"通过 MCP 塞入超长内容"成为新的上下文挤占手段（§8.4）。
MAX_MCP_RESULT_CHARS = 8_000

# 列举工具时的上限，防止恶意/异常的 Server 返回海量工具。
MAX_TOOLS_PER_SERVER = 200


class McpCallError(RuntimeError):
    """MCP 调用失败。message 面向模型与用户，不含内部细节。"""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class McpToolInfo:
    """从 Server 发现的一个工具。字段来自 Server，因此**全部不可信**。

    `description` 会进入模型上下文，调用方必须标注其来源（§8.4）。
    `input_schema` 用于参数校验，但校验失败也不能当作 Server 可信的证据。
    """

    server: str
    name: str
    description: str
    input_schema: dict[str, Any]

    @property
    def qualified_name(self) -> str:
        """登记到本系统的工具名：mcp__<server>__<tool>。

        加前缀是为了避免与内建工具重名，也让用户一眼看出工具来源。
        """
        return f"mcp__{self.server}__{self.name}"


@dataclass(frozen=True, slots=True)
class McpCallResult:
    """一次工具调用的结果。`is_error` 来自 Server 的 isError 标记。"""

    text: str
    is_error: bool
    truncated: bool


def _build_server_params(server: McpServerConfig, workdir: Path):  # type: ignore[no-untyped-def]
    """构造 stdio 传输参数。

    环境变量刻意**不继承**当前进程：宿主进程里有模型 API Key，
    把它透传给第三方 Server 等于把密钥交给别人（§8.6）。
    只给出最小集合（PATH / HOME / LANG），保证常见 Server 能启动。
    """
    import os

    from mcp import StdioServerParameters

    minimal_env = {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", str(workdir)),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        # Python 系 Server 常需要，但不含任何密钥。
        "PYTHONUNBUFFERED": "1",
    }
    return StdioServerParameters(
        command=server.command,
        args=list(server.args),
        env=minimal_env,
        cwd=str(workdir),
    )


def _http_auth_headers(server: McpServerConfig) -> dict[str, str]:
    """从环境变量读取静态 Token。配置里只存**变量名**，不存密钥本身。"""
    import os

    if not server.token_env:
        return {}
    token = os.environ.get(server.token_env, "").strip()
    if not token:
        raise McpCallError(
            f"Server {server.name} 需要环境变量 {server.token_env}，但它没有设置。",
            code="MCP_TOKEN_MISSING",
        )
    return {"Authorization": f"Bearer {token}"}


def _tool_text(result: Any) -> tuple[str, bool]:
    """把 CallToolResult 的内容拼成一段文本。

    只取文本内容；图片、音频、内嵌资源一律降级为占位说明——
    把二进制塞进模型上下文既无意义又昂贵。
    长度超过上限时截断，并明确告知模型"内容不完整"。
    """
    parts: list[str] = []
    for item in getattr(result, "content", []) or []:
        kind = getattr(item, "type", "")
        if kind == "text":
            parts.append(str(getattr(item, "text", "")))
        elif kind == "image":
            parts.append("[图片内容：MCP 返回了图片，本地未展开]")
        elif kind == "audio":
            parts.append("[音频内容：MCP 返回了音频，本地未展开]")
        elif kind == "resource":
            parts.append("[资源引用：MCP 返回了资源，本地未展开]")
        else:
            parts.append(f"[{kind or '未知类型'}内容：未展开]")

    text = "\n".join(part for part in parts if part).strip()
    truncated = False
    if len(text) > MAX_MCP_RESULT_CHARS:
        text = text[:MAX_MCP_RESULT_CHARS] + "\n…（内容超过上限已截断）"
        truncated = True
    if not text:
        text = "（Server 返回了空内容）"
    return text, truncated


class McpClient:
    """按配置调用 MCP Server。无状态：每次调用自建会话。"""

    def __init__(self, *, workdir_root: Path) -> None:
        # 子进程的工作目录固定在数据目录下的 mcp/，
        # 不让 Server 的默认工作目录落在用户主目录或源码仓库里。
        self._workdir_root = workdir_root

    def _workdir(self, server: McpServerConfig) -> Path:
        path = self._workdir_root / server.name
        path.mkdir(parents=True, exist_ok=True)
        return path

    async def list_tools(self, server: McpServerConfig) -> list[McpToolInfo]:
        """连接 Server 并列出其工具。失败抛 McpCallError。"""
        import asyncio

        try:
            return await asyncio.wait_for(self._list_tools(server), timeout=server.timeout_seconds)
        except TimeoutError as exc:
            raise McpCallError(
                f"连接 Server {server.name} 超时（{server.timeout_seconds} 秒）。",
                code="MCP_TIMEOUT",
            ) from exc

    async def _list_tools(self, server: McpServerConfig) -> list[McpToolInfo]:
        async with self._session(server) as session:
            result = await session.list_tools()

        tools: list[McpToolInfo] = []
        for item in result.tools[:MAX_TOOLS_PER_SERVER]:
            schema = getattr(item, "inputSchema", None)
            tools.append(
                McpToolInfo(
                    server=server.name,
                    name=str(getattr(item, "name", "")),
                    description=str(getattr(item, "description", "") or ""),
                    # Schema 来自 Server，原样保留但会在 Broker 侧校验参数时使用；
                    # 拿不到合法 object schema 时退化为"空 object"，不放宽校验。
                    input_schema=schema if isinstance(schema, dict) else {},
                )
            )
        return tools

    async def call_tool(
        self, server: McpServerConfig, tool_name: str, arguments: dict[str, Any]
    ) -> McpCallResult:
        """调用一个工具。失败抛 McpCallError。"""
        import asyncio

        try:
            return await asyncio.wait_for(
                self._call_tool(server, tool_name, arguments),
                timeout=server.timeout_seconds,
            )
        except TimeoutError as exc:
            # 不重试：远端可能已经产生了副作用，重试有重复执行的风险。
            raise McpCallError(
                f"调用 Server {server.name} 的工具超时（{server.timeout_seconds} 秒）。",
                code="MCP_TIMEOUT",
            ) from exc

    async def _call_tool(
        self, server: McpServerConfig, tool_name: str, arguments: dict[str, Any]
    ) -> McpCallResult:
        try:
            async with self._session(server) as session:
                result = await session.call_tool(tool_name, arguments)
        except McpCallError:
            raise
        except Exception as exc:
            # 实测：Server 返回格式不合规的结果时，SDK 抛出的校验异常正文
            # （例如 pydantic 的 "1 validation error for CallToolResult ..."）
            # 会原样进入模型上下文——那是内部实现细节，对模型毫无用处，
            # 还泄露了我们的技术栈。统一转成可读、固定的说明。
            logger.warning(
                "MCP 调用失败",
                extra={"server": server.name, "tool": tool_name, "error_type": type(exc).__name__},
            )
            raise McpCallError(
                f"Server {server.name} 的工具 {tool_name} 返回了无法解析的结果。",
                code="MCP_BAD_RESPONSE",
            ) from exc

        text, truncated = _tool_text(result)
        return McpCallResult(
            text=text,
            is_error=bool(getattr(result, "isError", False)),
            truncated=truncated,
        )

    def _session(self, server: McpServerConfig):  # type: ignore[no-untyped-def]
        """返回一个异步上下文管理器，产出已初始化的 ClientSession。"""
        if server.transport is Transport.STDIO:
            return self._stdio_session(server)
        return self._http_session(server)

    def _stdio_session(self, server: McpServerConfig):  # type: ignore[no-untyped-def]
        from contextlib import asynccontextmanager

        from mcp import ClientSession
        from mcp.client.stdio import stdio_client

        @asynccontextmanager
        async def manager():  # type: ignore[no-untyped-def]
            params = _build_server_params(server, self._workdir(server))
            try:
                async with stdio_client(params) as (read_stream, write_stream):
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            except McpCallError:
                raise
            except Exception as exc:
                # SDK 的异常类型繁杂，统一收敛；只记类型名，不记正文（可能含用户数据）。
                logger.warning(
                    "MCP stdio 会话失败",
                    extra={"server": server.name, "error_type": type(exc).__name__},
                )
                raise McpCallError(
                    f"无法连接 Server {server.name}，请检查它的配置与运行状态。",
                    code="MCP_UNAVAILABLE",
                ) from exc

        return manager()

    def _http_session(self, server: McpServerConfig):  # type: ignore[no-untyped-def]
        from contextlib import asynccontextmanager

        from mcp import ClientSession

        @asynccontextmanager
        async def manager():  # type: ignore[no-untyped-def]
            headers = _http_auth_headers(server)
            try:
                async with self._http_transport(server, headers) as streams:
                    # streamable-http 产出三元组（读流、写流、session_id 回调），
                    # 与 stdio 的二元组不同，这里只取前两个。
                    read_stream, write_stream = streams[0], streams[1]
                    async with ClientSession(read_stream, write_stream) as session:
                        await session.initialize()
                        yield session
            except McpCallError:
                raise
            except Exception as exc:
                logger.warning(
                    "MCP http 会话失败",
                    extra={"server": server.name, "error_type": type(exc).__name__},
                )
                raise McpCallError(
                    f"无法连接 Server {server.name}，请检查地址与网络。",
                    code="MCP_UNAVAILABLE",
                ) from exc

        return manager()

    def _http_transport(self, server: McpServerConfig, headers: dict[str, str]):  # type: ignore[no-untyped-def]
        from mcp.client.streamable_http import streamablehttp_client

        return streamablehttp_client(server.url, headers=headers or None)
