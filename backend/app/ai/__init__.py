"""模型网关（AGENT.md §4.6）。

统一不同供应商的接口，业务层只依赖 ChatProvider 协议，不绑定某一家 SDK。

安全边界（不可妥协）：
- API Key 只从进程环境变量读取，永不进入数据库、日志、Prompt 或前端。
- 网关不保存长期记忆，不做权限决策。
- 日志只记录元数据（模型名、消息条数、耗时、token 用量），不记录对话正文。
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Protocol


class ProviderConfigurationError(RuntimeError):
    """供应商未配置（例如缺少 API Key）。

    这是配置问题，不是用户输入问题；错误信息里绝不能包含 Key 本身。
    """


class ProviderError(RuntimeError):
    """供应商调用失败（网络、鉴权、限流、服务端错误）。

    message 面向用户，必须安全；供应商原始报错只进服务端日志。
    """

    def __init__(self, message: str, *, code: str = "PROVIDER_ERROR") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class ChatMessage:
    """一条对话消息。

    role 取值：system / user / assistant / tool。
    带 tool 角色时 tool_call_id 必须指向对应的工具调用（OpenAI 协议要求）。
    """

    role: str
    content: str
    tool_call_id: str | None = None
    # assistant 发起工具调用时，把调用列表一并带上，供下一轮上下文使用。
    tool_calls: tuple[ToolCall, ...] = ()


@dataclass(frozen=True, slots=True)
class ToolCall:
    """模型发起的一次工具调用。

    arguments 是**未经解析的原始 JSON 字符串**：模型可能返回非法 JSON，
    必须在 Tool Broker 处校验，不能在这里假设它合法。
    """

    id: str
    name: str
    arguments: str


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """提供给模型的工具声明（JSON Schema）。

    只描述"能做什么"，不授予任何权限——真正是否允许由 Tool Broker 决定。
    """

    name: str
    description: str
    parameters: dict[str, object]


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """流式输出的一个片段。

    delta 为空字符串时表示结束标记之外的心跳，调用方应跳过渲染。
    tool_call_delta 非空时表示正在累积一次工具调用（参数可能分多片到达）。
    """

    delta: str = ""
    tool_call_delta: ToolCallDelta | None = None
    finish_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ToolCallDelta:
    """工具调用的一个流式分片。

    index 用于把后续只带 arguments 的分片归到同一次调用上；
    id / name 只在第一片出现。
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_fragment: str = ""


class ChatProvider(Protocol):
    """供应商适配器协议。

    除流式对话外，支持可选的工具调用。传入 tools 为空时不发送工具声明，
    保证不支持工具调用的模型仍可正常使用。
    """

    name: str

    def stream_chat(
        self, messages: list[ChatMessage], model: str, tools: list[ToolSpec] | None = None
    ) -> AsyncIterator[StreamChunk]:
        """以异步迭代器返回增量文本与工具调用分片。实现必须关闭底层连接。"""
        ...


def read_api_key(env_name: str) -> str:
    """读取 API Key。空值视为未配置，绝不返回空串冒充有效值。"""
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ProviderConfigurationError(f"未配置 {env_name}。请在启动后端前设置该环境变量。")
    return value
