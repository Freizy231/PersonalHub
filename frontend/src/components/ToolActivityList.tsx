import type { ToolActivity } from '../hooks/useConversationDetail'

const TOOL_LABEL: Record<string, string> = {
  search_memories: '检索记忆',
  list_tasks: '查看任务',
  create_task: '创建任务',
}

const STATUS_LABEL: Record<ToolActivity['status'], string> = {
  running: '执行中',
  ok: '完成',
  denied: '已被拒绝',
  error: '失败',
}

/**
 * 工具调用记录。
 *
 * 用户有权知道 AI 调用了什么、结果如何（AGENT.md §7.4 可理解摘要）。
 * 这里只展示工具名与状态，不展示参数内容——参数可能包含用户数据。
 */
export function ToolActivityList({ activities }: { activities: ToolActivity[] }) {
  if (activities.length === 0) return null

  return (
    <ul className="tool-activity" aria-live="polite">
      {activities.map((item) => (
        <li key={item.id} className={`tool-activity__item tool-activity__item--${item.status}`}>
          <span className="tool-activity__dot" aria-hidden="true" />
          <span className="tool-activity__name">{TOOL_LABEL[item.name] ?? item.name}</span>
          <span className="tool-activity__status">{STATUS_LABEL[item.status]}</span>
        </li>
      ))}
    </ul>
  )
}
