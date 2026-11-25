import { Link } from 'react-router-dom'

import { UserTaskCard, UserTaskOriginNote } from '../components/UserTaskCard'
import { useUserTasks, type UserTaskFilter } from '../hooks/useUserTasks'

const FILTERS: { key: UserTaskFilter; label: string }[] = [
  { key: 'ALL', label: '全部' },
  { key: 'OPEN', label: '未完成' },
  { key: 'DONE', label: '已完成' },
]

/**
 * 「我的任务」页面（`tasks` 表，D-013）。
 *
 * 与 `/activity`（后台作业）严格区分：
 * - 这里是你记录的事项，状态只有未完成 / 已完成；
 * - 后台作业（生成应用等）在「后台作业」页，有执行阶段与取消/重试。
 *
 * 不提供创建入口：任务由对话中的写操作经用户审批后创建（§2.4）。
 */
export function UserTasksPage({ filter }: { filter: UserTaskFilter }) {
  const {
    state,
    actionError,
    busyId,
    confirmingDelete,
    reload,
    clearActionError,
    requestDelete,
    cancelDelete,
    complete,
    reopen,
    remove,
  } = useUserTasks(filter)

  return (
    <section className="user-tasks">
      <header className="user-tasks__header">
        <h2>我的任务</h2>
        <span className="user-tasks__count">
          {state.loading ? '加载中…' : `${state.openCount} 项未完成 · 共 ${state.total} 项`}
        </span>
      </header>

      <UserTaskOriginNote />

      <nav className="user-tasks__filters" aria-label="任务筛选">
        {FILTERS.map((item) => (
          <Link
            key={item.key}
            to={item.key === 'ALL' ? '/tasks' : `/tasks/${item.key.toLowerCase()}`}
            className={
              item.key === filter ? 'user-tasks__filter is-active' : 'user-tasks__filter'
            }
            aria-current={item.key === filter ? 'page' : undefined}
          >
            {item.label}
          </Link>
        ))}
      </nav>

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
        <p className="user-tasks__empty">正在加载任务…</p>
      ) : state.items.length === 0 ? (
        <div className="user-tasks__empty">
          <p>
            {filter === 'DONE'
              ? '还没有已完成的任务。'
              : filter === 'OPEN'
                ? '没有未完成的任务。'
                : '还没有任务。'}
          </p>
          <p>在会话里对 AI 说「帮我记一个任务：……」，确认后就会出现在这里。</p>
          <Link className="button" to="/conversations">
            去会话
          </Link>
        </div>
      ) : (
        <ul className="user-task-list">
          {state.items.map((task) => (
            <UserTaskCard
              key={task.id}
              task={task}
              busy={busyId === task.id}
              confirming={confirmingDelete === task.id}
              onToggle={(item) => void (item.status === 'DONE' ? reopen(item.id) : complete(item.id))}
              onRequestDelete={requestDelete}
              onCancelDelete={cancelDelete}
              onConfirmDelete={(id) => void remove(id)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
