/** 工具的中文展示名与状态文案。
 *
 * 原先在 MessageList / ToolActivityList / ApprovalCard 各写一份，改一处漏两处。
 * 统一放在这里，且**优先使用后端给的中文名**（工具自己声明 display_name），
 * 这里的表只作为旧数据与未声明工具的兜底。
 */

export const TOOL_LABEL: Record<string, string> = {
  search_memories: '检索记忆',
  list_tasks: '查看任务',
  create_task: '创建任务',
}

export const TOOL_STATUS_LABEL: Record<string, string> = {
  ok: '完成',
  denied: '已被拒绝',
  error: '失败',
  running: '执行中',
}

/** 后端状态值收敛到四种展示状态，未知值按失败处理（不假装成功）。 */
export function normalizeToolStatus(status: string): 'running' | 'ok' | 'denied' | 'error' {
  if (status === 'running' || status === 'ok' || status === 'denied') return status
  if (status === 'error') return 'error'
  return 'error'
}

export function toolLabel(name: string, displayName?: string): string {
  return displayName || TOOL_LABEL[name] || name
}
