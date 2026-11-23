/**
 * SSE 流式读取。
 *
 * 为什么不用 EventSource：它只支持 GET，无法发送消息正文；
 * 用 POST + fetch 读取响应流更安全，也便于携带取消信号。
 */

import { getSessionToken } from './session'

export interface StreamHandlers {
  onDelta: (text: string) => void
  /** 工具调用的开始与结束。让用户看得见 AI 在做什么（AGENT.md §7.4 可理解摘要）。 */
  onToolStart?: (payload: { id: string; name: string }) => void
  onToolEnd?: (payload: { id: string; name: string; status: string; error_code?: string }) => void
  /** 需要用户审批。收到后对话会暂停，直到调用 approvals.decide。 */
  onApprovalRequired?: (payload: {
    approval_id: string
    tool: string
    preview: string
    expires_in: number
  }) => void
  onDone: (payload: { message_id: string }) => void
  onError: (payload: { code: string; message: string }) => void
}

/** 单条 SSE 帧解析结果。 */
interface SseFrame {
  event: string
  data: string
}

/** 把缓冲区按空行切分为完整帧，返回 [帧列表, 剩余未完成内容]。 */
function splitFrames(buffer: string): [SseFrame[], string] {
  const frames: SseFrame[] = []
  // SSE 帧之间用空行分隔；兼容 \r\n。
  const normalized = buffer.replace(/\r\n/g, '\n')
  const parts = normalized.split('\n\n')
  const rest = parts.pop() ?? ''

  for (const part of parts) {
    let event = 'message'
    const dataLines: string[] = []
    for (const line of part.split('\n')) {
      if (line.startsWith('event:')) event = line.slice('event:'.length).trim()
      else if (line.startsWith('data:')) dataLines.push(line.slice('data:'.length).trim())
    }
    if (dataLines.length > 0) frames.push({ event, data: dataLines.join('\n') })
  }
  return [frames, rest]
}

export async function streamChat(
  conversationId: string,
  content: string,
  handlers: StreamHandlers,
  signal: AbortSignal,
): Promise<void> {
  let response: Response
  try {
    response = await fetch('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Accept: 'text/event-stream',
        // 会话令牌：后端据此判断该浏览器会话是否勾选过"不再询问"。
        'X-Session-Token': getSessionToken(),
      },
      body: JSON.stringify({ conversation_id: conversationId, content }),
      signal,
    })
  } catch {
    if (signal.aborted) return
    handlers.onError({ code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' })
    return
  }

  if (!response.ok || !response.body) {
    // 非 200：后端已给出结构化错误（例如未配置 API Key）。
    try {
      const body = await response.json()
      const message = body?.error?.message ?? '请求失败。'
      handlers.onError({ code: body?.error?.code ?? `HTTP_${response.status}`, message })
    } catch {
      handlers.onError({ code: `HTTP_${response.status}`, message: '请求失败。' })
    }
    return
  }

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    for (;;) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const [frames, rest] = splitFrames(buffer)
      buffer = rest

      for (const frame of frames) {
        let payload: unknown
        try {
          payload = JSON.parse(frame.data)
        } catch {
          continue
        }
        if (frame.event === 'delta') {
          const text = (payload as { text?: unknown }).text
          if (typeof text === 'string') handlers.onDelta(text)
        } else if (frame.event === 'approval_required') {
          handlers.onApprovalRequired?.(
            payload as { approval_id: string; tool: string; preview: string; expires_in: number },
          )
        } else if (frame.event === 'tool_start') {
          handlers.onToolStart?.(payload as { id: string; name: string })
        } else if (frame.event === 'tool_end') {
          handlers.onToolEnd?.(
            payload as { id: string; name: string; status: string; error_code?: string },
          )
        } else if (frame.event === 'done') {
          handlers.onDone(payload as { message_id: string })
        } else if (frame.event === 'error') {
          handlers.onError(payload as { code: string; message: string })
        }
      }
    }
  } catch {
    // 用户主动取消不算错误；其余中断提示网络问题。
    if (!signal.aborted) {
      handlers.onError({ code: 'STREAM_INTERRUPTED', message: '连接中断，回复可能不完整。' })
    }
  } finally {
    reader.releaseLock()
  }
}
