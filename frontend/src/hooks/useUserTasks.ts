import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { UserTask } from '../types/api'

export type UserTaskFilter = 'ALL' | 'OPEN' | 'DONE'

export interface UserTasksState {
  items: UserTask[]
  total: number
  openCount: number
  loading: boolean
  loadError: string | null
}

/**
 * 用户任务（`tasks` 表）的状态编排。
 *
 * 与 `useJobs` 刻意分开：两者是不同的实体（D-013），
 * 状态取值、操作（完成/重开 vs 取消/重试）与数据来源都不同。
 * 合并会重新制造"作业冒充任务"的问题。
 */
export function useUserTasks(filter: UserTaskFilter) {
  const [state, setState] = useState<UserTasksState>({
    items: [],
    total: 0,
    openCount: 0,
    loading: true,
    loadError: null,
  })
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [confirmingDelete, setConfirmingDelete] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)

  const reload = useCallback(
    async (showLoading = true) => {
      const current = ++generation.current
      if (showLoading) {
        setState((prev) => ({ ...prev, loading: true, loadError: null }))
      }

      const status = filter === 'ALL' ? undefined : filter
      const result = await api.userTasks.list(status)
      if (!mounted.current || current !== generation.current) return

      if (result.ok) {
        setState({
          items: result.data.items,
          total: result.data.total,
          openCount: result.data.open_count,
          loading: false,
          loadError: null,
        })
      } else {
        setState((prev) => ({ ...prev, loading: false, loadError: result.message }))
      }
    },
    [filter],
  )

  useEffect(() => {
    mounted.current = true
    // 与外部系统（后端）同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void reload()
    return () => {
      mounted.current = false
    }
  }, [reload])

  const act = useCallback(
    async (id: string, action: () => Promise<{ ok: boolean; message?: string }>) => {
      setBusyId(id)
      setActionError(null)
      const result = await action()
      if (!mounted.current) return
      if (!result.ok) {
        setActionError(result.message ?? '操作失败。')
      } else {
        setConfirmingDelete(null)
        await reload(false)
      }
      if (mounted.current) setBusyId(null)
    },
    [reload],
  )

  return {
    state,
    actionError,
    busyId,
    confirmingDelete,
    reload,
    clearActionError: () => setActionError(null),
    requestDelete: (id: string) => setConfirmingDelete(id),
    cancelDelete: () => setConfirmingDelete(null),
    complete: (id: string) => act(id, () => api.userTasks.complete(id)),
    reopen: (id: string) => act(id, () => api.userTasks.reopen(id)),
    remove: (id: string) => act(id, () => api.userTasks.remove(id)),
  }
}
