import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import { getSessionToken } from '../services/session'
import { streamChat } from '../services/stream'
import { MESSAGE_MAX_LENGTH } from '../types/api'
import type { ApprovalRequestEvent, Conversation, Message } from '../types/api'

export { MESSAGE_MAX_LENGTH }

const PAGE_SIZE = 200

export interface ConversationDetailState {
  conversation: Conversation | null
  messages: Message[]
  loading: boolean
  /** 会话不存在或无权限时为 true，用于展示 404 页面而不是"后端坏了"。 */
  notFound: boolean
  loadError: string | null
}

/** 一次工具调用在界面上的展示状态。 */
export interface ToolActivity {
  id: string
  name: string
  /** 后端给的中文名（工具自己声明）。没有时由前端标签表兜底。 */
  displayName?: string
  status: 'running' | 'ok' | 'denied' | 'error'
  errorCode?: string
}

export interface UseConversationDetail {
  state: ConversationDetailState
  /** 流式生成中的文本；null 表示当前没有生成。 */
  streamingText: string | null
  /** 本轮的工具调用记录，用于向用户展示 AI 做了什么。 */
  toolActivity: ToolActivity[]
  /** 等待用户确认的审批请求；非 null 时对话已暂停。 */
  pendingApproval: ApprovalRequestEvent | null
  /** 对审批做出决定，随后对话继续。 */
  decideApproval: (decision: 'approve' | 'reject', remember: boolean) => Promise<void>
  /** 审批已过期，收起卡片。 */
  dismissApproval: () => void
  /** 正在提交审批决定。 */
  deciding: boolean
  sending: boolean
  sendError: string | null
  /** 发送一条消息。返回 false 表示服务端**没有接收**（原文需还给用户）。 */
  send: (content: string) => Promise<boolean>
  cancel: () => void
  reload: () => Promise<void>
}

