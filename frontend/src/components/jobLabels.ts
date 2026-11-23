/** 作业相关的展示文案。与组件分开，避免影响 Fast Refresh。 */

export const JOB_STATUS_LABEL: Record<string, string> = {
  PENDING: '排队中',
  RUNNING: '执行中',
  SUCCEEDED: '已完成',
  FAILED: '失败',
  CANCELLED: '已取消',
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
