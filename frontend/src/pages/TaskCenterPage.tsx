import { Link } from 'react-router-dom'

import { JobCard } from '../components/JobCard'
import { useJobs, type JobFilter } from '../hooks/useJobs'

const FILTERS: { key: JobFilter; label: string }[] = [
  { key: 'ALL', label: '全部' },
  { key: 'PENDING', label: '排队中' },
  { key: 'RUNNING', label: '执行中' },
  { key: 'SUCCEEDED', label: '已完成' },
  { key: 'FAILED', label: '失败' },
]

/**
 * Task Center（AGENT.md §2.5）。
 *
 * 明确告知用户：任务在服务端执行，关掉浏览器不影响（M7 验收要点）。
 */
export function TaskCenterPage({ filter }: { filter: JobFilter }) {
  const { state, actionError, busyId, reload, cancel, retry, clearActionError } = useJobs(filter)

  return (
    <section className="task-center">
      <header className="task-center__header">
        <h2>任务</h2>
        <span className="task-center__count">
          {state.loading ? '加载中…' : `共 ${state.total} 个`}
        </span>
      </header>

      <p className="task-center__notice">
        任务在本地服务进程中执行，<strong>关闭浏览器不影响</strong>。
        服务重启后，未完成的任务会重新排队。
      </p>

      <nav className="task-center__filters" aria-label="任务筛选">
        {FILTERS.map((item) => (
          <Link
            key={item.key}
            to={item.key === 'ALL' ? '/tasks' : `/tasks/${item.key.toLowerCase()}`}
            className={item.key === filter ? 'task-center__filter is-active' : 'task-center__filter'}
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
      ) : state.items.length === 0 ? (
        <p className="task-center__empty">
          {state.loading ? '正在加载任务…' : '还没有任务。'}
        </p>
      ) : (
        <ul className="job-list">
          {state.items.map((job) => (
            <JobCard
              key={job.id}
              job={job}
              busy={busyId === job.id}
              onCancel={(id) => void cancel(id)}
              onRetry={(id) => void retry(id)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
