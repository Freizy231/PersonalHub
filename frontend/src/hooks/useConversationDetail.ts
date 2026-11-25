import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import {
  INITIAL_SEND_STATE,
  type SendFailure,
  type SendState,
} from '../services/sendState'
import { getSessionToken } from '../services/session'
import { streamChat } from '../services/stream'
import { MESSAGE_MAX_LENGTH } from '../types/api'
import type { ApprovalOutcome, ApprovalRequestEvent, Conversation, Message } from '../types/api'

export { MESSAGE_MAX_LENGTH }

/** 首屏加载的消息条数。
 *
 * 刻意不是"一次尽量多取"：长会话会因此把全部历史塞进首屏，既慢又让
 * "加载更早"永远不出现（用户以为已经看完）。50 条足够一屏多，
 * 更早的内容按需加载（迭代 2 第 6 项）。
 */
const PAGE_SIZE = 50

/** 按 id 去重，保留先出现的那一份（顺序即显示顺序）。 */
function dedupeById(messages: Message[]): Message[] {
  const seen = new Set<string>()
  return messages.filter((message) => {
    if (seen.has(message.id)) return false
    seen.add(message.id)
    return true
  })
}

export interface ConversationDetailState {
  conversation: Conversation | null
  messages: Message[]
  loading: boolean
  /** 会话不存在或无权限时为 true，用于展示 404 页面而不是"后端坏了"。 */
  notFound: boolean
  loadError: string | null
  /** 更早的消息是否还有（服务端 `has_more`）。 */
  hasEarlier: boolean
  /** 正在加载更早的消息。 */
  loadingEarlier: boolean
  /** 加载更早失败的原因。与 loadError 分开：已有内容仍然可用。 */
  earlierError: string | null
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
  /** 已做出的决定（含失败原因）。非 null 时卡片进入终态。 */
  approvalOutcome: ApprovalOutcome | null
  /** 对审批做出决定，随后对话继续。 */
  decideApproval: (decision: 'approve' | 'reject', remember: boolean) => Promise<void>
  /** 审批已过期：卡片保留并显示"未执行"。 */
  dismissApproval: () => void
  /** 正在提交审批决定。 */
  deciding: boolean
  sending: boolean
  /** 发送状态机：sending / streaming / stopping / interrupted（迭代 2 第 1 项）。 */
  sendState: SendState
  /** 发送一条消息。返回 false 表示服务端**没有接收**（原文需还给用户）。 */
  send: (content: string) => Promise<boolean>
  /** 原样重发上一次失败的请求。仅在 sendState.retryText 有值时有效。 */
  retry: () => Promise<void>
  /** 放弃重试，回到可继续输入的状态。 */
  dismissFailure: () => void
  cancel: () => void
  reload: () => Promise<void>
  /** 加载更早的一页消息（游标分页，不影响已加载内容）。 */
  loadEarlier: () => Promise<void>
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
    hasEarlier: false,
    loadingEarlier: false,
    earlierError: null,
  })
  const [streamingText, setStreamingText] = useState<string | null>(null)
  const [toolActivity, setToolActivity] = useState<ToolActivity[]>([])
  const [pendingApproval, setPendingApproval] = useState<ApprovalRequestEvent | null>(null)
  const [approvalOutcome, setApprovalOutcome] = useState<ApprovalOutcome | null>(null)
  const [deciding, setDeciding] = useState(false)
  const [sending, setSending] = useState(false)
  const [sendState, setSendState] = useState<SendState>(INITIAL_SEND_STATE)

  const mounted = useRef(true)
  const generation = useRef(0)
  /**
   * 最新 state 的同步镜像。
   *
   * loadEarlier 需要读"当前最早一条"，而 useCallback 的闭包会捕获旧值：
   * 连续点两次"加载更早"时第二次仍会拿同一个游标，取回同一页。
   * 用 ref 读最新值，绕开闭包过期问题。
   */
  /** 最新 state 的同步镜像。 */
  const stateRef = useRef(state)
  useEffect(() => {
    // 在 effect 里同步而不是渲染期赋值：渲染期写 ref 会让"渲染是否纯"
    // 依赖执行顺序，React 严格模式下会被重复执行。
    stateRef.current = state
  }, [state])
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
  /** 到期只上报一次，避免重复写入终态。 */
  const expiredRef = useRef(false)

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
        hasEarlier: false,
        loadingEarlier: false,
        earlierError: null,
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
      hasEarlier: list.data.has_more ?? false,
      loadingEarlier: false,
      earlierError: null,
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

  /**
   * 加载更早的一页。
   *
   * 用**游标**（当前最早一条的 id）而不是 offset：offset 从尾部数，
   * 这期间用户若发了新消息，同一个 offset 会指向别处——表现为重复或跳过。
   * 后端为此提供了 `before` 参数。
   *
   * 结果**前插**到已有列表头部，不替换：用户已经在读的内容不该跳走。
   * 失败只设置 earlierError，不清空任何已加载内容。
   */
  const loadEarlier = useCallback(async () => {
    const earliest = stateRef.current.messages[0]
    if (!earliest || stateRef.current.loadingEarlier || !stateRef.current.hasEarlier) return

    setState((prev) => ({ ...prev, loadingEarlier: true, earlierError: null }))
    const result = await api.conversations.messages.before(conversationId, earliest.id, PAGE_SIZE)
    if (!mounted.current) return

    if (!result.ok) {
      setState((prev) => ({ ...prev, loadingEarlier: false, earlierError: result.message }))
      return
    }

    setState((prev) => ({
      ...prev,
      // 去重：并发或重复点击时同一条可能被取两次。
      messages: dedupeById([...result.data.items, ...prev.messages]),
      hasEarlier: result.data.has_more ?? false,
      loadingEarlier: false,
      earlierError: null,
    }))
  }, [conversationId])

  const send = useCallback(
    async (content: string): Promise<boolean> => {
      const trimmed = content.trim()
      // 同步闸门：并发保护不依赖异步 state。
      if (!trimmed || trimmed.length > MESSAGE_MAX_LENGTH) return false
      if (inFlightRef.current || sending) return false

      inFlightRef.current = true
      setSending(true)
      setSendState({ phase: 'sending', failure: null, retryText: null })
      setStreamingText('')
      setToolActivity([])
      setPendingApproval(null)
      setApprovalOutcome(null)
      expiredRef.current = false

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
      /** 本轮是否出错（决定进入 interrupted 还是正常结束）。 */
      // 用可变容器而不是 let：TS 无法追踪闭包内对 let 的赋值，
      // 会把后续读取窄化成 null（进而报 never）。
      const outcome: { failure: SendFailure | null } = { failure: null }
      /** 服务端是否真的收到了这条消息（决定原文要不要还给输入框）。 */
      let accepted = false

      const flush = () => {
        rafRef.current = null
        if (mounted.current) setStreamingText(accumulated)
      }

      /** 第一次拿到内容（delta 或工具事件）时，把 "sending" 推进到 "streaming"。 */
      let sawActivity = false
      const markActivity = () => {
        if (sawActivity) return
        sawActivity = true
        if (mounted.current) setSendState((prev) => ({ ...prev, phase: 'streaming' }))
      }

      try {
        await streamChat(
          conversationId,
          trimmed,
          {
            onDelta: (text) => {
              markActivity()
              accumulated += text
              // 每个 delta 都 setState 会让 60fps 内重渲染上百次，
              // 接入 Markdown 后开销明显。按帧合并。
              if (rafRef.current === null) {
                rafRef.current = window.requestAnimationFrame(flush)
              }
            },
            onApprovalRequired: (payload) => {
              if (!mounted.current) return
              markActivity()
              // 新的一次审批请求：清掉上一条的终态，重新开始计时。
              setApprovalOutcome(null)
              expiredRef.current = false
              setPendingApproval(payload)
            },
            onToolStart: (payload) => {
              if (!mounted.current) return
              accepted = true
              markActivity()
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
              // **不要**在这里置 accepted。
              //
              // `onError` 覆盖两类完全不同的失败：
              // 1. 非 200 响应（例如 503 未配置密钥）——服务端在落库**之前**
              //    就拒绝了，消息没入库，原文必须还给用户（实测缺陷：这里
              //    无条件置 accepted，导致输入框已清空的文字凭空消失）；
              // 2. 流中途的 error 事件——此时 200 已到，`onAccepted` 早已
              //    置 true，消息确实入库，原文不该还给用户。
              // 因此 accepted 只由 `onAccepted` / `onDone` / 工具事件决定。
              outcome.failure = {
                code: payload.code,
                message: payload.message,
                // 服务端没给就按不可重试处理：给一个点了必然失败的按钮更糟。
                retryable: payload.retryable === true,
                partial: accumulated.length > 0,
              }
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

      const aborted = controller.signal.aborted
      // 已经收到的部分文本**保留**到本轮结束再清。
      // 原先这里无条件 setStreamingText(null)：断线时用户看到 AI 消失，
      // 仿佛什么都没发生。现在由 interrupted 状态说明"可能不完整"。
      const partialText = accumulated

      setSending(false)

      // 无论成功、失败还是取消，都重新拉取：确保界面与数据库一致
      // （失败时用户消息已入库，乐观条目需要换成带真实 id 的版本）。
      await load()
      if (!mounted.current) return false

      setToolActivity([])

      if (aborted) {
        // 用户主动停止。半截回复**不入库**（D-006），因此这里如实清掉，
        // 并说明"已停止，未保存"。不谎称成功，也不当作错误。
        setStreamingText(null)
        setSendState({
          phase: 'interrupted',
          failure: {
            code: 'USER_ABORTED',
            message: '已停止生成，这次的回复没有保存。',
            retryable: true,
            partial: false,
          },
          retryText: accepted ? trimmed : null,
        })
        return accepted
      }

      if (outcome.failure) {
        const failure = outcome.failure
        // 断线/失败：保留已到达的文本，让用户看到 AI 说到哪儿了。
        setStreamingText(partialText ? `${partialText}\n\n（回复中断）` : null)
        setSendState({
          phase: 'interrupted',
          failure,
          // 只有服务端说"可以重试"时才给出重试入口。
          retryText: failure.retryable && accepted ? trimmed : null,
        })
        return accepted
      }

      setStreamingText(null)
      setSendState({ phase: 'idle', failure: null, retryText: null })
      return accepted
    },
    [conversationId, load, sending],
  )

  /** 重试上一次失败的请求：原样重发同一句。 */
  const retry = useCallback(async () => {
    const text = sendState.retryText
    if (!text || inFlightRef.current) return
    await send(text)
  }, [sendState.retryText, send])

  /** 放弃重试：回到 idle，保留已收到的文本。 */
  const dismissFailure = useCallback(() => {
    setSendState({ phase: 'idle', failure: null, retryText: null })
  }, [])

  const cancel = useCallback(() => {
    // 先进入 stopping：用户需要看到"停止请求已发出"，而不是按钮点了没反应。
    setSendState((prev) =>
      prev.phase === 'sending' || prev.phase === 'streaming'
        ? { ...prev, phase: 'stopping' }
        : prev,
    )
    abortRef.current?.abort()
  }, [])

  const dismissApproval = useCallback(() => {
    // 实测缺陷：原先这里直接把卡片设为 null，用户只看到卡片凭空消失。
    // 现在改为写入终态——卡片保留，并明确告知"已失效、未执行"。
    setPendingApproval((current) => {
      if (current && !expiredRef.current) {
        expiredRef.current = true
        setApprovalOutcome({ kind: 'expired', tool: current.tool, title: current.title ?? current.tool })
      }
      return current
    })
  }, [])

  const decideApproval = useCallback(
    async (decision: 'approve' | 'reject', remember: boolean) => {
      const approval = pendingApproval
      if (!approval) return
      const title = approval.title ?? approval.tool
      setDeciding(true)
      const result = await api.approvals.decide(approval.approval_id, decision, {
        rememberForSession: remember,
        sessionToken: getSessionToken(),
      })
      if (!mounted.current) return
      setDeciding(false)

      if (result.ok) {
        // 卡片进入终态；后端会唤醒对话并继续推送 delta。
        // 注意措辞：批准只代表"可以执行了"，结果要看后续回复。
        setApprovalOutcome({
          kind: decision === 'approve' ? 'approved' : 'rejected',
          tool: approval.tool,
          title,
        })
        return
      }

      // 决策失败就地显示在卡片上，而不是写到页面另一处。
      // 409 通常意味着审批已过期或被处理过——如实说明，不假装成功。
      setApprovalOutcome({
        kind: result.code === 'CONFLICT' ? 'expired' : 'failed',
        tool: approval.tool,
        title,
        ...(result.code === 'CONFLICT' ? {} : { message: result.message }),
      } as ApprovalOutcome)
    },
    [pendingApproval],
  )

  return {
    state,
    streamingText,
    toolActivity,
    pendingApproval,
    approvalOutcome,
    decideApproval,
    dismissApproval,
    deciding,
    sending,
    sendState,
    send,
    retry,
    dismissFailure,
    cancel,
    reload: load,
    loadEarlier,
  }
}
