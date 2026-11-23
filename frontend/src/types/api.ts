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
