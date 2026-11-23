import { useState, type FormEvent } from 'react'

import type { Conversation } from '../types/api'

interface ConversationItemProps {
  conversation: Conversation
  busy: boolean
  onRename: (id: string, title: string) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
}

const MAX_TITLE = 200

/** 把后端返回的 UTC 时间格式化成本地时间。 */
function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
}

/**
 * 单个会话条目。
 *
 * 删除使用两段式确认（先点"删除"进入确认态，再点"确认删除"），
 * 避免误触；不用 window.confirm，因为它会阻塞页面且样式不可控。
 */
export function ConversationItem({ conversation, busy, onRename, onDelete }: ConversationItemProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(conversation.title)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const trimmed = draft.trim()
  const canSave = trimmed.length > 0 && trimmed.length <= MAX_TITLE && !busy

  async function handleSave(event: FormEvent) {
    event.preventDefault()
    if (!canSave) return
    if (trimmed === conversation.title) {
      setEditing(false)
      return
    }
    const ok = await onRename(conversation.id, trimmed)
    if (ok) setEditing(false)
  }

  function cancelEdit() {
    setDraft(conversation.title)
    setEditing(false)
  }

  async function handleDelete() {
    const ok = await onDelete(conversation.id)
    if (!ok) setConfirmingDelete(false)
  }

  if (editing) {
    return (
      <li className="conversation conversation--editing">
        <form onSubmit={handleSave} className="conversation__edit-form">
          <label className="visually-hidden" htmlFor={`rename-${conversation.id}`}>
            重命名会话
          </label>
          <input
            id={`rename-${conversation.id}`}
            type="text"
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={MAX_TITLE}
            disabled={busy}
            autoFocus
            onKeyDown={(event) => {
              if (event.key === 'Escape') cancelEdit()
            }}
          />
          <button type="submit" disabled={!canSave}>
            保存
          </button>
          <button type="button" onClick={cancelEdit} disabled={busy}>
            取消
          </button>
        </form>
      </li>
    )
  }

  return (
    <li className="conversation">
      <div className="conversation__main">
        <span className="conversation__title">{conversation.title}</span>
        <time className="conversation__time" dateTime={conversation.updated_at}>
          {formatTime(conversation.updated_at)}
        </time>
      </div>

      <div className="conversation__actions">
        {confirmingDelete ? (
          <>
            <span className="conversation__confirm-text">确定删除？</span>
            <button type="button" className="danger" onClick={handleDelete} disabled={busy}>
              确认删除
            </button>
            <button type="button" onClick={() => setConfirmingDelete(false)} disabled={busy}>
              取消
            </button>
          </>
        ) : (
          <>
            <button
              type="button"
              onClick={() => {
                setDraft(conversation.title)
                setEditing(true)
              }}
              disabled={busy}
            >
              重命名
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => setConfirmingDelete(true)}
              disabled={busy}
            >
              删除
            </button>
          </>
        )}
      </div>
    </li>
  )
}
