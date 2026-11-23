import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Job } from '../types/api'

export type JobFilter = 'ALL' | 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED' | 'CANCELLED'

export interface JobsState {
  items: Job[]
  total: number
  loading: boolean
  loadError: string | null
}

/**
 * 作业列表状态编排。
 *
 * 自动刷新：作业状态由后台 Worker 改变，前端不主动轮询就看不到进展。
 * 只在有进行中的作业时轮询，避免空闲时无谓请求。
 */
export function useJobs(filter: JobFilter) {
  const [state, setState] = useState<JobsState>({
    items: [],
    total: 0,
    loading: true,
    loadError: null,
  })
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)

  const reload = useCallback(
    async (showLoading = true) => {
      const current = ++generation.current
      if (showLoading) {
        setState((prev) => ({ ...prev, loading: true, loadError: null }))
      }

      const result = await api.jobs.list(filter === 'ALL' ? undefined : filter)
      if (!mounted.current || current !== generation.current) return

      if (result.ok) {
        setState({
          items: result.data.items,
          total: result.data.total,
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
    // oxlint-disable-next-line react/set-state-in-effect
    void reload()
    return () => {
      mounted.current = false
    }
  }, [reload])

  // 有进行中的作业时轮询（后台 Worker 在推进状态）。
  useEffect(() => {
    const hasActive = state.items.some(
      (job) => job.status === 'PENDING' || job.status === 'RUNNING',
    )
    if (!hasActive) return

    const timer = setInterval(() => {
      void reload(false)
    }, 2000)
    return () => clearInterval(timer)
  }, [state.items, reload])

  const act = useCallback(
    async (id: string, action: () => Promise<{ ok: boolean; message?: string }>) => {
      setBusyId(id)
      setActionError(null)
      const result = await action()
      if (!mounted.current) return
      if (!result.ok) {
        setActionError(result.message ?? '操作失败。')
      } else {
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
    reload,
    clearActionError: () => setActionError(null),
    cancel: (id: string) => act(id, () => api.jobs.cancel(id)),
    retry: (id: string) => act(id, () => api.jobs.retry(id)),
  }
}
