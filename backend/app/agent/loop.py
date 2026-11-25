"""Agent Loop（AGENT.md §4.8）。

一次对话回合的循环：

    模型调用 -> 若返回工具调用 -> 经 Broker 执行 -> 结果回填 -> 再调用模型
             -> 若返回文本 -> 结束

硬性约束：
- **最大循环次数**：模型可能反复要求调用工具，必须设上限（§4.8）。
- **每次工具调用都走 Broker**：Agent 不能直接调用 Handler（§4.10）。
- **工具结果不可信**：回填给模型时标明来源，提示其不得把结果里的指令当真（§8.4）。
- 工具失败不终止循环：让模型看到错误并自行决定下一步，比直接崩掉更合理。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from app.ai import ChatMessage, ProviderError, StreamChunk, ToolCall, ToolSpec
from app.capabilities import ToolContext
from app.capabilities.broker import ToolBroker, ToolResult

logger = logging.getLogger(__name__)

# 单回合最多几轮"模型<->工具"往返。防止模型陷入自我循环消耗额度（§4.8）。
MAX_TOOL_ROUNDS = 4


class AgentLoopExceeded(RuntimeError):
    """超过最大循环次数。调用方应告知用户，而不是继续消耗额度。"""


@dataclass
class AgentEvent:
    """Agent 循环产出的事件，由 API 层转成 SSE。"""

    kind: str  # delta / tool_start / tool_end / done / error
    text: str = ""
    payload: dict[str, object] = field(default_factory=dict)


async def run_agent_loop(
    *,
    provider,  # type: ignore[no-untyped-def]
    model: str,
    messages: list[ChatMessage],
    broker: ToolBroker,
    tools: list[ToolSpec],
    context: ToolContext,
    max_rounds: int = MAX_TOOL_ROUNDS,
) -> AsyncIterator[AgentEvent]:
    """执行一轮对话，必要时穿插工具调用。

    调用方负责把事件转成 SSE 并持久化最终文本。
    """
    conversation = list(messages)

    for round_index in range(max_rounds + 1):
        collected_text: list[str] = []
        tool_calls: dict[int, _PartialCall] = {}
        finish_reason: str | None = None

        try:
            async for chunk in provider.stream_chat(conversation, model, tools or None):
                if chunk.delta:
                    collected_text.append(chunk.delta)
                    yield AgentEvent(kind="delta", text=chunk.delta)
                if chunk.tool_call_delta is not None:
                    _accumulate(tool_calls, chunk)
                if chunk.finish_reason:
                    finish_reason = chunk.finish_reason
                    # finish_reason 表示本轮生成已经结束。有些服务端不会主动
                    # 关闭流（例如带心跳或连接复用的网关），继续读会一直挂住，
                    # 用户看到的是"说完了但不继续"。拿到结束标记就停。
                    break
        except ProviderError as exc:
            yield AgentEvent(kind="error", payload={"code": exc.code, "message": str(exc)})
            return

        assistant_text = "".join(collected_text)

        # 没有工具调用：这是一次普通回答，回合结束。
        if not tool_calls:
            yield AgentEvent(
                kind="done",
                # 不重复携带全文：文本已经通过 delta 事件流式发出，
                # 再带一次会让调用方重复拼接（实测踩过）。
                payload={
                    "finish_reason": finish_reason,
                    "rounds": round_index + 1,
                    "answer": assistant_text,
                },
            )
            return

        if round_index >= max_rounds:
            # 已经用完轮次仍在要求调用工具：停止，避免无限消耗。
            yield AgentEvent(
                kind="error",
                payload={
                    "code": "TOOL_LOOP_EXCEEDED",
                    "message": "工具调用次数超过上限，已停止本轮处理。",
                },
            )
            return

        calls = [_finalize(index, partial) for index, partial in sorted(tool_calls.items())]

        # 把 assistant 的工具调用意图写入上下文，否则供应商会拒绝后续 tool 消息。
        conversation.append(
            ChatMessage(
                role="assistant",
                content=assistant_text,
                tool_calls=tuple(calls),
            )
        )

        for call in calls:
            # 带上工具自己的中文名，界面就不用维护一张会漂移的对照表（D-012）。
            definition = broker.definition_of(call.name)
            yield AgentEvent(
                kind="tool_start",
                payload={
                    "id": call.id,
                    "name": call.name,
                    **({"display_name": definition.title} if definition is not None else {}),
                },
            )
            # Broker 是 async：写操作会在这里暂停、等待用户审批。
            result = await broker.execute(call.name, call.arguments, context, call_id=call.id)
            conversation.append(_tool_message(result))
            yield AgentEvent(
                kind="tool_end",
                payload={
                    "id": call.id,
                    "name": call.name,
                    "status": result.status,
                    "error_code": result.error_code,
                    "approval_id": result.approval_id,
                },
            )

    # 理论上不可达：循环内部每条路径都会 return。
    raise AgentLoopExceeded("agent loop exceeded")  # pragma: no cover


@dataclass
class _PartialCall:
    call_id: str = ""
    name: str = ""
    arguments: str = ""


def _accumulate(store: dict[int, _PartialCall], chunk: StreamChunk) -> None:
    """把工具调用分片按 index 累积。

    协议细节：只有第一片带 id/name，后续分片只带 arguments 片段；
    必须按 index 归并，不能按到达顺序（多个调用可能交错到达）。
    """
    delta = chunk.tool_call_delta
    assert delta is not None
    partial = store.setdefault(delta.index, _PartialCall())
    if delta.id:
        partial.call_id = delta.id
    if delta.name:
        partial.name = delta.name
    partial.arguments += delta.arguments_fragment


def _finalize(index: int, partial: _PartialCall) -> ToolCall:
    """补全缺失字段。

    模型偶尔不返回 id；缺 id 的调用于后续 tool 消息无法对应，
    因此生成一个稳定占位（仅本回合内有效）。
    """
    return ToolCall(
        id=partial.call_id or f"call_{index}",
        name=partial.name,
        arguments=partial.arguments,
    )


def _tool_message(result: ToolResult) -> ChatMessage:
    """把工具结果包装成 tool 消息。

    关键：明确标注这是**工具输出、属于不可信数据**，
    防止工具返回内容被模型当成新的系统指令（§8.4 间接注入）。

    对 denied 状态额外给出指引：实测发现模型面对"被拒绝"时倾向于
    含糊带过（例如只说"结果无法解析"）。明确告诉它如实告知用户是必要的，
    否则用户会以为操作成功了。
    """
    payload: dict[str, object] = {
        "source": "tool",
        "trust": "untrusted",
        "tool": result.name,
        "status": result.status,
        **({"error_code": result.error_code} if result.error_code else {}),
        "result": result.content,
    }
    if result.status == "denied":
        payload["instruction"] = (
            "该操作被拒绝，未执行任何更改。请如实告知用户操作没有完成及原因，不要声称已完成。"
        )
    elif result.status == "error":
        payload["instruction"] = "该操作失败，未产生预期结果。请如实说明，不要编造结果。"

    return ChatMessage(
        role="tool",
        content=json.dumps(payload, ensure_ascii=False),
        tool_call_id=result.tool_call_id,
    )
