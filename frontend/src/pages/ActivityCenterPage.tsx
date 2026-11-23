import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'

import { JobDetailView } from '../components/JobCard'
import { EVENT_LABEL, formatTime } from '../components/jobLabels'
import { api } from '../services/api'
import type { ActivityList, JobDetail } from '../types/api'

/**
 * Activity Center（AGENT.md §2.5）。
 *
 * 两种视图：
 * - /activity        最近事件的全局时间线
 * - /activity/{id}   单个作业的详情（执行实例 + 事件）
 *
 * 只展示元数据，不展示用户内容。
 */
export function ActivityCenterPage() {
  const { jobId } = useParams()
  const [events, setEvents] = useState<ActivityList | null>(null)
  const [detail, setDetail] = useState<JobDetail | null>(null)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const load = async () => {
      if (jobId) {
        const result = await api.jobs.get(jobId)
        if (!mounted.current) return
        if (result.ok) setDetail(result.data)
        else setError(result.message)
      } else {
        const result = await api.activity.list(50)
        if (!mounted.current) return
        if (result.ok) setEvents(result.data)
        else setError(result.message)
      }
    }
    void load()
    return () => {
      mounted.current = false
    }
  }, [jobId])

  return (
    <section className="activity-center">
      <header className="activity-center__header">
        <h2>{jobId ? '任务详情' : '活动'}</h2>
        {jobId && (
          <Link className="activity-center__back" to="/activity">
            ← 返回活动
          </Link>
        )}
      </header>

      {error ? (
        <div className="alert alert--error" role="alert">
          <p>{error}</p>
          <Link className="button" to="/activity">
            返回活动
          </Link>
        </div>
      ) : jobId ? (
        detail ? (
          <JobDetailView detail={detail} />
        ) : (
          <p className="activity-center__empty">正在加载…</p>
        )
      ) : events === null ? (
        <p className="activity-center__empty">正在加载…</p>
      ) : events.items.length === 0 ? (
        <p className="activity-center__empty">还没有活动记录。</p>
      ) : (
        <ol className="event-list">
          {events.items.map((event) => (
            <li key={event.id} className={`event event--${event.event_type}`}>
              <span className="event__type">{EVENT_LABEL[event.event_type] ?? event.event_type}</span>
              <Link className="event__job" to={`/activity/${event.job_id}`}>
                {event.job_id.slice(0, 8)}
              </Link>
              <span className="event__message">{event.message}</span>
              <time dateTime={event.created_at}>{formatTime(event.created_at)}</time>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
