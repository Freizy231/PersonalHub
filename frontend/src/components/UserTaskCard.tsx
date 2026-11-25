import { Link } from 'react-router-dom'

import type { UserTask } from '../types/api'
import { formatTime } from './jobLabels'

interface UserTaskCardProps {
  task: UserTask
  busy: boolean
  confirming: boolean
  onToggle: (task: UserTask) => void
  onRequestDelete: (id: string) => void
  onCancelDelete: () => void
  onConfirmDelete: (id: string) => void
}

/**
 * 单条用户任务。
 *
 * 与作业卡片长得像，但语义不同：这里只有"完成 / 重新打开 / 删除"，
 * 没有取消、重试、执行阶段。状态也只可能是未完成 / 已完成。
 */
export function UserTaskCard({
  task,
  busy,
  confirming,
  onToggle,
  onRequestDelete,
  onCancelDelete,
  onConfirmDelete,
}: UserTaskCardProps) {
  const done = task.status === 'DONE'

  return (
    <li className={`user-task${done ? ' user-task--done' : ''}`}>
      <div className="user-task__main">
        <h3 className={`user-task__title${done ? ' user-task__title--done' : ''}`}>
          {task.title}
        </h3>
        <span className={`user-task__status user-task__status--${done ? 'done' : 'open'}`}>
          {done ? '已完成' : '未完成'}
        </span>
      </div>

      {task.description && <p className="user-task__description">{task.description}</p>}

      <div className="user-task__meta">
        <time dateTime={task.updated_at}>{formatTime(task.updated_at)}</time>
        {task.created_via_approval_id && (
          <span className="user-task__source">来自对话中的操作确认</span>
        )}
      </div>

      {confirming ? (
        <div className="user-task__confirm" role="group" aria-label="确认删除任务">
          <p>确定删除「{task.title}」吗？删除后无法恢复。</p>
          <button type="button" className="danger" disabled={busy} onClick={() => onConfirmDelete(task.id)}>
            删除
          </button>
          <button type="button" onClick={onCancelDelete} disabled={busy}>
            取消
          </button>
        </div>
      ) : (
        <div className="user-task__actions">
          <button type="button" onClick={() => onToggle(task)} disabled={busy}>
            {done ? '重新打开' : '标记完成'}
          </button>
          <button
            type="button"
            className="danger"
            onClick={() => onRequestDelete(task.id)}
            disabled={busy}
          >
            删除
          </button>
        </div>
      )}
    </li>
  )
}

/** 任务来源说明：任务在对话中经审批创建，本页不提供直接新建。 */
export function UserTaskOriginNote() {
  return (
    <p className="user-task-origin">
      任务来自对话：让 AI「帮我记一个任务」并确认后，它会出现在这里。
      <Link to="/conversations">去会话</Link>
    </p>
  )
}
