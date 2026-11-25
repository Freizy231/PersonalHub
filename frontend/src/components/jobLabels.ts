/** 作业相关的展示文案。与组件分开，避免影响 Fast Refresh。 */

export const JOB_STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '执行中',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
}

/**
 * 内部作业类型 -> 用户可读名称。
 *
 * 界面主标题不再显示 `generate_app` 这类内部标识（审计 P1：两条作业
 * 都是 generate_app，用户无法区分）。未知类型退回原名，但明确标为
 * "内部标识"，避免误认为这是产品里的功能名。
 */
export const JOB_TYPE_LABEL: Record<string, string> = {
  generate_app: '生成应用',
  noop: '连通性测试',
}

export function jobTypeLabel(jobType: string): string {
  return JOB_TYPE_LABEL[jobType] ?? jobType
}

/** 作业阶段（来自服务端事件）-> 人话。只翻译已知阶段。 */
export const STAGE_LABEL: Record<string, string> = {
  enqueued: '已排队',
  started: '开始执行',
  progress: '生成中',
  retrying: '准备重试',
  interrupted: '被中断',
  recovered: '已恢复',
  succeeded: '已完成',
  failed: '失败',
  cancelled: '已取消',
}

export function stageLabel(eventType: string): string {
  return STAGE_LABEL[eventType] ?? eventType
}

export const EVENT_LABEL: Record<string, string> = {
  enqueued: '入队',
  started: '开始',
  progress: '进行中',
  succeeded: '成功',
  failed: '失败',
  cancelled: '取消',
  retrying: '重试',
  interrupted: '被中断',
  recovered: '已恢复',
}

const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED'])

export function isTerminal(status: string): boolean {
  return TERMINAL.has(status)
}

export function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'medium' })
}

/** 相对时间，用于"最近更新"这类信息（避免每次都要读绝对时间）。 */
export function formatRelative(iso: string, now: number = Date.now()): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  const seconds = Math.round((now - date.getTime()) / 1000)
  if (seconds < 60) return '刚刚'
  const minutes = Math.round(seconds / 60)
  if (minutes < 60) return `${minutes} 分钟前`
  const hours = Math.round(minutes / 60)
  if (hours < 24) return `${hours} 小时前`
  const days = Math.round(hours / 24)
  if (days < 30) return `${days} 天前`
  return date.toLocaleDateString('zh-CN')
}
