import { Link } from 'react-router-dom'

import { formatRelative, isTerminal, jobTypeLabel, stageLabel } from '../components/jobLabels'
import { PageHeader } from '../components/PageHeader'
import { StatusCard } from '../components/StatusCard'
import { toolLabel } from '../components/toolLabels'
import { useHealth } from '../hooks/useHealth'
import { WORKBENCH_LIMIT, useWorkbench } from '../hooks/useWorkbench'

/**
 * 首页 = 工作台。
 *
 * 首屏回答四个问题（《前端体验开发周期》迭代 1）：
 * 1. 我有什么事没做？        → 我的任务（tasks）
 * 2. 有什么在等我确认？      → 待审批
 * 3. AI 正在做什么/刚失败？  → 后台作业（job_queue）
 * 4. 我上次聊到哪了？        → 继续最近会话
 *
 * 四个模块并行加载、独立报错；每个模块都有空状态与下一步入口。
 */
export function HomePage() {
  const { state, recheck } = useHealth()
  const { state: bench, reload } = useWorkbench()

  const activeJobs = bench.jobs.filter(
    (job) => job.status === 'PENDING' || job.status === 'RUNNING',
  )
  const failedJobs = bench.jobs.filter((job) => job.status === 'FAILED')

  return (
    <>
      <PageHeader
        title="工作台"
        description="接下来要做的事、等你确认的操作，以及后台正在跑的作业。"
        actions={
          <button type="button" className="workbench__refresh" onClick={reload} disabled={bench.loading}>
            刷新
          </button>
        }
      />

      <section className="workbench" aria-labelledby="workbench-title">
        <h2 id="workbench-title" className="section-title">
          今天从这里开始
        </h2>

        <div className="workbench__grid">
          <article className="workbench-card workbench-card--tasks" aria-labelledby="wb-tasks">
            <header className="workbench-card__header">
              <h3 id="wb-tasks">我的任务</h3>
              <Link className="workbench-card__more" to="/tasks">
                查看全部
              </Link>
            </header>
            {bench.errors.tasks ? (
              <ModuleError message={bench.errors.tasks} onRetry={reload} />
            ) : bench.openTasks.length === 0 ? (
              <div className="workbench-card__empty">
                <p>没有未完成的任务。</p>
                <Link to="/conversations">在会话里让 AI 记一件</Link>
              </div>
            ) : (
              <ul className="workbench-list">
                {bench.openTasks.slice(0, WORKBENCH_LIMIT).map((task) => (
                  <li key={task.id} className="workbench-list__item">
                    <span className="workbench-list__title">{task.title}</span>
                    <span className="workbench-list__meta">{formatRelative(task.updated_at)}</span>
                  </li>
                ))}
              </ul>
            )}
          </article>

          <article className="workbench-card workbench-card--approvals" aria-labelledby="wb-approvals">
            <header className="workbench-card__header">
              <h3 id="wb-approvals">待确认</h3>
              <span className="workbench-card__badge">{bench.pendingApprovals.length}</span>
            </header>
            {bench.errors.approvals ? (
              <ModuleError message={bench.errors.approvals} onRetry={reload} />
            ) : bench.pendingApprovals.length === 0 ? (
              <p className="workbench-card__empty">没有等待你确认的操作。</p>
            ) : (
              <>
                <ul className="workbench-list">
                  {bench.pendingApprovals.slice(0, WORKBENCH_LIMIT).map((approval) => (
                    <li key={approval.id} className="workbench-list__item">
                      <span className="workbench-list__title">
                        {approval.arguments_preview || toolLabel(approval.tool_name)}
                      </span>
                      <span className="workbench-list__meta">在对话中查看</span>
                    </li>
                  ))}
                </ul>
                <p className="workbench-card__hint">
                  确认卡片出现在发起它的对话里，请回到对应会话处理。
                </p>
              </>
            )}
          </article>

          <article className="workbench-card workbench-card--jobs" aria-labelledby="wb-jobs">
            <header className="workbench-card__header">
              <h3 id="wb-jobs">后台作业</h3>
              <Link className="workbench-card__more" to="/jobs">
                查看全部
              </Link>
            </header>
            {bench.errors.jobs ? (
              <ModuleError message={bench.errors.jobs} onRetry={reload} />
            ) : bench.jobs.length === 0 ? (
              <div className="workbench-card__empty">
                <p>没有正在进行或最近失败的作业。</p>
                <Link to="/apps">生成一个小应用</Link>
              </div>
            ) : (
              <ul className="workbench-list">
                {bench.jobs.slice(0, WORKBENCH_LIMIT).map((job) => (
                  <li key={job.id} className="workbench-list__item">
                    <Link className="workbench-list__title" to={`/activity/${job.id}`}>
                      {jobTypeLabel(job.job_type)}
                    </Link>
                    <span
                      className={`workbench-list__meta workbench-list__meta--${job.status.toLowerCase()}`}
                    >
                      {!isTerminal(job.status) && job.stage
                        ? stageLabel(job.stage)
                        : job.status === 'FAILED'
                          ? '失败'
                          : job.status === 'SUCCEEDED'
                            ? '已完成'
                            : '已取消'}
                    </span>
                  </li>
                ))}
              </ul>
            )}
            {(activeJobs.length > 0 || failedJobs.length > 0) && (
              <p className="workbench-card__hint">
                进行中 {activeJobs.length} 个
                {failedJobs.length > 0 && ` · 失败 ${failedJobs.length} 个`}
              </p>
            )}
          </article>

          <article className="workbench-card workbench-card--conversations" aria-labelledby="wb-conversations">
            <header className="workbench-card__header">
              <h3 id="wb-conversations">继续对话</h3>
              <Link className="workbench-card__more" to="/conversations">
                查看全部
              </Link>
            </header>
            {bench.errors.conversations ? (
              <ModuleError message={bench.errors.conversations} onRetry={reload} />
            ) : bench.conversations.length === 0 ? (
              <div className="workbench-card__empty">
                <p>还没有会话。</p>
                <Link to="/conversations">开始第一次对话</Link>
              </div>
            ) : (
              <ul className="workbench-list">
                {bench.conversations.slice(0, WORKBENCH_LIMIT).map((conversation) => (
                  <li key={conversation.id} className="workbench-list__item">
                    <Link
                      className="workbench-list__title"
                      to={`/conversations/${conversation.id}`}
                    >
                      {conversation.title}
                    </Link>
                    <span className="workbench-list__meta">
                      更新于 {formatRelative(conversation.updated_at)}
                    </span>
                  </li>
                ))}
              </ul>
            )}
          </article>
        </div>
      </section>

      <StatusCard state={state} onRecheck={recheck} checking={state.status === 'checking'} />
    </>
  )
}

function ModuleError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="workbench-card__error" role="alert">
      <p>{message}</p>
      <button type="button" onClick={onRetry}>
        重试
      </button>
    </div>
  )
}
