import { memo, type ReactNode } from 'react'

import type { Message } from '../types/api'
import { MarkdownContent } from './MarkdownContent'
import { TOOL_STATUS_LABEL, normalizeToolStatus, toolLabel } from './toolLabels'

interface MessageListProps {
  messages: Message[]
  /** 正在生成的助手回复。非 null 时在末尾追加一个"生成中"气泡。 */
  streamingText: string | null
  /**
   * 本轮正在进行的内容（工具活动、审批卡片、错误提示）。
   * 渲染在消息流末尾，跟随用户当前视线，而不是固定在对话顶部（D-012）。
   */
  liveTurn?: ReactNode
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
 * 渲染策略（D-012）：
 * - 助手消息用 Markdown 渲染（标题、列表、表格、代码块），否则长回复里
 *   会原样露出 `##` 和 `**`，基本不可读。
 * - **用户消息保持纯文本**并保留换行：用户自己输入的内容不该被重新解释，
 *   粘贴的代码也不会被当成 Markdown 语法。
 * - 一律不使用 dangerouslySetInnerHTML；模型输出属于不可信数据（§8.4）。
 */
export function MessageList({ messages, streamingText, liveTurn }: MessageListProps) {
  if (messages.length === 0 && streamingText === null && !liveTurn) {
    return <p className="messages__empty">还没有消息。在下方输入第一条。</p>
  }

  return (
    <ol className="messages">
      {messages.map((message) => (
        <MessageItem key={message.id} message={message} />
      ))}

      {streamingText !== null && (
        <li className="message message--assistant message--streaming">
          <div className="message__meta">
            <span className="message__role">{ROLE_LABEL.assistant}</span>
            <span className="message__time">生成中…</span>
          </div>
          {/* 读屏软件不该把逐字到达的文本全部播报一遍：
              用独立的 aria-live="off" 容器 + 结束时才有的完整消息。 */}
          <div aria-live="off">
            {streamingText ? (
              <MarkdownContent content={streamingText} />
            ) : (
              <p className="message__content message__content--pending">正在思考…</p>
            )}
          </div>
          <span className="caret" aria-hidden="true" />
        </li>
      )}

      {liveTurn && <li className="messages__live">{liveTurn}</li>}
    </ol>
  )
}

const MessageItem = memo(function MessageItem({ message }: { message: Message }) {
  const isAssistant = message.role === 'assistant'

  return (
    <li className={`message message--${message.role}`}>
      <div className="message__meta">
        <span className="message__role">{ROLE_LABEL[message.role] ?? message.role}</span>
        <time className="message__time" dateTime={message.created_at}>
          {formatTime(message.created_at)}
        </time>
      </div>

      {isAssistant ? (
        <div className="message__content message__content--markdown">
          <MarkdownContent content={message.content} />
        </div>
      ) : (
        // 用户/系统/工具消息：纯文本，保留换行与缩进。
        <p className="message__content">{message.content}</p>
      )}

      {message.tools && message.tools.length > 0 && (
        <ul className="message__tools">
          {message.tools.map((item, index) => {
            const status = normalizeToolStatus(item.status)
            return (
              <li
                key={`${item.tool}-${index}`}
                className={`message__tool message__tool--${status}`}
              >
                调用工具：{toolLabel(item.tool)}
                <span className="message__tool-status">
                  {TOOL_STATUS_LABEL[status] ?? item.status}
                </span>
              </li>
            )
          })}
        </ul>
      )}
    </li>
  )
})
