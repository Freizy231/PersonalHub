import { useEffect, useRef } from 'react'
import { Link, useParams } from 'react-router-dom'

import { MessageComposer } from '../components/MessageComposer'
import { MessageList } from '../components/MessageList'
import { useConversationDetail } from '../hooks/useConversationDetail'

/** 会话详情：消息列表 + 输入框。 */
export function ConversationDetailPage() {
  const { conversationId = '' } = useParams()
  const { state, streamingText, sending, sendError, send, cancel, reload } =
    useConversationDetail(conversationId)
  const bottomRef = useRef<HTMLDivElement>(null)

  // 新消息或流式文本增长时滚到底部，否则用户看不到最新内容。
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ block: 'end' })
  }, [state.messages.length, streamingText])

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
        <MessageList messages={state.messages} streamingText={streamingText} />
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
