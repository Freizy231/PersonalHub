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
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class StreamChunk:
    """流式输出的一个片段。

    delta 为空字符串时表示结束标记之外的心跳，调用方应跳过渲染。
    """

    delta: str


class ChatProvider(Protocol):
    """供应商适配器协议。

    只暴露 MVP 需要的能力：对话流式输出。工具调用、多模态等在后续里程碑扩展。
    """

    name: str

    def stream_chat(self, messages: list[ChatMessage], model: str) -> AsyncIterator[StreamChunk]:
        """以异步迭代器返回增量文本。实现必须关闭底层连接。"""
        ...


def read_api_key(env_name: str) -> str:
    """读取 API Key。空值视为未配置，绝不返回空串冒充有效值。"""
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ProviderConfigurationError(f"未配置 {env_name}。请在启动后端前设置该环境变量。")
    return value
