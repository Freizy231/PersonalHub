import type { HealthState } from '../hooks/useHealth'

interface StatusCardProps {
  state: HealthState
  onRecheck: () => void
  checking: boolean
}

const STATUS_TEXT: Record<HealthState['status'], string> = {
  checking: '正在检查…',
  online: '已连接',
  offline: '未连接',
}

/**
 * 后端连接状态卡片。
 *
 * 纯展示组件：不发起请求，只渲染传入状态并回调刷新。
 */
export function StatusCard({ state, onRecheck, checking }: StatusCardProps) {
  return (
    <section className="status-card" aria-live="polite">
      <header className="status-card__header">
        <span className={`dot dot--${state.status}`} aria-hidden="true" />
        <h2 className="status-card__title">本地后端</h2>
        <span className="status-card__badge">{STATUS_TEXT[state.status]}</span>
      </header>

      {state.status === 'online' && (
        <dl className="status-card__meta">
          <div>
            <dt>服务</dt>
            <dd>{state.health.service}</dd>
          </div>
          <div>
            <dt>版本</dt>
            <dd>v{state.health.version}</dd>
          </div>
          <div>
            <dt>服务器时间</dt>
            <dd>
              <time dateTime={state.health.timestamp}>
                {new Date(state.health.timestamp).toLocaleString('zh-CN')}
              </time>
            </dd>
          </div>
        </dl>
      )}

      {state.status === 'offline' && (
        <div className="status-card__error" role="alert">
          <p>{state.message}</p>
          <p className="status-card__hint">
            请确认后端已启动：<code>uv run uvicorn app.main:app --host 127.0.0.1 --port 8000</code>
          </p>
          <p className="status-card__diag">
            错误码 <code>{state.code}</code>
            {state.requestId && (
              <>
                {' · '}请求 ID <code>{state.requestId}</code>
              </>
            )}
          </p>
        </div>
      )}

      <button type="button" onClick={onRecheck} disabled={checking}>
        {checking ? '检查中…' : '重新检查'}
      </button>
    </section>
  )
}
