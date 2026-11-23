import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { MemoryItem } from '../types/api'

export type MemoryFilter = 'PENDING' | 'APPROVED' | 'REJECTED'

export interface MemoryState {
  items: MemoryItem[]
  total: number
  pendingCount: number
  loading: boolean
  loadError: string | null
}

/**
 * 记忆中心的状态编排。
 *
 * 约束：
 * - 列表以服务端为准；任何写操作成功后重新拉取，避免本地状态漂移。
 * - 用 generation 丢弃过期响应（切换筛选、连续操作）。
 * - 组件卸载后不 setState。
 */
export function useMemories(filter: MemoryFilter) {
  const [state, setState] = useState<MemoryState>({
    items: [],
    total: 0,
    pendingCount: 0,
    loading: true,
    loadError: null,
  })
  const [actionError, setActionError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)

  const statusParam = filter === 'PENDING' ? 'PROPOSED' : filter

  const reload = useCallback(async () => {
    const current = ++generation.current
    setState((prev) => ({ ...prev, loading: true, loadError: null }))

    const result = await api.memory.list(statusParam)
    if (!mounted.current || current !== generation.current) return

    if (result.ok) {
      setState({
        items: result.data.items,
        total: result.data.total,
        pendingCount: result.data.pending_count,
        loading: false,
        loadError: null,
      })
      return
    }
    setState((prev) => ({ ...prev, loading: false, loadError: result.message }))
  }, [statusParam])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void reload()
    return () => {
      mounted.current = false
    }
  }, [reload])

  const act = useCallback(
    async (
      id: string,
      action: () => Promise<{ ok: boolean; message?: string }>,
    ): Promise<boolean> => {
      setBusyId(id)
      setActionError(null)
      const result = await action()
      if (!mounted.current) return result.ok

      if (!result.ok) {
        setActionError(result.message ?? '操作失败。')
        setBusyId(null)
        return false
      }
      await reload()
      if (mounted.current) setBusyId(null)
      return true
    },
    [reload],
  )

  return {
    state,
    actionError,
    busyId,
    reload,
    clearActionError: () => setActionError(null),
    approve: (id: string) => act(id, () => api.memory.approve(id)),
    reject: (id: string) => act(id, () => api.memory.reject(id)),
    update: (id: string, content: string) => act(id, () => api.memory.update(id, content)),
    remove: (id: string) => act(id, () => api.memory.remove(id)),
  }
}
