import { ConversationItem } from './ConversationItem'
import { NewConversationForm } from './NewConversationForm'
import { useConversations } from '../hooks/useConversations'

/**
 * 会话管理面板。
 *
 * 状态区分清楚：加载中 / 加载失败 / 空列表 / 有数据，
 * 避免用户面对空白页面却不知道发生了什么。
 */
export function ConversationList() {
  const { state, actionError, busy, reload, create, rename, remove, clearActionError } =
    useConversations()

  return (
    <section className="conversations">
      <header className="conversations__header">
        <h2>会话</h2>
        <span className="conversations__count">
          {state.loading ? '加载中…' : `共 ${state.total} 个`}
        </span>
      </header>

      <NewConversationForm onSubmit={create} busy={busy} />

      {actionError && (
        <div className="alert alert--error" role="alert">
          <span>{actionError}</span>
          <button type="button" onClick={clearActionError} aria-label="关闭提示">
            ✕
          </button>
        </div>
      )}

      {state.loadError ? (
        <div className="alert alert--error" role="alert">
          <p>{state.loadError}</p>
          <button type="button" onClick={() => void reload()} disabled={state.loading}>
            重试
          </button>
        </div>
      ) : state.loading && state.items.length === 0 ? (
        <p className="conversations__empty">正在加载会话…</p>
      ) : state.items.length === 0 ? (
        <p className="conversations__empty">还没有会话。在上方输入标题创建第一个。</p>
      ) : (
        <ul className="conversation-list">
          {state.items.map((conversation) => (
            <ConversationItem
              key={conversation.id}
              conversation={conversation}
              busy={busy}
              onRename={rename}
              onDelete={remove}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
