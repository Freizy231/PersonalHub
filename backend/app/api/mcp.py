"""MCP 状态接口（M8 第 4–5 项）。

    GET /api/v1/mcp/servers    已配置的 Server 及其状态

**只读**：不提供运行期增删改 Server 的接口。连接外部服务与启动子进程
属于高影响操作（§8.3），必须由用户直接编辑配置文件并在服务启动时生效，
而不是由一个 HTTP 请求触发。

安全：

- 不返回子进程命令与环境变量的值（可能含用户路径信息）；
- 不返回 Token（配置里只存变量名，本来就没有 Token）；
- Server 自述的工具描述原样展示时已标注"来自外部服务"。
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

router = APIRouter(prefix="/mcp", tags=["mcp"])


class McpServerOut(BaseModel):
    name: str
    transport: str
    enabled: bool
    # 只给出命令的**可执行文件名**，不暴露完整参数（参数里常有用户目录路径）。
    command: str
    status: str  # ready / unavailable / disabled
    tool_count: int
    tools: list[str]
    reason: str | None = None


class McpServersOut(BaseModel):
    items: list[McpServerOut]
    total: int
    errors: list[str]
    notice: str = (
        "MCP 工具来自外部服务，全部需要你逐次确认后才会执行。外部服务声称的只读属性不会被采信。"
    )


@router.get("/servers", response_model=McpServersOut, summary="列出 MCP Server")
async def list_servers(request: Request) -> McpServersOut:
    mcp_settings = getattr(request.app.state, "mcp_settings", None)
    status = getattr(request.app.state, "mcp_status", {"errors": [], "servers": {}})

    items: list[McpServerOut] = []
    for server in mcp_settings.servers if mcp_settings else ():
        info = status.get("servers", {}).get(server.name, {})
        enabled = bool(server.enabled)
        items.append(
            McpServerOut(
                name=server.name,
                transport=server.transport.value,
                enabled=enabled,
                # 只显示可执行文件名：完整路径与 args 可能暴露用户目录结构。
                command=_command_basename(server.command),
                status=str(info.get("status", "ready" if enabled else "disabled")),
                tool_count=int(info.get("tool_count", 0)),
                tools=list(info.get("tools", [])),
                reason=info.get("reason"),
            )
        )

    return McpServersOut(
        items=items,
        total=len(items),
        errors=list(status.get("errors", [])),
    )


def _command_basename(command: str) -> str:
    """只取可执行文件名，去掉目录与参数。"""
    if not command:
        return ""
    return command.replace("\\", "/").rsplit("/", 1)[-1]
