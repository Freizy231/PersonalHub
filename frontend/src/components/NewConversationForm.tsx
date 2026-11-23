import { useState, type FormEvent } from 'react'

interface NewConversationFormProps {
  onSubmit: (title: string) => Promise<boolean>
  busy: boolean
}

const MAX_TITLE = 200

/**
 * 新建会话表单。
 *
 * 前端做即时校验只是提升体验；真正的边界仍由后端 Schema 与数据库约束保证。
 */
export function NewConversationForm({ onSubmit, busy }: NewConversationFormProps) {
  const [title, setTitle] = useState('')

  const trimmed = title.trim()
  const canSubmit = trimmed.length > 0 && trimmed.length <= MAX_TITLE && !busy

  async function handleSubmit(event: FormEvent) {
    event.preventDefault()
    if (!canSubmit) return
    const ok = await onSubmit(trimmed)
    if (ok) setTitle('')
  }

  return (
    <form className="new-conversation" onSubmit={handleSubmit}>
      <label className="visually-hidden" htmlFor="new-conversation-title">
        新会话标题
      </label>
      <input
        id="new-conversation-title"
        type="text"
        value={title}
        onChange={(event) => setTitle(event.target.value)}
        placeholder="新会话标题，例如：每周学习计划"
        maxLength={MAX_TITLE}
        disabled={busy}
        autoComplete="off"
      />
      <button type="submit" disabled={!canSubmit}>
        {busy ? '处理中…' : '新建会话'}
      </button>
    </form>
  )
}