/**
 * 单个会话及其消息的加载与流式对话。
 *
 * 关键行为：
 * - 发送后立即把用户消息显示出来（乐观更新），不必等请求返回。
 * - 生成过程中文本增量写入，按动画帧批量刷新，避免每个字符都重渲染整棵树。
 * - 结束后从服务端重新拉取，保证与数据库一致（时间戳、id、顺序）。
 * - 取消（AbortController）会中断 fetch；后端据此停止生成、不保存半截回复。
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
  const [toolActivity, setToolActivity] = useState<ToolActivity[]>([])
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequestEvent | null>(null)
  const [deciding, setDeciding] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendError, setSendError] = useState<string | null>(null)

  const mounted = useRef(true)
  const generation = useRef(0)
  const abortRef = useRef<AbortController | null>(null)
  /**
   * 同步的"正在发送"闸门。
   *
   * `sending` 是 React state，同一事件循环内连续两次回车读到的都是旧值 false，
   * 两次都会通过检查，结果是两轮生成同时跑、历史交错（实测问题）。
   * 用 ref 做同步占位就没有这个窗口。
   */
  const inFlightRef = useRef(false)
  /** 流式文本的动画帧句柄：一帧只 setState 一次。 */
  const rafRef = useRef<number | null>(null)

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

    // 取**最新**的 200 条：原先固定 offset=0 取最早的 200 条，
    // 超长会话里最新的消息反而看不见（实测缺陷）。
    const list = await api.conversations.messages.list(
      conversationId,
      PAGE_SIZE,
      0,
      'desc',
    )
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
      abortRef.current?.abort()
      if (rafRef.current !== null) window.cancelAnimationFrame(rafRef.current)
    }
  }, [load])

  const send = useCallback(
    async (content: string): Promise<boolean> => {
      const trimmed = content.trim()
      // 同步闸门：并发保护不依赖异步 state。
      if (!trimmed || trimmed.length > MESSAGE_MAX_LENGTH) return false
      if (inFlightRef.current || sending) return false

      inFlightRef.current = true
      setSending(true)
      setSendError(null)
      setStreamingText('')
      setToolActivity([])
      setPendingApproval(null)

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
      /** 服务端是否真的收到了这条消息（决定原文要不要还给用户）。 */
      let accepted = false

      const flush = () => {
        rafRef.current = null
        if (mounted.current) setStreamingText(accumulated)
      }

      try {
        await streamChat(
          conversationId,
          trimmed,
          {
            onDelta: (text) => {
              accumulated += text
              // 每个 delta 都 setState 会让 60fps 内重渲染上百次，
              // 接入 Markdown 后开销明显。按帧合并。
              if (rafRef.current === null) {
                rafRef.current = window.requestAnimationFrame(flush)
              }
            },
            onApprovalRequired: (payload) => {
              if (!mounted.current) return
              setPendingApproval(payload)
            },
            onToolStart: (payload) => {
              if (!mounted.current) return
              accepted = true
              setToolActivity((prev) => [
                ...prev,
                {
                  id: payload.id,
                  name: payload.name,
                  status: 'running',
                  ...(payload.display_name ? { displayName: payload.display_name } : {}),
                },
              ])
            },
            onToolEnd: (payload) => {
              if (!mounted.current) return
              const status =
                payload.status === 'ok'
                  ? 'ok'
                  : payload.status === 'denied'
                    ? 'denied'
                    : 'error'
              setToolActivity((prev) =>
                prev.map((item) =>
                  item.id === payload.id
                    ? {
                        ...item,
                        status,
                        ...(payload.error_code ? { errorCode: payload.error_code } : {}),
                      }
                    : item,
                ),
              )
            },
            onAccepted: () => {
              // 后端已开始处理（它先落库用户消息，再调用模型）。
              // 从这里开始，取消也不该把原文还给输入框——消息已经入库了。
              accepted = true
            },
            onDone: () => {
              accepted = true
            },
            onError: (payload) => {
              failed = true
              accepted = true
              if (mounted.current) setSendError(payload.message)
            },
          },
          controller.signal,
        )
      } finally {
        if (rafRef.current !== null) {
          window.cancelAnimationFrame(rafRef.current)
          rafRef.current = null
        }
        abortRef.current = null
        inFlightRef.current = false
      }

      if (!mounted.current) return false

      setStreamingText(null)
      setSending(false)

      // 无论成功、失败还是取消，都重新拉取：确保界面与数据库一致
      // （失败时用户消息已入库，乐观条目需要换成带真实 id 的版本）。
      await load()
      if (!mounted.current) return false
      // 本轮结束后清空实时区：工具调用已经随助手消息持久化并在消息下端展示，
      // 留着会造成同一组调用出现两次（D-012 审查发现）。
      // 被用户中断的那一轮没有入库，这张卡片紧接着丢掉即为正确结果。
      setToolActivity([])
      if (controller.signal.aborted) {
        setSendError(null)
      }
      // 只有在"服务端压根没收到"时才让输入框把原文要回去。
      return accepted || failed
    },
    [conversationId, load, sending],
  )

  const cancel = useCallback(() => {
    abortRef.current?.abort()
  }, [])

  const dismissApproval = useCallback(() => setPendingApproval(null), [])

  const decideApproval = useCallback(
    async (decision: 'approve' | 'reject', remember: boolean) => {
      const approval = pendingApproval
      if (!approval) return
      setDeciding(true)
      const result = await api.approvals.decide(approval.approval_id, decision, {
        rememberForSession: remember,
        sessionToken: getSessionToken(),
      })
      if (!mounted.current) return
      setDeciding(false)
      if (result.ok) {
        // 清除卡片；后端会唤醒对话并继续推送 delta。
        setPendingApproval(null)
      } else {
        setSendError(result.message)
      }
    },
    [pendingApproval],
  )

  return {
    state,
    streamingText,
    toolActivity,
    pendingApproval,
    decideApproval,
    dismissApproval,
    deciding,
    sending,
    sendError,
    send,
    cancel,
    reload: load,
  }
}
