import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApprovalCard } from '../components/ApprovalCard'
import { MessageComposer } from '../components/MessageComposer'
import { MessageList } from '../components/MessageList'
import { ToolActivityList } from '../components/ToolActivityList'
import { useConversationDetail } from '../hooks/useConversationDetail'

/** 距离底部多少像素以内算"正在看最新内容"，超过就不再自动跟随。 */
const STICK_THRESHOLD_PX = 96

/** 会话详情：消息列表 + 输入框。 */
export function ConversationDetailPage() {
  const { conversationId = '' } = useParams()
  const {
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
    reload,
  } = useConversationDetail(conversationId)
  const bottomRef = useRef<HTMLDivElement>(null)
  /** 用户是否贴着底部阅读。往上翻看历史时为 false，此时不要把人拽回底部。 */
  const stickToBottom = useRef(true)

  useEffect(() => {
    const onScroll = () => {
      const distance =
        document.documentElement.scrollHeight - window.scrollY - window.innerHeight
      stickToBottom.current = distance <= STICK_THRESHOLD_PX
    }
    window.addEventListener('scroll', onScroll, { passive: true })
    onScroll()
    return () => window.removeEventListener('scroll', onScroll)
  }, [])

  // 新内容到达时滚到底部，但仅在用户本来就贴着底部时。
  // 原件每次都强行滚动，用户往上翻看前文会被立刻拽回（D-012）。
  useEffect(() => {
    if (stickToBottom.current) {
      bottomRef.current?.scrollIntoView({ block: 'end' })
    }
  }, [state.messages.length, streamingText, pendingApproval, toolActivity.length])

  if (state.notFound) {
    return (
      <section className="detail">
        <p className="detail__message">这个会话不存在，可能已被删除。</p>
        <Link className="button" to="/conversations">
          返回会话列表
        </Link>
      </section>
    )
  }

  if (state.loadError) {
    return (
      <section className="detail">
        <div className="alert alert--error" role="alert">
          <p>{state.loadError}</p>
          <button type="button" onClick={() => void reload()} disabled={state.loading}>
            重试
          </button>
        </div>
        <Link className="button" to="/conversations">
          返回会话列表
        </Link>
      </section>
    )
  }

  // 本轮实时内容：工具活动 + 审批卡片。渲染在消息流末尾，
  // 跟随用户当前视线，而不是固定在页面上方（D-012 第 2 条）。
  const liveTurn =
    toolActivity.length > 0 || pendingApproval ? (
      <>
        <ToolActivityList activities={toolActivity} />
        {pendingApproval && (
          <ApprovalCard
            key={pendingApproval.approval_id}
            request={pendingApproval}
            busy={deciding}
            onDecide={(decision, remember) => void decideApproval(decision, remember)}
            onExpired={dismissApproval}
          />
        )}
      </>
    ) : null

  return (
    <section className="detail">
      <nav className="detail__nav">
        <Link to="/conversations">← 返回列表</Link>
      </nav>

      <h2 className="detail__title">
        {state.loading && !state.conversation ? '正在加载…' : state.conversation?.title}
      </h2>

      {state.loading && state.messages.length === 0 ? (
        <p className="messages__empty">正在加载消息…</p>
      ) : (
        <MessageList
          messages={state.messages}
          streamingText={streamingText}
          liveTurn={liveTurn}
        />
      )}

      <div ref={bottomRef} />

      {sendError && (
        <div className="alert alert--error" role="alert">
          <p>{sendError}</p>
        </div>
      )}

      <MessageComposer onSend={send} onCancel={cancel} sending={sending} />
    </section>
  )
}
