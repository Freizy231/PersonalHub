import { useEffect, useState } from 'react'

import type { ApprovalRequestEvent } from '../types/api'
import { toolLabel } from './toolLabels'

interface ApprovalCardProps {
  request: ApprovalRequestEvent
  busy: boolean
  onDecide: (decision: 'approve' | 'reject', remember: boolean) => void
  /** 审批到期后通知上层收起卡片，避免留下一个点了就报错的死按钮。 */
  onExpired?: () => void
}

/**
 * 对话中的审批卡片（M6 阻塞式交互）。
 *
 * 设计要点（D-012）：
 * - 展示**人话摘要**：中文工具名 + 逐项「标签：值」，不再是 JSON 结构体。
 *   审批是给人看的，`{"title": "…"}` 只会增加阅读负担。
 * - 原始参数保留在折叠区里，默认收起：需要精确核对时有入口，
 *   不需要时不会干扰阅读。
 * - 显示剩余有效时间；到期自动收起，并说明"已过期，请重新发起"。
 * - 拒绝与批准同等醒目：按钮不做视觉诱导。
 */
export function ApprovalCard({ request, busy, onDecide, onExpired }: ApprovalCardProps) {
  const [remember, setRemember] = useState(false)
  // 由父组件用 approval_id 作 key，换一条审批就重新挂载，倒计时自然归零。
  const [remaining, setRemaining] = useState(request.expires_in)
  const [showRaw, setShowRaw] = useState(false)

  // 倒计时：审批有 TTL（默认 10 分钟），过期后决策会返回 409。
  // 与其让用户点了才看到报错，不如提前把时间显示出来。
  useEffect(() => {
    const timer = window.setInterval(() => {
      setRemaining((prev) => Math.max(0, prev - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    if (remaining === 0 && !busy) onExpired?.()
  }, [remaining, busy, onExpired])

  const expired = remaining === 0
  // 摘要优先用后端给的结构化项；旧数据没有 summary 时退回多行文本。
  const summary = request.summary ?? []
  const rawArguments = request.arguments ?? {}

  return (
    <section
      className={`approval-card${expired ? ' approval-card--expired' : ''}`}
      role="alertdialog"
      aria-label="需要你的确认"
    >
      <header className="approval-card__header">
        <span className="approval-card__dot" aria-hidden="true" />
        <strong>{expired ? '审批已过期' : '需要你确认'}</strong>
        <span className="approval-card__timer" aria-live="off">
          {expired ? '请重新发起' : `${formatRemaining(remaining)} 后失效`}
        </span>
      </header>

      <p className="approval-card__action">
        AI 想执行：<strong>{toolLabel(request.tool, request.title)}</strong>
      </p>

      {summary.length > 0 ? (
        <dl className="approval-card__summary">
          {summary.map((item) => (
            <div key={item.label} className="approval-card__field">
              <dt>{item.label}</dt>
              <dd>{item.value}</dd>
            </div>
          ))}
        </dl>
      ) : (
        // 兼容旧后端：没有结构化摘要时只显示文本。
        <pre className="approval-card__preview">{request.preview}</pre>
      )}

      {Object.keys(rawArguments).length > 0 && (
        <details className="approval-card__raw" open={showRaw} onToggle={(e) => setShowRaw(e.currentTarget.open)}>
          <summary>查看原始参数</summary>
          <pre className="approval-card__preview">{JSON.stringify(rawArguments, null, 2)}</pre>
        </details>
      )}

      <label className="approval-card__remember">
        <input
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
          disabled={busy || expired}
        />
        本次会话内不再询问（刷新页面后恢复询问）
      </label>

      <div className="approval-card__actions">
        <button
          type="button"
          onClick={() => onDecide('approve', remember)}
          disabled={busy || expired}
        >
          {busy ? '处理中…' : '批准'}
        </button>
        <button
          type="button"
          className="danger"
          onClick={() => onDecide('reject', false)}
          disabled={busy || expired}
        >
          拒绝
        </button>
      </div>
    </section>
  )
}

function formatRemaining(seconds: number): string {
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60)
    const rest = seconds % 60
    return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`
  }
  return `${seconds} 秒`
}
