import { useState, type FormEvent, type KeyboardEvent } from 'react'

const MAX_LENGTH = 100_000

interface MessageComposerProps {
  onSend: (content: string) => Promise<boolean>
  sending: boolean
}

/**
 * 消息输入框。
 *
 * 交互：Enter 发送，Shift+Enter 换行（写作习惯，也是主流聊天软件的行为）。
 * 输入法组合期间（输入中文拼音时）按 Enter 只是选词，不能触发发送——
 * 用 isComposing 判断，否则中文用户每选一个词就会误发。
 */
export function MessageComposer({ onSend, sending }: MessageComposerProps) {
  const [content, setContent] = useState('')

  const trimmed = content.trim()
  const canSend = trimmed.length > 0 && trimmed.length <= MAX_LENGTH && !sending

  async function submit() {
    if (!canSend) return
    const ok = await onSend(trimmed)
    if (ok) setContent('')
  }

  function handleSubmit(event: FormEvent) {
    event.preventDefault()
    void submit()
  }

  function handleKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== 'Enter' || event.shiftKey) return
    // 输入法正在组合文字时不发送。
    if (event.nativeEvent.isComposing) return
    event.preventDefault()
    void submit()
  }

  return (
    <form className="composer" onSubmit={handleSubmit}>
      <label className="visually-hidden" htmlFor="composer-input">
        输入消息
      </label>
      <textarea
        id="composer-input"
        value={content}
        onChange={(event) => setContent(event.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="输入消息，Enter 发送，Shift+Enter 换行"
        rows={3}
        disabled={sending}
      />
      <div className="composer__actions">
        <span className="composer__hint">{trimmed.length.toLocaleString()} 字符</span>
        <button type="submit" disabled={!canSend}>
          {sending ? '发送中…' : '发送'}
        </button>
      </div>
    </form>
  )
}
