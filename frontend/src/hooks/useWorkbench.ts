import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Approval, Conversation, Job, UserTask } from '../types/api'

export interface WorkbenchState {
  loading: boolean
  /** 每个模块各自的错误，互不覆盖：一个模块失败不该让整页空白。 */
  errors: Partial<Record<ModuleKey, string>>
  conversations: Conversation[]
  pendingApprovals: Approval[]
  openTasks: UserTask[]
  jobs: Job[]
}

export type ModuleKey = 'conversations' | 'approvals' | 'tasks' | 'jobs'

const EMPTY: WorkbenchState = {
  loading: true,
  errors: {},
  conversations: [],
  pendingApprovals: [],
  openTasks: [],
  jobs: [],
}

/** 首页各模块最多展示几条；完整列表在各自页面。 */
export const WORKBENCH_LIMIT = 3

/**
 * 首页工作台的数据编排。
 *
 * 设计取舍：
 * - 四个模块**并行**请求、**独立**报错。任一接口失败只影响自己的卡片，
 *   用户仍然能看到其余可用信息（不出现"整页加载失败"）。
 * - 只取最小列表并在前端截断：首页是概览，不是列表页。
 * - 有进行中的作业时轮询，让"AI 正在做什么"保持真实。
 */
export function useWorkbench() {
  const [state, setState] = useState<WorkbenchState>(EMPTY)
  const mounted = useRef(true)
  const generation = useRef(0)

  const load = useCallback(async (showLoading: boolean) => {
    const current = ++generation.current
    if (showLoading) setState((prev) => ({ ...prev, loading: true }))

    const [conversations, approvals, tasks, jobs] = await Promise.all([
      api.conversations.list(5, 0),
      api.approvals.list('PENDING'),
      api.userTasks.list('OPEN'),
      api.jobs.list(),
    ])
    if (!mounted.current || current !== generation.current) return

    const errors: WorkbenchState['errors'] = {}
    if (!conversations.ok) errors.conversations = conversations.message
    if (!approvals.ok) errors.approvals = approvals.message
    if (!tasks.ok) errors.tasks = tasks.message
    if (!jobs.ok) errors.jobs = jobs.message

    setState({
      loading: false,
      errors,
      conversations: conversations.ok ? conversations.data.items : [],
      pendingApprovals: approvals.ok ? approvals.data.items : [],
      openTasks: tasks.ok ? tasks.data.items : [],
      // 首页只关心"正在跑"和"最近失败"的作业；已完成的放"后台作业"页。
      jobs: jobs.ok
        ? jobs.data.items.filter((job) => job.status !== 'SUCCEEDED' && job.status !== 'CANCELLED')
        : [],
    })
  }, [])

  useEffect(() => {
    mounted.current = true
    // oxlint-disable-next-line react/set-state-in-effect
    void load(true)
    return () => {
      mounted.current = false
    }
  }, [load])

  // 有排队/执行中的作业时轮询，让"AI 正在做什么"不是过期快照。
  useEffect(() => {
    const active = state.jobs.some((job) => job.status === 'PENDING' || job.status === 'RUNNING')
    if (!active) return
    const timer = setInterval(() => {
      void load(false)
    }, 3000)
    return () => clearInterval(timer)
  }, [state.jobs, load])

  return { state, reload: () => void load(false) }
}
