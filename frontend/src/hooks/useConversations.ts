import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Conversation } from '../types/api'

export interface ConversationsState {
  items: Conversation[]
  total: number
  loading: boolean
  /** 列表加载失败时的信息；操作失败走 actionError，两者分开以免互相覆盖。 */
  loadError: string | null
}

export interface UseConversations {
  state: ConversationsState
  /** 新建/重命名/删除失败的原因，供 UI 顶部提示。 */
  actionError: string | null
  busy: boolean
  reload: () => Promise<void>
  create: (title: string) => Promise<boolean>
  rename: (id: string, title: string) => Promise<boolean>
  remove: (id: string) => Promise<boolean>
  clearActionError: () => void
}

const PAGE_SIZE = 50

/**
 * 会话列表的状态编排。
 *
 * 约束：
 * - 所有数据来自后端，前端不把列表当作唯一事实来源（AGENT.md §4.1）。
 * - 写操作成功后重新拉取列表，避免本地状态与数据库不一致
 *   （例如 updated_at 排序变化）。
 * - 用 generation 丢弃过期响应，避免快速操作时旧结果覆盖新结果。
 */
export function useConversations(): UseConversations {
  const [state, setState] = useState<ConversationsState>({
    items: [],
    total: 0,
    loading: true,
    loadError: null,
  })
  const [actionError, setActionError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const mounted = useRef(true)
  const generation = useRef(0)

  const reload = useCallback(async () => {
    const current = ++generation.current
    setState((prev) => ({ ...prev, loading: true, loadError: null }))

    const result = await api.conversations.list(PAGE_SIZE, 0)
    if (!mounted.current || current !== generation.current) return

    if (result.ok) {
      setState({
        items: result.data.items,
        total: result.data.total,
        loading: false,
        loadError: null,
      })
      return
    }
    setState((prev) => ({ ...prev, loading: false, loadError: result.message }))
  }, [])

  useEffect(() => {
    mounted.current = true
    // 与外部系统（后端）同步，结果异步返回，不会同步触发级联渲染。
    // oxlint-disable-next-line react/set-state-in-effect
    void reload()
    return () => {
      mounted.current = false
    }
  }, [reload])

  /** 执行写操作：统一处理 busy、错误提示与成功后刷新。 */
  const runAction = useCallback(
    async (action: () => Promise<{ ok: boolean; message?: string }>): Promise<boolean> => {
      setBusy(true)
      setActionError(null)
      const result = await action()
      if (!mounted.current) return result.ok

      if (!result.ok) {
        setActionError(result.message ?? '操作失败。')
        setBusy(false)
        return false
      }

      await reload()
      if (mounted.current) setBusy(false)
      return true
    },
    [reload],
  )

  const create = useCallback(
    (title: string) => runAction(() => api.conversations.create(title.trim())),
    [runAction],
  )

  const rename = useCallback(
    (id: string, title: string) => runAction(() => api.conversations.rename(id, title.trim())),
    [runAction],
  )

  const remove = useCallback(
    (id: string) => runAction(() => api.conversations.remove(id)),
    [runAction],
  )

  const clearActionError = useCallback(() => setActionError(null), [])

  return { state, actionError, busy, reload, create, rename, remove, clearActionError }
}
