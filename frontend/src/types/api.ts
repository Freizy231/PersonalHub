/**
 * 后端 API 类型定义。
 *
 * 与后端 Pydantic 模型保持可验证一致（AGENT.md §9.1）。
 * 后续可从 OpenAPI 生成，避免手工漂移。
 */

export interface HealthResponse {
  status: 'ok'
  service: string
  version: string
  timestamp: string
}

/** 单条消息的字符上限。与后端 `models.message.MAX_CONTENT_LENGTH` 保持一致。
 *
 * 放在共享类型里而不是某个组件内：前端有多处需要校验它，
 * 分散硬编码会漂移（D-012）。
 */
export const MESSAGE_MAX_LENGTH = 100_000

/** 与后端 ConversationOut 对应。时间为 ISO 8601 + Z（UTC）。 */
export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationList {
  items: Conversation[]
  total: number
  limit: number
  offset: number
}

export interface ApiErrorBody {
  error: {
    code: string
    message: string
    request_id: string
    details?: unknown
  }
}

/** 前端展示用的统一结果类型，避免在各组件里重复处理异常。 */
export type Result<T> =
  | { ok: true; data: T }
  | { ok: false; message: string; code: string; requestId?: string }

/** 与后端 MessageOut 对应。时间同样为 ISO 8601 + Z。 */
export interface ToolInvocationSummary {
  tool: string
  status: string
  error_code: string | null
}

export interface Message {
  id: string
  conversation_id: string
  role: string
  content: string
  created_at: string
  /** 该回合调用过的工具（仅助手消息可能有值）。 */
  tools?: ToolInvocationSummary[]
}

export interface MessageList {
  items: Message[]
  total: number
  limit: number
  offset: number
}

/** 与后端 MemoryOut 对应（M4）。敏感级别当前只展示，不阻断外发。 */
export interface MemorySource {
  source_type: string
  source_id: string | null
  excerpt: string
}

export interface MemoryItem {
  id: string
  content: string
  status: 'PROPOSED' | 'APPROVED' | 'REJECTED' | 'DELETED'
  kind: 'SEMANTIC' | 'EPISODIC' | 'PROCEDURAL'
  sensitivity: 'PUBLIC' | 'PERSONAL' | 'SENSITIVE' | 'SECRET'
  confidence: number
  created_at: string
  updated_at: string
  sources: MemorySource[]
}

export interface MemoryList {
  items: MemoryItem[]
  total: number
  limit: number
  offset: number
  pending_count: number
}

/** 审批记录（M6）。 */
export interface Approval {
  id: string
  tool_name: string
  arguments_preview: string
  status: 'PENDING' | 'APPROVED' | 'DENIED' | 'EXPIRED'
  conversation_id: string | null
  expires_at: string
  decided_at: string | null
  consumed: number
}

export interface ApprovalList {
  items: Approval[]
  total: number
}

/** 对话中出现的审批请求（来自 SSE approval_required 事件）。
 *
 * summary / title / arguments 是给人看的（D-012）：
 * - title：中文工具名，例如"创建任务"；
 * - summary：逐项的「中文标签 + 值」，界面直接分行显示；
 * - arguments：原始参数，只放在"查看原始参数"的折叠区里备查。
 * preview 保留是为了兼容只显示纯文本的旧客户端。
 */
export interface ApprovalSummaryItem {
  label: string
  value: string
}

export interface ApprovalRequestEvent {
  approval_id: string
  tool: string
  title?: string
  summary?: ApprovalSummaryItem[]
  arguments?: Record<string, unknown>
  preview: string
  expires_in: number
}

/** 作业（M7）。 */
export interface Job {
  id: string
  job_type: string
  status: 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'
  attempts: number
  max_attempts: number
  last_error: string | null
  available_at: string
  created_at: string
  updated_at: string
}

export interface TaskEventItem {
  id: string
  job_id: string
  run_id: string | null
  event_type: string
  message: string
  created_at: string
}

export interface TaskRunItem {
  id: string
  attempt: number
  status: string
  started_at: string
  finished_at: string | null
  error_code: string | null
}

export interface JobDetail extends Job {
  runs: TaskRunItem[]
  events: TaskEventItem[]
}

export interface JobList {
  items: Job[]
  total: number
}

export interface ActivityList {
  items: TaskEventItem[]
  total: number
}

/** 能力声明（M8）。字段与后端 CapabilityOut 对应。
 *
 * 注意：permissions 只是**声明**，不代表已获授权（见 docs/DECISIONS.md D-014）。
 */
export interface Capability {
  id: string
  name: string
  version: string
  type: string
  description: string
  input_schema: Record<string, unknown>
  output_schema: Record<string, unknown>
  runtime: string
  permissions: string[]
  dependencies: string[]
  compatibility: string
  source: string
  signature: string | null
}

export interface CapabilityList {
  items: Capability[]
  total: number
  notice: string
}

export interface DependencyHealth {
  missing: Record<string, string[]>
  cycle: string[]
  healthy: boolean
}

/** MCP Server 状态（M8）。工具来自外部服务，全部需逐次确认。 */
export interface McpServer {
  name: string
  transport: string
  enabled: boolean
  command: string
  status: 'ready' | 'unavailable' | 'disabled' | string
  tool_count: number
  tools: string[]
  reason: string | null
}

export interface McpServerList {
  items: McpServer[]
  total: number
  errors: string[]
  notice: string
}

/** 插件（M8）。本期为声明式：不含可执行代码。 */
export interface PluginTool {
  name: string
  display_name: string
  description: string
  risk_hint: string
  composes: string[]
}

export interface Plugin {
  id: string
  name: string
  version: string
  type: string
  description: string
  enabled: boolean
  installed_at: string
  permissions: string[]
  dependencies: string[]
  tools: PluginTool[]
  error: string | null
}

export interface PluginList {
  items: Plugin[]
  total: number
  notice: string
}

export interface PluginPreview {
  id: string
  name: string
  version: string
  description: string
  permissions: string[]
  dependencies: string[]
  tools: PluginTool[]
  warnings: string[]
}
