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
  AppList,
  GenerateAccepted,
  InstallResult,
  ApiErrorBody,
  Approval,
  ApprovalList,
  Capability,
  CapabilityList,
  Conversation,
  ConversationList,
  DependencyHealth,
  HealthResponse,
  Job,
  JobDetail,
  JobList,
  McpServerList,
  MemoryItem,
  MemoryList,
  Message,
  Plugin,
  PluginList,
  PluginPreview,
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
      /**
       * 列出消息。
       *
       * order='desc' 表示"从最新一条往回取"，items 仍按时间正序返回。
       * 会话详情页必须用它：固定 offset=0 只能看到最早的 200 条，
       * 超长会话里最新的消息反而看不见（D-012）。
       */
      list: (conversationId: string, limit = 200, offset = 0, order: 'asc' | 'desc' = 'asc') =>
        request<MessageList>(
          `/conversations/${encodeURIComponent(conversationId)}/messages` +
            `?limit=${limit}&offset=${offset}&order=${order}`,
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

  /** 能力声明（M8）。只读：安装与调用需要单独的授权流程，不在此暴露。 */
  plugins: {
    list: () => request<PluginList>('/plugins'),

    /** 安装前预览：来源、权限、依赖与风险（§8.5）。 */
    preview: (directory: string) =>
      request<PluginPreview>(`/plugins/preview?directory=${encodeURIComponent(directory)}`),

    install: (directory: string) =>
      request<Plugin>('/plugins/install', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ directory }),
      }),

    enable: (id: string) =>
      request<Plugin>(`/plugins/${encodeURIComponent(id)}/enable`, { method: 'POST' }),

    disable: (id: string) =>
      request<Plugin>(`/plugins/${encodeURIComponent(id)}/disable`, { method: 'POST' }),

    remove: (id: string) =>
      request<{ ok: boolean; message: string }>(`/plugins/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),
  },

  mcp: {
    /** 只读：MCP Server 的配置与状态。增删改需直接编辑配置文件并重启。 */
    servers: () => request<McpServerList>('/mcp/servers'),
  },

  /**
   * 生成的 Personal App（M9）。
   *
   * 预览与运行都通过 iframe 加载 `/apps/.../content`：那条路径带
   * CSP `default-src 'none'` 等安全响应头，是应用唯一的运行方式。
   * 这里刻意不取 HTML 文本再塞进 srcDoc——reason 见 AppsPage 注释。
   */
  apps: {
    list: () => request<AppList>('/apps'),

    /**
     * 提交生成作业。**立即返回**（毫秒级），不等待模型。
     *
     * 刻意不设长超时：接口已经不再耗时，长超时只会掩盖真正的问题。
     */
    /**
     * 提交生成作业。
     *
     * `force=false` 时，后端发现相似应用会**跳过生成**并返回 similar 清单，
     * 由用户决定"用已有的"还是"仍然重新生成"。
     */
    generate: (prompt: string, force = false) =>
      request<GenerateAccepted>('/apps/generate', {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify({ request: prompt, force }),
      }),

    staged: (stagingId: string) =>
      request<{ staging_id: string; html: string; title: string; size_bytes: number }>(
        `/apps/staging/${encodeURIComponent(stagingId)}`,
      ),

    install: (stagingId: string) =>
      request<InstallResult>(`/apps/staging/${encodeURIComponent(stagingId)}/install`, {
        method: 'POST',
      }),

    discard: (stagingId: string) =>
      request<{ ok: boolean; message: string }>(
        `/apps/staging/${encodeURIComponent(stagingId)}`,
        { method: 'DELETE' },
      ),

    rollback: (id: string) =>
      request<InstallResult>(`/apps/${encodeURIComponent(id)}/rollback`, { method: 'POST' }),

    remove: (id: string) =>
      request<{ ok: boolean; message: string }>(`/apps/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      }),

    /** iframe 的 src。不要 fetch 它再把结果塞进 srcDoc——见 AppsPage 注释。 */
    contentUrl: (id: string) => `${API_PREFIX}/apps/${encodeURIComponent(id)}/content`,

    stagingContentUrl: (stagingId: string) =>
      `${API_PREFIX}/apps/staging/${encodeURIComponent(stagingId)}/content`,
  },

  capabilities: {
    list: (options: { q?: string; type?: string; source?: string } = {}) => {
      const params = new URLSearchParams()
      if (options.q) params.set('q', options.q)
      if (options.type) params.set('type', options.type)
      if (options.source) params.set('source', options.source)
      const qs = params.toString()
      return request<CapabilityList>(`/capabilities${qs ? `?${qs}` : ''}`)
    },

    get: (id: string) => request<Capability>(`/capabilities/${encodeURIComponent(id)}`),

    versions: (id: string) =>
      request<CapabilityList>(`/capabilities/${encodeURIComponent(id)}/versions`),

    dependencyHealth: () => request<DependencyHealth>('/capabilities/health/deps'),
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
