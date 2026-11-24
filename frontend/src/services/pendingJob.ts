/**
 * 记住"我刚提交的生成作业"。
 *
 * 生成现在跑在后台，耗时十几秒到一分钟。用户手机切换应用、
 * 或者不小心刷新页面，都不该丢掉这次生成——作业本身在服务端照常跑完。
 *
 * 存两份：
 * - 模块内变量：页面内切换路由时不需要碰存储；
 * - sessionStorage：刷新后还能接上（按标签页存活，关闭即失效，
 *   与 D-010 的会话令牌策略一致）。
 *
 * 只存 job id 与 staging id，**不存用户描述**——那是隐私内容，
 * 没必要为了恢复状态而多存一份。
 */

const KEY = 'ph.apps.pending-job'

export interface PendingJob {
  jobId: string
  stagingId: string
  /** 提交时间戳（毫秒）。用于判断是否已经太久，避免无限恢复。 */
  submittedAt: number
}

/** 超过这个时长的待恢复作业视为过期（与服务端轮询上限一致）。 */
const MAX_AGE_MS = 10 * 60 * 1000

export function rememberPendingJob(job: Omit<PendingJob, 'submittedAt'>): void {
  const value: PendingJob = { ...job, submittedAt: Date.now() }
  try {
    sessionStorage.setItem(KEY, JSON.stringify(value))
  } catch {
    // 隐私模式下存储可能不可用：功能降级为"刷新后不恢复"，不影响主流程。
  }
}

export function forgetPendingJob(): void {
  try {
    sessionStorage.removeItem(KEY)
  } catch {
    /* 同上 */
  }
}

export function loadPendingJob(): PendingJob | null {
  try {
    const raw = sessionStorage.getItem(KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw) as Partial<PendingJob>
    if (
      typeof parsed.jobId !== 'string' ||
      typeof parsed.stagingId !== 'string' ||
      typeof parsed.submittedAt !== 'number'
    ) {
      return null
    }
    if (Date.now() - parsed.submittedAt > MAX_AGE_MS) {
      forgetPendingJob()
      return null
    }
    return { jobId: parsed.jobId, stagingId: parsed.stagingId, submittedAt: parsed.submittedAt }
  } catch {
    return null
  }
}
