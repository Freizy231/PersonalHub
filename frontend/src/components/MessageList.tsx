import type { Message } from '../types/api'

interface MessageListProps {
  messages: Message[]
  /** 正在生成的助手回复。非 null 时在末尾追加一个"生成中"气泡。 */
  streamingText: string | null
}

const ROLE_LABEL: Record<string, string> = {
  user: '你',
  assistant: 'AI',
  system: '系统',
  tool: '工具',
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
}

/**
 * 消息列表。
 *
 * 用 white-space: pre-wrap 保留用户输入的换行与缩进（可能是代码）。
 * 文本一律作为纯文本渲染，绝不使用 dangerouslySetInnerHTML：
 * 消息内容是用户输入，将来还会包含模型输出，属于不可信数据（AGENT.md §8.4）。
 */
export function MessageList({ messages, streamingText }: MessageListProps) {
  if (messages.length === 0 && streamingText === null) {
    return <p className="messages__empty">还没有消息。在下方输入第一条。</p>
  }

  return (
    <ol className="messages">
      {messages.map((message) => (
        <li key={message.id} className={`message message--${message.role}`}>
          <div className="message__meta">
            <span className="message__role">{ROLE_LABEL[message.role] ?? message.role}</span>
            <time className="message__time" dateTime={message.created_at}>
              {formatTime(message.created_at)}
            </time>
          </div>
          <p className="message__content">{message.content}</p>
        </li>
      ))}

      {streamingText !== null && (
        <li className="message message--assistant message--streaming" aria-live="polite">
          <div className="message__meta">
            <span className="message__role">{ROLE_LABEL.assistant}</span>
            <span className="message__time">生成中…</span>
          </div>
          <p className="message__content">
            {streamingText}
            <span className="caret" aria-hidden="true" />
          </p>
        </li>
      )}
    </ol>
  )
}
