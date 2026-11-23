/**
 * 后端 API 客户端。
 *
 * 设计约束（AGENT.md §4.1、§7.1）：
 * - 前端不直接持有数据库或模型逻辑，只通过受控 API 通信。
 * - 不在浏览器保存任何秘密。
 * - 错误统一为 Result 类型，把后端的安全错误文案原样交给 UI，
 *   同时保留 request_id 以便报障时与服务端日志对齐。
 */

import type {
  ActivityList,
  ApiErrorBody,
  Approval,
  ApprovalList,
  Conversation,
  ConversationList,
  HealthResponse,
  Job,
  JobDetail,
  JobList,
  MemoryItem,
  MemoryList,
  Message,
  MessageList,
  Result,
} from '../types/api'

const API_PREFIX = '/api/v1'

/** 请求超时。健康检查必须快速失败，避免界面长时间转圈。 */
const DEFAULT_TIMEOUT_MS = 8_000

/** 代理/网关层表示"后端不可达"的状态码。 */
const GATEWAY_STATUS = new Set([502, 503, 504])

const JSON_HEADERS = { 'Content-Type': 'application/json' }

function isApiErrorBody(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null) return false
  const maybe = (value as { error?: unknown }).error
  return (
    typeof maybe === 'object' &&
    maybe !== null &&
    typeof (maybe as { code?: unknown }).code === 'string' &&
    typeof (maybe as { message?: unknown }).message === 'string'
  )
}

async function request<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<Result<T>> {
  const { timeoutMs = DEFAULT_TIMEOUT_MS, ...rest } = init
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeoutMs)

  try {
    const response = await fetch(`${API_PREFIX}${path}`, {
      ...rest,
      signal: controller.signal,
      headers: {
        Accept: 'application/json',
        ...rest.headers,
      },
    })

    const requestId = response.headers.get('X-Request-ID') ?? ''

    if (!response.ok) {
      let body: unknown = null
      let bodyIsJson = true
      try {
        body = await response.json()
      } catch {
        // 后端异常时可能返回非 JSON，使用兜底文案。
        bodyIsJson = false
      }

      if (isApiErrorBody(body)) {
        return {
          ok: false,
          code: body.error.code,
          message: body.error.message,
          ...(body.error.request_id || requestId
            ? { requestId: body.error.request_id || requestId }
            : {}),
        }
      }

      // 网关类状态码（502/503/504）且响应体不是我们的错误结构，通常意味着
      // 开发代理连不上后端，或后端进程已退出。此时对用户说"无法连接"才准确。
      if (GATEWAY_STATUS.has(response.status) && !bodyIsJson) {
        return { ok: false, code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' }
      }

      return {
        ok: false,
        code: `HTTP_${response.status}`,
        message: '服务返回了未预期的响应。',
        ...(requestId ? { requestId } : {}),
      }
    }

    // 204 No Content（删除接口）没有响应体，不能调用 response.json()。
    if (response.status === 204) {
      return { ok: true, data: undefined as T }
    }

    const data = (await response.json()) as T
    return { ok: true, data }
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') {
      return { ok: false, code: 'TIMEOUT', message: '请求超时，后端可能未启动。' }
    }
    return { ok: false, code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' }
  } finally {
    clearTimeout(timer)
  }
}

export const api = {
  health: () => request<HealthResponse>('/health'),

  conversations: {
    list: (limit = 50, offset = 0) =>
      request<ConversationList>(`/conversations?limit=${limit}&offset=${offset}`),

    get: (id: string) => request<Conversation>(`/conversations/${encodeURIComponent(id)}`),

    create: (title: string) =>
      request<Conversation>('/conversations', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ title }),
      }),

    rename: (id: string, title: string) =>
      request<Conversation>(`/conversations/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: JSON_HEADERS,
        body: JSON.stringify({ title }),
      }),

    remove: (id: string) =>
      request<void>(`/conversations/${encodeURIComponent(id)}`, { method: 'DELETE' }),

    messages: {
      list: (conversationId: string, limit = 200, offset = 0) =>
        request<MessageList>(
          `/conversations/${encodeURIComponent(conversationId)}/messages` +
            `?limit=${limit}&offset=${offset}`,
        ),

      // 后端只接受 role="user"，这里也不发送 role，避免前端与后端契约漂移。
      append: (conversationId: string, content: string) =>
        request<Message>(`/conversations/${encodeURIComponent(conversationId)}/messages`, {
          method: 'POST',
          headers: JSON_HEADERS,
          body: JSON.stringify({ content }),
        }),
    },
  },

  jobs: {
    list: (status?: string) => {
      const params = new URLSearchParams()
      if (status) params.set('status', status)
      const qs = params.toString()
      return request<JobList>(`/jobs${qs ? `?${qs}` : ''}`)
    },

    get: (id: string) => request<JobDetail>(`/jobs/${encodeURIComponent(id)}`),

    cancel: (id: string) =>
      request<Job>(`/jobs/${encodeURIComponent(id)}/cancel`, { method: 'POST' }),

    retry: (id: string) =>
      request<Job>(`/jobs/${encodeURIComponent(id)}/retry`, { method: 'POST' }),
  },

  activity: {
    list: (limit = 50) => request<ActivityList>(`/activity?limit=${limit}`),
  },

  approvals: {
    list: (status?: string) => {
      const params = new URLSearchParams()
      if (status) params.set('status', status)
      const qs = params.toString()
      return request<ApprovalList>(`/approvals${qs ? `?${qs}` : ''}`)
    },

    decide: (
      id: string,
      decision: 'approve' | 'reject',
      options: { rememberForSession?: boolean; sessionToken?: string } = {},
    ) =>
      request<Approval>(`/approvals/${encodeURIComponent(id)}/decide`, {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({
          decision,
          remember_for_session: options.rememberForSession ?? false,
          session_token: options.sessionToken ?? null,
        }),
      }),
  },

  memory: {
    list: (status?: string, limit = 100, offset = 0) => {
      const params = new URLSearchParams({ limit: String(limit), offset: String(offset) })
      if (status) params.set('status', status)
      return request<MemoryList>(`/memory?${params.toString()}`)
    },

    approve: (id: string) =>
      request<MemoryItem>(`/memory/${encodeURIComponent(id)}/approve`, { method: 'POST' }),

    reject: (id: string) =>
      request<MemoryItem>(`/memory/${encodeURIComponent(id)}/reject`, { method: 'POST' }),

    update: (id: string, content: string) =>
      request<MemoryItem>(`/memory/${encodeURIComponent(id)}`, {
        method: 'PATCH',
        headers: JSON_HEADERS,
        body: JSON.stringify({ content }),
      }),

    remove: (id: string) =>
      request<void>(`/memory/${encodeURIComponent(id)}`, { method: 'DELETE' }),
  },
}
