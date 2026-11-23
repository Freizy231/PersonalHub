"""流式对话接口（AGENT.md §7.1：POST /api/v1/chat/stream）。

数据流：

    用户消息入库 -> 读取历史 -> 调用 Provider 流式生成
      -> 逐块推给前端（SSE）
      -> 生成结束：助手消息入库
      -> 生成失败或用户取消：不保存半截回复

取消语义（§7.3）：用户中断表示放弃这次生成，**不保存**不完整的助手回复，
避免库里出现被截断的 AI 消息被误当作完整内容。

为什么用 POST + SSE 而不是 EventSource：
EventSource 只支持 GET，无法携带消息正文；而且 GET 会把内容塞进 URL，
进入浏览器历史与代理日志。POST + fetch 流式读取更安全。
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from app.ai import ChatMessage, ProviderConfigurationError, ProviderError, read_api_key
from app.ai.deepseek import DeepSeekProvider
from app.api.deps import ConversationRepoDep, MessageRepoDep
from app.core.audit import EgressRecord, record_egress
from app.core.config import Settings
from app.core.errors import PublicMessageError
from app.schemas.chat import ChatStreamRequest

router = APIRouter(prefix="/chat", tags=["chat"])

logger = logging.getLogger(__name__)

# 发送给模型的历史消息上限。上下文窗口有限（AGENT.md §1.7），
# MVP 先用简单的条数截断；M4 的 Context Engine 会做真正的预算管理。
MAX_HISTORY_MESSAGES = 40


def _sse(event: str, data: dict[str, object]) -> str:
    """构造一条 SSE 事件。data 用 JSON，避免换行破坏 SSE 帧格式。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def build_provider(settings: Settings) -> DeepSeekProvider:
    """按配置构造供应商。当前只有 DeepSeek；后续在这里扩展分支。"""
    if settings.provider != "deepseek":
        raise ProviderConfigurationError(f"暂不支持的供应商：{settings.provider}")
    return DeepSeekProvider(read_api_key("DEEPSEEK_API_KEY"), base_url=settings.provider_base_url)


@router.post("/stream", summary="流式对话")
async def stream_chat(
    payload: ChatStreamRequest,
    request: Request,
    conversations: ConversationRepoDep,
    messages: MessageRepoDep,
) -> StreamingResponse:
    conversation = conversations.get_in_workspace(payload.conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    settings: Settings = request.app.state.settings

    # 供应商未配置时立即返回明确错误，而不是开始一个注定失败的流。
    try:
        provider = build_provider(settings)
    except ProviderConfigurationError as exc:
        # 配置缺失是用户能自行修复的问题，文案由本模块预先写死，安全可外露。
        raise PublicMessageError(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="PROVIDER_NOT_CONFIGURED",
            message=("模型服务未配置。请在后端环境变量中设置 DEEPSEEK_API_KEY 后重启服务。"),
        ) from exc

    # 1) 先保存用户消息。即使用户随后取消，他的提问也不该丢。
    messages.append(payload.conversation_id, payload.content, role="user")
    conversations.touch(conversation)

    history = messages.list_for_conversation(payload.conversation_id, limit=MAX_HISTORY_MESSAGES)
    chat_messages = [ChatMessage(role=m.role, content=m.content) for m in history]
    request_id = getattr(request.state, "request_id", None)
    egress_base = EgressRecord(
        provider=provider.name,
        model=settings.model,
        conversation_id=payload.conversation_id,
        message_count=len(chat_messages),
        char_count=sum(len(m.content) for m in chat_messages),
    )
    record_egress(egress_base, request_id=request_id)

    async def event_stream() -> AsyncIterator[str]:
        """生成 SSE 事件流。

        终态用单一变量记录，并在 finally 中兜底写出审计记录。
        实测发现：用户取消时生成器被关闭，若只在正常分支记录，
        审计里就只剩 "started"，事后无法判断这次外发是否完成。
        """
        collected: list[str] = []
        outcome = "aborted"
        error_code: str | None = None

        try:
            async for chunk in provider.stream_chat(chat_messages, settings.model):
                collected.append(chunk.delta)
                yield _sse("delta", {"text": chunk.delta})

            reply = "".join(collected).strip()
            if not reply:
                outcome, error_code = "failed", "EMPTY_RESPONSE"
                yield _sse("error", {"code": "EMPTY_RESPONSE", "message": "模型没有返回内容。"})
                return

            # 助手消息入库。role 由服务端写入，前端无法伪造（§8.4）。
            stored = messages.append(payload.conversation_id, reply, role="assistant")
            conversations.touch(conversation)
            outcome = "completed"
            yield _sse("done", {"message_id": stored.id})
        except ProviderError as exc:
            outcome, error_code = "failed", exc.code
            yield _sse("error", {"code": exc.code, "message": str(exc)})
        except Exception:
            logger.exception("模型流式调用异常", extra={"conversation_id": payload.conversation_id})
            outcome, error_code = "failed", "INTERNAL_ERROR"
            yield _sse("error", {"code": "INTERNAL_ERROR", "message": "生成失败，请稍后重试。"})
        finally:
            record_egress(_with_outcome(egress_base, outcome, error_code), request_id=request_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            # 禁止代理/浏览器缓存流式响应。
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _with_outcome(base: EgressRecord, outcome: str, error_code: str | None = None) -> EgressRecord:
    return EgressRecord(
        provider=base.provider,
        model=base.model,
        conversation_id=base.conversation_id,
        message_count=base.message_count,
        char_count=base.char_count,
        outcome=outcome,
        error_code=error_code,
    )
