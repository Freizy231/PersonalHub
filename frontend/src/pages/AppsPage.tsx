import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { AppInfo, StagedApp } from '../types/api'

/**
 * 应用中心（M9 第 5 步）：用一句话描述，生成一个带界面的小应用。
 *
 * ## 预览为什么用 iframe 的 src，而不是取 HTML 塞进 srcDoc
 *
 * 取文本再 `srcDoc` 完全可行，但那样**丢掉了后端的安全响应头**：
 * CSP 是随 HTTP 响应下发的，`srcDoc` 的内容没有响应头。
 * 虽然 iframe 的 `sandbox` 仍然生效，但"应用不能联网"这一条
 * 就只剩 CSP 兜底不了的一层——正好废掉了纵深防御的一半。
 *
 * 所以预览与运行都加载 `/api/v1/apps/.../content`：
 * 同样的 HTML、同样的 CSP、同样的 sandbox。预览不是"先放松再收紧"。
 *
 * ## sandbox 为什么只有 allow-scripts
 *
 * 不加 `allow-same-origin`，iframe 就是**不透明来源**：
 * 应用无法读父页面 DOM、无法用 localStorage/cookie、无法调用后端 API。
 * 一旦加上 `allow-same-origin` 且与主页面同源，应用就能拿到父文档与其身份——
 * 那等于把第三方代码请进内核。
 *
 * ## 生成很慢，但仍要有明确结束
 *
 * 生成要走两次模型调用（需求 → 生成），因此请求超时放宽到 3 分钟，
 * 并在界面上显示"正在进行第几步"，避免用户以为是卡死。
 */
