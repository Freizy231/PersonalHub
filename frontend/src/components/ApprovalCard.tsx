import { useEffect, useRef, useState } from 'react'

import type { ApprovalOutcome, ApprovalRequestEvent } from '../types/api'
import { toolLabel } from './toolLabels'

interface ApprovalCardProps {
  request: ApprovalRequestEvent
  busy: boolean
  onDecide: (decision: 'approve' | 'reject', remember: boolean) => void
  /** 卡片到期时上报，用于在卡片内呈现"已过期"，而不是让它凭空消失。 */
  onExpire?: () => void
  /** 已做出的决定（或失败原因）。非 null 时卡片进入终态，不再接受操作。 */
  outcome?: ApprovalOutcome | null
}

/**
 * 对话中的审批卡片（M6 阻塞式交互 + 迭代 2 第 2 项）。
 *
 * 设计要点：
 * - **人话摘要**：中文工具名 + 逐项「标签：值」，不是 JSON（D-012）。
 * - **会发生什么**：`impact` 由工具自己声明（后端 `ToolDefinition.impact`）。
 *   没声明时不编造，这一段直接不显示。
 * - **原始参数**折叠，默认收起：需要精确核对时有入口。
 * - **过期后卡片保留**：显示"已过期，未执行"，而不是突然消失。
 *   实测缺陷：原实现在 remaining 归零时立刻卸载卡片，用户看不到任何解释。
 * - **失败就地提示**：409（已过期/已被处理）等错误显示在卡片里，
 *   而不是写到页面另一处。
 * - 拒绝与批准同等醒目：不做视觉诱导。
 */
export function ApprovalCard({ request, busy, onDecide, onExpire, outcome }: ApprovalCardProps) {
  const [remember, setRemember] = useState(false)
  // 由父组件用 approval_id 作 key，换一条审批就重新挂载，倒计时自然归零。
  const [remaining, setRemaining] = useState(request.expires_in)
  const reportedRef = useRef(false)

  // 倒计时：审批有 TTL（默认 10 分钟），过期后决策会返回 409。
  // 与其让用户点了才看到报错，不如提前把时间显示出来。
  //
  // 注意：**不在这里卸载自己**。归零只上报一次，由父组件决定呈现方式。
  useEffect(() => {
    if (outcome) return
    const timer = window.setInterval(() => {
      setRemaining((prev) => Math.max(0, prev - 1))
    }, 1000)
    return () => window.clearInterval(timer)
  }, [outcome])

  useEffect(() => {
    if (remaining === 0 && !busy && !outcome && !reportedRef.current) {
      reportedRef.current = true
      onExpire?.()
    }
  }, [remaining, busy, outcome, onExpire])

  const expired = !outcome && remaining === 0
  const settled = outcome != null
  const locked = busy || expired || settled
  const summary = request.summary ?? []
  const rawArguments = request.arguments ?? {}
  const title = toolLabel(request.tool, request.title)

  return (
    <section
      className={`approval-card${expired ? ' approval-card--expired' : ''}${settled ? ' approval-card--settled' : ''}`}
      role="alertdialog"
      aria-label="需要你的确认"
    >
      <header className="approval-card__header">
        <span className="approval-card__dot" aria-hidden="true" />
        <strong>{headerText(outcome, expired)}</strong>
        <span className="approval-card__timer" aria-live="off">
          {outcome
            ? ''
            : expired
              ? '本次确认未生效'
              : `${formatRemaining(remaining)} 后失效`}
        </span>
      </header>

      {outcome ? (
        <OutcomeBody outcome={outcome} />
      ) : (
        <>
          <p className="approval-card__action">
            AI 想执行：<strong>{title}</strong>
          </p>

          {request.impact && (
            <p className="approval-card__impact">
              <span className="approval-card__impact-label">会发生什么</span>
              {request.impact}
            </p>
          )}

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
            <details className="approval-card__raw">
              <summary>查看原始参数</summary>
              <pre className="approval-card__preview">
                {JSON.stringify(rawArguments, null, 2)}
              </pre>
            </details>
          )}

          <label className="approval-card__remember">
            <input
              type="checkbox"
              checked={remember}
              onChange={(event) => setRemember(event.target.checked)}
              disabled={locked}
            />
            本次会话内不再询问（刷新页面后恢复询问）
          </label>

          <div className="approval-card__actions">
            <button
              type="button"
              onClick={() => onDecide('approve', remember)}
              disabled={locked}
            >
              {busy ? '处理中…' : '批准'}
            </button>
            <button
              type="button"
              className="danger"
              onClick={() => onDecide('reject', false)}
              disabled={locked}
            >
              拒绝
            </button>
          </div>

          {expired && (
            <p className="approval-card__notice" role="status">
              这个确认已经失效，<strong>操作没有被执行</strong>。
              如果仍然需要，请让 AI 重新发起。
            </p>
          )}
        </>
      )}
    </section>
  )
}

function headerText(outcome: ApprovalOutcome | null | undefined, expired: boolean): string {
  if (outcome) {
    switch (outcome.kind) {
      case 'approved':
        return '已批准'
      case 'rejected':
        return '已拒绝'
      case 'expired':
        return '确认已失效'
      case 'failed':
        return '操作未完成'
    }
  }
  return expired ? '确认已过期' : '需要你确认'
}

/**
 * 终态说明。
 *
 * 关键措辞：**不能声称"已完成"**。批准之后工具才真正开始执行，
 * 执行结果会以消息形式出现；这里只能说"已批准，正在执行"。
 * 拒绝要说清楚"什么都没有发生"，否则用户会担心是否已经执行了一半。
 */
function OutcomeBody({ outcome }: { outcome: ApprovalOutcome }) {
  switch (outcome.kind) {
    case 'approved':
      return (
        <p className="approval-card__notice approval-card__notice--ok" role="status">
          已批准「{outcome.title}」。AI 正在执行，结果会出现在下面的回复里。
        </p>
      )
    case 'rejected':
      return (
        <p className="approval-card__notice" role="status">
          已拒绝「{outcome.title}」。<strong>这个操作没有被执行</strong>，AI 会被告知你的决定。
        </p>
      )
    case 'expired':
      return (
        <p className="approval-card__notice" role="status">
          这个确认已经失效，<strong>操作没有被执行</strong>。
          如果仍然需要，请让 AI 重新发起。
        </p>
      )
    case 'failed':
      return (
        <p className="approval-card__notice approval-card__notice--error" role="alert">
          操作未完成：{outcome.message}
          <br />
          请重试，或让 AI 重新发起这次请求。
        </p>
      )
  }
}

function formatRemaining(seconds: number): string {
  if (seconds >= 60) {
    const minutes = Math.floor(seconds / 60)
    const rest = seconds % 60
    return rest === 0 ? `${minutes} 分钟` : `${minutes} 分 ${rest} 秒`
  }
  return `${seconds} 秒`
}
