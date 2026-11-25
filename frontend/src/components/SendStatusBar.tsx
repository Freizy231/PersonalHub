import {
  canRetry,
  failureText,
  phaseLabel,
  type SendState,
} from '../services/sendState'

interface SendStatusBarProps {
  state: SendState
  onRetry: () => void
  onDismiss: () => void
}

/**
 * 对话的发送状态提示（迭代 2 第 1 项）。
 *
 * 三种内容互斥：
 * - 进行中（发送/回复/停止中）：一行状态说明，不用 alert 角色——
 *   它是持续状态，不是需要立刻读的通知。
 * - 中断/失败：明确说明发生了什么、已收到的内容是否完整，并给出下一步。
 * - 其余：不渲染（不占位、不留空白条）。
 *
 * 关键约束：**重试入口只在服务端说"可重试"时出现**。配置类错误
 * （没配 Key、地址不对）给一个重试按钮，用户点几次都是同样失败，
 * 反而会怀疑是系统坏了。这类情况直接说"请检查配置"。
 */
export function SendStatusBar({ state, onRetry, onDismiss }: SendStatusBarProps) {
  const label = phaseLabel(state)
  if (label) {
    return (
      <p className="send-status" role="status" aria-live="polite">
        <span className="send-status__dot" aria-hidden="true" />
        {label}
      </p>
    )
  }

  if (state.phase !== 'interrupted' || !state.failure) return null

  const retryable = canRetry(state)

  return (
    <div className="send-status send-status--error" role="alert">
      <p className="send-status__message">{failureText(state.failure)}</p>
      <div className="send-status__actions">
        {retryable ? (
          <>
            <button type="button" onClick={onRetry}>
              重试
            </button>
            <button type="button" onClick={onDismiss}>
              不用了
            </button>
          </>
        ) : (
          <>
            <span className="send-status__hint">
              这种情况重发同样的内容不会成功，请先检查配置（见「设置」页）。
            </span>
            <button type="button" onClick={onDismiss}>
              知道了
            </button>
          </>
        )}
      </div>
      {state.failure.code && (
        <p className="send-status__code">错误码 {state.failure.code}</p>
      )}
    </div>
  )
}
