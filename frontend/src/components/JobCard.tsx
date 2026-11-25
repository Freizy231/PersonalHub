import { Link } from 'react-router-dom'

import type { Job, JobDetail } from '../types/api'
import {
  EVENT_LABEL,
  JOB_STATUS_LABEL,
  formatRelative,
  formatTime,
  isTerminal,
  jobTypeLabel,
  stageLabel,
} from './jobLabels'

interface JobCardProps {
  job: Job
  busy: boolean
  onCancel: (id: string) => void
  onRetry: (id: string) => void
}

function statusClass(status: string): string {
  return status.toLowerCase()
}

/**
 * 单个后台作业卡片。
 *
 * 主标题显示**用户可读的作业名称**（"生成应用"），内部标识只作为
 * 次要信息；输入摘要与阶段来自服务端，不在前端解析 payload。
 */
export function JobCard({ job, busy, onCancel, onRetry }: JobCardProps) {
  const finished = isTerminal(job.status)
  const title = jobTypeLabel(job.job_type)

  return (
    <li className={`job job--${statusClass(job.status)}`}>
      <div className="job__main">
        <span className="job__type">{title}</span>
        <span className={`job__status job__status--${statusClass(job.status)}`}>
          {JOB_STATUS_LABEL[job.status] ?? job.status}
        </span>
      </div>

      {job.summary && <p className="job__summary">{job.summary}</p>}

      {!finished && job.stage && (
        <p className="job__stage">
          当前阶段：{stageLabel(job.stage)}
          {job.stage_message && job.stage_message !== job.stage && (
            <span className="job__stage-detail">（{job.stage_message}）</span>
          )}
        </p>
      )}

      <div className="job__meta">
        <span>尝试 {job.attempts}/{job.max_attempts}</span>
        <span>更新于 {formatRelative(job.updated_at)}</span>
        <time dateTime={job.created_at}>{formatTime(job.created_at)}</time>
      </div>

      {job.last_error && <p className="job__error">失败原因：{job.last_error}</p>}

      <div className="job__actions">
        <Link className="job__detail-link" to={`/activity/${job.id}`}>
          查看详情
        </Link>
        {!finished && (
          <button type="button" className="danger" onClick={() => onCancel(job.id)} disabled={busy}>
            取消
          </button>
        )}
        {(job.status === 'FAILED' || job.status === 'CANCELLED') && (
          <button type="button" onClick={() => onRetry(job.id)} disabled={busy}>
            重试
          </button>
        )}
      </div>
    </li>
  )
}

/** 作业详情：执行实例与事件时间线。 */
export function JobDetailView({ detail }: { detail: JobDetail }) {
  return (
    <section className="job-detail">
      <dl className="job-detail__meta">
        <div>
          <dt>作业</dt>
          <dd>{jobTypeLabel(detail.job_type)}</dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>{JOB_STATUS_LABEL[detail.status] ?? detail.status}</dd>
        </div>
        <div>
          <dt>尝试</dt>
          <dd>
            {detail.attempts}/{detail.max_attempts}
          </dd>
        </div>
        <div>
          <dt>创建</dt>
          <dd>{formatTime(detail.created_at)}</dd>
        </div>
      </dl>

      {detail.summary && (
        <p className="job-detail__summary">
          输入摘要：<span>{detail.summary}</span>
        </p>
      )}

      <h3>执行实例</h3>
      {detail.runs.length === 0 ? (
        <p className="job-detail__empty">尚未执行。</p>
      ) : (
        <ul className="run-list">
          {detail.runs.map((run) => (
            <li key={run.id} className="run">
              <span>第 {run.attempt} 次</span>
              <span>{JOB_STATUS_LABEL[run.status] ?? run.status}</span>
              {run.error_code && <code>{run.error_code}</code>}
              <time dateTime={run.started_at}>{formatTime(run.started_at)}</time>
            </li>
          ))}
        </ul>
      )}

      <h3>事件</h3>
      {detail.events.length === 0 ? (
        <p className="job-detail__empty">没有事件。</p>
      ) : (
        <ol className="event-list">
          {detail.events.map((event) => (
            <li key={event.id} className={`event event--${event.event_type}`}>
              <span className="event__type">{EVENT_LABEL[event.event_type] ?? event.event_type}</span>
              <span className="event__message">{event.message}</span>
              <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
