import { useEffect, useLayoutEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { ApprovalCard } from '../components/ApprovalCard'
import { MessageComposer } from '../components/MessageComposer'
import { MessageList } from '../components/MessageList'
import { PageHeader } from '../components/PageHeader'
import { EarlierMessages } from '../components/EarlierMessages'
import { SendStatusBar } from '../components/SendStatusBar'
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
    reload,
    loadEarlier,
  } = useConversationDetail(conversationId)
  const bottomRef = useRef<HTMLDivElement>(null)
  /** 用户是否贴着底部阅读。往上翻看历史时为 false，此时不要把人拽回底部。 */
  const stickToBottom = useRef(true)

  /**
   * 加载更早之前记下文档高度与滚动位置，加载后按差值补回。
   *
   * 不这样做的话，前插内容会把当前阅读位置往下推——用户点"加载更早"，
   * 视线里的那句话会突然跳走（浏览器默认保持 scrollTop，而内容变长了）。
   */
  const restoreScroll = useRef<{ height: number; top: number } | null>(null)

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

  // 前插完成后补回滚动位置（在浏览器绘制前，避免闪烁）。
  useLayoutEffect(() => {
    const saved = restoreScroll.current
    if (!saved) return
    restoreScroll.current = null
    const delta = document.documentElement.scrollHeight - saved.height
    if (delta > 0) {
      window.scrollTo({ top: saved.top + delta })
    }
  }, [state.messages.length])

  function handleLoadEarlier() {
    restoreScroll.current = {
      height: document.documentElement.scrollHeight,
      top: window.scrollY,
    }
    void loadEarlier()
  }

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
            outcome={approvalOutcome}
            onDecide={(decision, remember) => void decideApproval(decision, remember)}
            onExpire={dismissApproval}
          />
        )}
      </>
    ) : null

  return (
    <section className="detail">
      <PageHeader
        title={state.loading && !state.conversation ? '正在加载…' : (state.conversation?.title ?? '对话')}
        actions={
          <Link className="detail__back" to="/conversations">
            ← 返回列表
          </Link>
        }
      />

      {state.loading && state.messages.length === 0 ? (
        <p className="messages__empty">正在加载消息…</p>
      ) : (
        <>
          <EarlierMessages
            visible={state.messages.length > 0}
            hasEarlier={state.hasEarlier}
            loading={state.loadingEarlier}
            error={state.earlierError}
            onLoad={handleLoadEarlier}
          />
          <MessageList
            messages={state.messages}
            streamingText={streamingText}
            liveTurn={liveTurn}
          />
        </>
      )}

      <div ref={bottomRef} />

      <SendStatusBar
        state={sendState}
        onRetry={() => void retry()}
        onDismiss={dismissFailure}
      />

      <MessageComposer onSend={send} onCancel={cancel} sending={sending} />
    </section>
  )
}
