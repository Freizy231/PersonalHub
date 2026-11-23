import { useState } from 'react'

import type { ApprovalRequestEvent } from '../types/api'

const TOOL_LABEL: Record<string, string> = {
  create_task: '创建任务',
}

interface ApprovalCardProps {
  request: ApprovalRequestEvent
  busy: boolean
  onDecide: (decision: 'approve' | 'reject', remember: boolean) => void
}

/**
 * 对话中的审批卡片（M6 阻塞式交互）。
 *
 * 设计要点：
 * - 明确展示**将要执行什么**（工具名 + 参数摘要），而不是只问"允许吗"。
 * - "本次会话内不再询问"需用户显式勾选，且刷新页面即失效。
 * - 拒绝与批准同等醒目：按钮不做视觉诱导。
 */
export function ApprovalCard({ request, busy, onDecide }: ApprovalCardProps) {
  const [remember, setRemember] = useState(false)

  return (
    <section className="approval-card" role="alertdialog" aria-label="需要你的确认">
      <header className="approval-card__header">
        <span className="approval-card__dot" aria-hidden="true" />
        <strong>需要你确认</strong>
      </header>

      <p className="approval-card__action">
        AI 想执行：<strong>{TOOL_LABEL[request.tool] ?? request.tool}</strong>
      </p>

      <pre className="approval-card__preview">{request.preview}</pre>

      <label className="approval-card__remember">
        <input
          type="checkbox"
          checked={remember}
          onChange={(event) => setRemember(event.target.checked)}
          disabled={busy}
        />
        本次会话内不再询问（刷新页面后恢复询问）
      </label>

      <div className="approval-card__actions">
        <button type="button" onClick={() => onDecide('approve', remember)} disabled={busy}>
          {busy ? '处理中…' : '批准'}
        </button>
        <button
          type="button"
          className="danger"
          onClick={() => onDecide('reject', false)}
          disabled={busy}
        >
          拒绝
        </button>
      </div>
    </section>
  )
}
