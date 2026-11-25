import { useRef, useState, type FormEvent, type KeyboardEvent } from 'react'

import { MESSAGE_MAX_LENGTH } from '../types/api'

interface MessageComposerProps {
  /**
   * 提交一条消息。返回 false 表示**服务端没有接收**（网络不通、后端未启动等），
   * 此时调用方需要把原文还给用户，避免辛苦打的内容凭空消失。
   */
  onSend: (content: string) => Promise<boolean>
  onCancel: () => void
  sending: boolean
}

/**
 * 消息输入框。
 *
 * 交互（D-012 修复了三个实测问题）：
 * 1. **发送后立刻清空**。原先清空发生在整条回复生成完之后，用户会看到
 *    "已经发出去的内容还留在框里"。
 * 2. **生成期间可以继续输入**。原先 `disabled={sending}` 让输入框整段
 *    时间不可用，写下一句话也做不到。现在只禁用发送按钮。
 * 3. **同步重入保护**。原先用异步 state `sending` 判断，同一帧内连按两次
 *    回车都能通过检查，导致并发两轮对话。用 ref 做同步闸门。
 *
 * Enter 发送，Shift+Enter 换行；输入法组合期间 Enter 只选词不发送。
 */
export function MessageComposer({ onSend, onCancel, sending }: MessageComposerProps) {
  const [content, setContent] = useState('')
  // 同步闸门：state 更新是异步的，回调里读到的是旧值。
  const submittingRef = useRef(false)

  const trimmed = content.trim()
  const tooLong = trimmed.length > MESSAGE_MAX_LENGTH
  // `submittingRef` 只用于事件处理器里的同步重入判断，不参与渲染——
  // 渲染期读 ref 会让 UI 依赖一个不会触发重渲染的值。
  const canSend = trimmed.length > 0 && !tooLong && !sending

  async function submit() {
    // 先同步占位，再检查 state —— 这样连按两次也只会发出一次。
    if (submittingRef.current) return
    if (!trimmed || tooLong || sending) return

    submittingRef.current = true
    const text = trimmed
    // 立刻清空：用户按下发送的那一刻，这条消息就已经"发出去了"。
    setContent('')

    try {
      const accepted = await onSend(text)
      if (!accepted) {
        // 服务端没收到（消息未入库）——把原文还回去，而不是让它消失。
        // 若用户在这期间已经开始写新内容，则把旧内容追加到前面而不是覆盖。
        setContent((current) => (current.trim() ? `${text}\n${current}` : text))
      }
    } finally {
      submittingRef.current = false
    }
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

  const hint = tooLong
    ? `超出上限 ${(trimmed.length - MESSAGE_MAX_LENGTH).toLocaleString()} 字符`
    : sending
      ? '生成中…（可以先写下一句，等这一轮结束再发送）'
      : `${trimmed.length.toLocaleString()} 字符`

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
      />
      <div className="composer__actions">
        <span className={`composer__hint${tooLong ? ' composer__hint--error' : ''}`}>{hint}</span>
        {sending ? (
          <>
            {/* 生成期间不提供"排队发送"：这一轮还没结束时发出去会与它竞争
                同一个会话，历史交错。按钮明确禁用，而不是点了没反应。 */}
            <button type="submit" disabled title="等这一轮生成结束后再发送">
              发送
            </button>
            <button type="button" className="danger" onClick={onCancel}>
              停止生成
            </button>
          </>
        ) : (
          <button type="submit" disabled={!canSend}>
            发送
          </button>
        )}
      </div>
    </form>
  )
}
