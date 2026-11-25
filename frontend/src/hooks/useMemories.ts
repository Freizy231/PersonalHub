import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { MemoryItem } from '../types/api'

/** 记忆中心的四个视图（迭代 2 第 3 项）。
 *
 * 分「待审核 / 已采用 / 已拒绝 / 已删除」四类，而不是原先的三类：
 * 用户删除后需要能确认"它确实被删了、不会再出现"，而不是在列表里消失后
 * 无从验证。已删除项对模型永久不可见（服务端保证），这里只是让用户看得见。
 */
export type MemoryFilter = 'PENDING' | 'APPROVED' | 'REJECTED' | 'DELETED'

export const MEMORY_STATUS: Record<MemoryFilter, string> = {
  PENDING: 'PROPOSED',
  APPROVED: 'APPROVED',
  REJECTED: 'REJECTED',
  DELETED: 'DELETED',
}

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
 * - 用 generation 丢弃过期响应（切换筛选、搜索、连续操作）。
 * - 搜索走服务端（`q`），不在前端过滤整页数据——分页之外的条目也必须能搜到。
 * - 组件卸载后不 setState。
 */
export function useMemories(filter: MemoryFilter, search: string = '') {
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

  const statusParam = MEMORY_STATUS[filter]
  const query = search.trim()

  const reload = useCallback(async () => {
    const current = ++generation.current
    setState((prev) => ({ ...prev, loading: true, loadError: null }))

    const result = await api.memory.list({
      status: statusParam,
      ...(query ? { q: query } : {}),
    })
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
  }, [statusParam, query])

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
    async (id: string, action: () => Promise<{ ok: boolean; message?: string }>): Promise<boolean> => {
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
