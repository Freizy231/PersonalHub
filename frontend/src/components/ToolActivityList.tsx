import type { ToolActivity } from '../hooks/useConversationDetail'
import { TOOL_STATUS_LABEL, toolLabel } from './toolLabels'

/**
 * 工具调用记录。
 *
 * 用户有权知道 AI 调用了什么、结果如何（AGENT.md §7.4 可理解摘要）。
 * 这里只展示工具名与状态，不展示参数内容——参数可能包含用户数据。
 *
 * 位置：渲染在消息流末尾（本轮回复下方），而不是对话顶部，
 * 这样它出现在用户正在看的地方，而不是被挤在页面上方（D-012）。
 */
export function ToolActivityList({ activities }: { activities: ToolActivity[] }) {
  if (activities.length === 0) return null

  return (
    <ul className="tool-activity" aria-live="polite">
      {activities.map((item) => (
        <li key={item.id} className={`tool-activity__item tool-activity__item--${item.status}`}>
          <span className="tool-activity__dot" aria-hidden="true" />
          <span className="tool-activity__name">{toolLabel(item.name, item.displayName)}</span>
          <span className="tool-activity__status">{TOOL_STATUS_LABEL[item.status]}</span>
        </li>
      ))}
    </ul>
  )
}