export function AppsPage() {
  const [apps, setApps] = useState<AppInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [prompt, setPrompt] = useState('')
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [staged, setStaged] = useState<StagedApp | null>(null)

  const [busyId, setBusyId] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const [confirmUninstall, setConfirmUninstall] = useState<string | null>(null)

  const mounted = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    const result = await api.apps.list()
    if (!mounted.current) return
    if (result.ok) {
      setApps(result.data.items)
      setLoadError(null)
    } else {
      setLoadError(result.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    return () => {
      mounted.current = false
    }
  }, [load])

  async function handleGenerate() {
    const text = prompt.trim()
    if (!text || generating) return

    setGenerating(true)
    setGenError(null)
    setNotice(null)
    setStaged(null)

    const result = await api.apps.generate(text)
    if (!mounted.current) return
    setGenerating(false)

    if (result.ok) {
      setStaged(result.data)
    } else {
      // 后端已把具体原因（含静态检查的逐条问题）写在 message 里，原样展示。
      setGenError(result.message)
    }
  }

  async function handleInstall() {
    if (!staged) return
    setBusyId('install')
    const result = await api.apps.install(staged.staging_id)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setStaged(null)
      setPrompt('')
      setNotice(`${result.data.message} 它已经出现在下方列表里。`)
      await load()
    } else {
      setGenError(result.message)
    }
  }

  async function handleDiscard() {
    if (!staged) return
    setBusyId('discard')
    const result = await api.apps.discard(staged.staging_id)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setStaged(null)
      setNotice('已丢弃这次生成，没有保存任何东西。')
    } else {
      setGenError(result.message)
    }
  }

  async function handleRollback(app: AppInfo) {
    setBusyId(app.id)
    const result = await api.apps.rollback(app.id)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setNotice(result.data.message)
      await load()
    } else {
      setLoadError(result.message)
    }
  }

  async function handleUninstall(app: AppInfo) {
    setBusyId(app.id)
    const result = await api.apps.remove(app.id)
    if (!mounted.current) return
    setBusyId(null)
    setConfirmUninstall(null)
    if (result.ok) {
      setNotice(result.data.message)
      await load()
    } else {
      setLoadError(result.message)
    }
  }

  return (
    <section className="app-center">
      <header className="app-center__header">
        <h2>应用</h2>
        <span className="app-center__count">{apps.length} 个</span>
      </header>

      <p className="app-center__notice">
        用一句话描述你想要的页面，系统会生成一个单文件小应用。应用在<strong>完全隔离</strong>
        的沙箱中运行：不能联网、不能读取你的数据、看不到主界面。生成后先预览，确认满意再安装。
      </p>

      <form
        className="app-builder"
        onSubmit={(event) => {
          event.preventDefault()
          void handleGenerate()
        }}
      >
        <label className="app-builder__label" htmlFor="app-prompt">
          你想做什么？
        </label>
        <textarea
          id="app-prompt"
          className="app-builder__input"
          value={prompt}
          rows={3}
          maxLength={1000}
          placeholder="例如：做一个番茄钟计时器，可以开始、暂停和重置"
          disabled={generating}
          onChange={(event) => setPrompt(event.target.value)}
        />
        <div className="app-builder__actions">
          <span className="app-builder__hint">
            只能做纯前端展示或计算，不能保存数据、不能联网。
          </span>
          <button type="submit" className="button" disabled={generating || !prompt.trim()}>
            {generating ? '正在生成…' : '生成'}
          </button>
        </div>
        {generating && (
          <p className="app-builder__progress">
            正在解析需求并生成页面，通常需要十几秒到一分钟，请不要关闭页面。
          </p>
        )}
      </form>

      {genError && (
        <div className="alert alert--error" role="alert">
          <p>{genError}</p>
        </div>
      )}

      {notice && (
        <div className="alert alert--info" role="status">
          <p>{notice}</p>
        </div>
      )}

      {staged && (
        <div className="app-preview">
          <div className="app-preview__meta">
            <h3 className="app-preview__title">{staged.title}</h3>
            <p className="app-preview__purpose">{staged.purpose}</p>
            <p className="app-preview__row">
              <span className="app-preview__label">界面</span>
              {staged.elements.join('、')}
            </p>
            <p className="app-preview__row">
              <span className="app-preview__label">类型</span>
              {staged.capability === 'local_compute' ? '本地计算' : '只读展示'}
            </p>
          </div>

          <AppFrame
            src={api.apps.stagingContentUrl(staged.staging_id)}
            title={`预览：${staged.title}`}
          />

          <div className="app-preview__actions">
            <button
              type="button"
              className="button"
              disabled={busyId !== null}
              onClick={() => void handleInstall()}
            >
              {busyId === 'install' ? '安装中…' : '安装这个应用'}
            </button>
            <button
              type="button"
              className="button"
              disabled={busyId !== null}
              onClick={() => void handleDiscard()}
            >
              丢弃
            </button>
            <span className="app-preview__sandbox-hint">
              预览已经开始受沙箱限制：应用此刻就看不到你的数据。
            </span>
          </div>
        </div>
      )}

      <h3 className="app-center__subtitle">已安装</h3>

      {loading && <p className="app-center__empty">正在加载…</p>}
      {loadError && (
        <div className="alert alert--error" role="alert">
          <p>{loadError}</p>
        </div>
      )}
      {!loading && !loadError && apps.length === 0 && (
        <p className="app-center__empty">还没有安装任何应用。上面生成一个试试。</p>
      )}

      <ul className="app-list">
        {apps.map((app) => (
          <li key={app.id} className={app.ok ? 'app-item' : 'app-item app-item--broken'}>
            <div className="app-item__main">
              <span className="app-item__name">{app.name}</span>
              <span className="app-item__version">{app.version ?? '不可用'}</span>
              <span className="app-item__count">{app.versions.length} 个版本</span>
            </div>
            {app.description && <p className="app-item__desc">{app.description}</p>}
            {!app.ok && (
              <p className="app-item__error">
                当前版本不可用（文件可能被移动或损坏）。可以回滚到上一个版本。
              </p>
            )}

            {app.ok && (
              <AppFrame src={api.apps.contentUrl(app.id)} title={`运行：${app.name}`} />
            )}

            <div className="app-item__actions">
              {app.versions.length > 1 && (
                <button
                  type="button"
                  className="button"
                  disabled={busyId !== null}
                  onClick={() => void handleRollback(app)}
                >
                  {busyId === app.id ? '处理中…' : '回滚到上一版本'}
                </button>
              )}

              {confirmUninstall === app.id ? (
                <>
                  <span className="app-item__confirm-text">卸载后目录会保留，确定吗？</span>
                  <button
                    type="button"
                    className="button danger"
                    disabled={busyId !== null}
                    onClick={() => void handleUninstall(app)}
                  >
                    确认卸载
                  </button>
                  <button
                    type="button"
                    className="button"
                    onClick={() => setConfirmUninstall(null)}
                  >
                    取消
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="button"
                  disabled={busyId !== null}
                  onClick={() => setConfirmUninstall(app.id)}
                >
                  卸载
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>
    </section>
  )
}

/**
 * 运行应用用的 iframe。
 *
 * `sandbox="allow-scripts"` 是**唯一**必要的授权：应用需要执行自己的脚本，
 * 但不应获得同源身份。刻意不写 `allow-same-origin`、`allow-popups`、
 * `allow-top-navigation`、`allow-forms`。
 *
 * `referrerPolicy="no-referrer"`：即使 CSP 失效，也不要向任何地方泄露来源地址。
 */
function AppFrame({ src, title }: { src: string; title: string }) {
  return (
    <iframe
      className="app-frame"
      src={src}
      title={title}
      sandbox="allow-scripts"
      referrerPolicy="no-referrer"
      loading="lazy"
    />
  )
}
