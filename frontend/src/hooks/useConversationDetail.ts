import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import { streamChat } from '../services/stream'
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

export interface UseConversationDetail {
  state: ConversationDetailState
  /** 流式生成中的文本；null 表示当前没有生成。 */
  streamingText: string | null
  sending: boolean
  sendError: string | null
  send: (content: string) => Promise<boolean>
  cancel: () => void
  reload: () => Promise<void>
}

/**
 * 单个会话及其消息的加载与流式对话。
 *
 * 关键行为：
 * - 发送后立即把用户消息显示出来（乐观更新），不必等请求返回。
 * - 生成过程中逐字追加到 streamingText，结束后从服务端重新拉取，
 *   保证与数据库一致（时间戳、id、顺序）。
 * - 取消（AbortController）会中断 fetch；后端据此不保存半截回复。
 * - 用 generation 丢弃过期响应（切换会话、连续发送）。
 */
export function useConversationDetail(conversationId: string): UseConversationDetail {
  const [state, setState] = useState<ConversationDetailState>({
    conversation: null,
    messages: [],
    loading: true,
    notFound: false,
    loadError: null,
  })
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)
  const abortRef = useRef<AbortController | null>(null)

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
      // 离开页面时中断进行中的生成，避免后端继续消耗额度。
      abortRef.current?.abort()
    }
  }, [load])

  const send = useCallback(
    async (content: string): Promise<boolean> => {
      const trimmed = content.trim()
      if (!trimmed || sending) return false

      setSending(true)
      setSendError(null)
      setStreamingText('')

      const controller = new AbortController()
      abortRef.current = controller

      // 乐观显示用户消息：不等后端往返，交互更即时。
      const optimistic: Message = {
        id: `optimistic-${Date.now()}`,
        conversation_id: conversationId,
        role: 'user',
        content: trimmed,
        created_at: new Date().toISOString(),
      }
      setState((prev) => ({ ...prev, messages: [...prev.messages, optimistic] }))

      let accumulated = ''
      let failed = false

      await streamChat(
        conversationId,
        trimmed,
        {
          onDelta: (text) => {
            accumulated += text
            if (mounted.current) setStreamingText(accumulated)
          },
          onDone: () => {
            // 结束后统一从服务端拉取，替换乐观消息与流式文本。
          },
          onError: (payload) => {
            failed = true
            if (mounted.current) setSendError(payload.message)
          },
        },
        controller.signal,
      )

      abortRef.current = null
      if (!mounted.current) return false

      setStreamingText(null)
      setSending(false)

      // 无论成功、失败还是取消，都重新拉取：确保界面与数据库一致
      // （失败时用户消息已入库，乐观条目需要换成带真实 id 的版本）。
      await load()
      if (mounted.current && controller.signal.aborted) {
        setSendError(null)
      }
      return !failed
    },
    [conversationId, load, sending],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  return { state, streamingText, sending, sendError, send, cancel, reload: load }
}
