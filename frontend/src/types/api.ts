/**
 * 后端 API 类型定义。
 *
 * 与后端 Pydantic 模型保持可验证一致（AGENT.md §9.1）。
 * M1 只有健康检查；后续可从 OpenAPI 生成，避免手工漂移。
 */

export interface HealthResponse {
  status: 'ok'
  service: string
  version: string
  timestamp: string
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
