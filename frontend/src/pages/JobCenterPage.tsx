import { Link } from 'react-router-dom'

import { JobCard } from '../components/JobCard'
import { useJobs, type JobFilter } from '../hooks/useJobs'

const FILTERS: { key: JobFilter; label: string; to: string }[] = [
  { key: 'ALL', label: '全部', to: '/jobs' },
  { key: 'PENDING', label: '排队中', to: '/jobs/pending' },
  { key: 'RUNNING', label: '执行中', to: '/jobs/running' },
  { key: 'SUCCEEDED', label: '已完成', to: '/jobs/succeeded' },
  { key: 'FAILED', label: '失败', to: '/jobs/failed' },
]

/**
 * 「后台作业」页面（`job_queue`，D-013 术语约定的执行作业）。
 *
 * 这里显示的是 **Worker 执行的作业**（例如生成应用），不是用户记下的
 * 事项。用户事项在「我的任务」（`/user-tasks`）。标题与提示都刻意写明
 * 这一区别——此前本页叫「任务」，导致用户以为对话里创建的任务在这里。
 */
export function JobCenterPage({ filter }: { filter: JobFilter }) {
  const { state, actionError, busyId, reload, cancel, retry, clearActionError } = useJobs(filter)

  return (
    <section className="task-center">
      <header className="task-center__header">
        <h2>后台作业</h2>
        <span className="task-center__count">
          {state.loading ? '加载中…' : `共 ${state.total} 个`}
        </span>
      </header>

      <p className="task-center__notice">
        作业由本地服务在后台执行，<strong>关闭浏览器不影响</strong>；
        服务重启后未完成的作业会重新排队。
        你自己记下的待办事项在 <Link to="/tasks">我的任务</Link>。
      </p>

      <nav className="task-center__filters" aria-label="作业筛选">
        {FILTERS.map((item) => (
          <Link
            key={item.key}
            to={item.to}
            className={item.key === filter ? 'task-center__filter is-active' : 'task-center__filter'}
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
      ) : state.items.length === 0 ? (
        <div className="task-center__empty">
          <p>{state.loading ? '正在加载作业…' : '还没有后台作业。'}</p>
          {!state.loading && (
            <p>
              在 <Link to="/apps">应用</Link> 页描述你想要的小工具，生成会在后台进行。
            </p>
          )}
        </div>
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
