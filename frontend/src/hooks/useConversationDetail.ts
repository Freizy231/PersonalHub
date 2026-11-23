import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Conversation, Message } from '../types/api'

export const MESSAGE_MAX_LENGTH = 100_000
const PAGE_SIZE = 200

export interface ConversationDetailState {
  conversation: Conversation | null
  messages: Message[]
  loading: boolean
  /** 会话不存在或无权限时为 true，用于展示 404 页面而不是"后端坏了"。 */
  notFound: boolean
  loadError: string | null
}

/**
 * 单个会话及其消息的加载与追加。
 *
 * 约束：
 * - 消息列表以服务端为准，发送成功后重新拉取，保证顺序与时间戳一致。
 * - 用 generation 丢弃过期响应（切换会话、快速重发）。
 * - 组件卸载后不 setState。
 */
export function useConversationDetail(conversationId: string) {
  const [state, setState] = useState<ConversationDetailState>({
    conversation: null,
    messages: [],
    loading: true,
    notFound: false,
    loadError: null,
  })
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)

  const load = useCallback(async () => {
    const current = ++generation.current
    setState((prev) => ({ ...prev, loading: true, loadError: null }))

    const detail = await api.conversations.get(conversationId)
    if (!mounted.current || current !== generation.current) return

    if (!detail.ok) {
      const notFound = detail.code === 'NOT_FOUND'
      setState({
        conversation: null,
        messages: [],
        loading: false,
        notFound,
        loadError: notFound ? null : detail.message,
      })
      return
    }

    const list = await api.conversations.messages.list(conversationId, PAGE_SIZE, 0)
    if (!mounted.current || current !== generation.current) return

    if (!list.ok) {
      setState((prev) => ({ ...prev, loading: false, loadError: list.message }))
      return
    }

    setState({
      conversation: detail.data,
      messages: list.data.items,
      loading: false,
      notFound: false,
      loadError: null,
    })
  }, [conversationId])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    return () => {
      mounted.current = false
    }
  }, [load])

  const send = useCallback(
    async (content: string): Promise<boolean> => {
      const trimmed = content.trim()
      if (!trimmed) return false

      setSending(true)
      setSendError(null)

      const result = await api.conversations.messages.append(conversationId, trimmed)
      if (!mounted.current) return result.ok

      if (!result.ok) {
        setSendError(result.message)
        setSending(false)
        return false
      }

      await load()
      if (mounted.current) setSending(false)
      return true
    },
    [conversationId, load],
  )

  return { state, sending, sendError, send, reload: load }
}
