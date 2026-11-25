import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'

import { FlowSteps, PendingStaging, StagedPreview } from '../components/AppFlow'
import { api } from '../services/api'
import { computeSteps } from '../services/appFlow'
import {
  forgetPendingJob,
  loadPendingJob,
  rememberPendingJob,
} from '../services/pendingJob'
import type { AppInfo, JobDetail, SimilarApp, StagedApp } from '../types/api'

/**
 * 应用中心：用一句话描述，生成一个带界面的小应用。
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
 * ## 生成为什么是后台作业
 *
 * 两次模型调用的总耗时常超过十秒。同步接口会让用户必须停在页面上等，
 * 而且请求一旦被代理或浏览器掐断，结果就丢了。现在：
 *
 *   提交（毫秒级）→ 轮询 GET /jobs/{id} → 成功则读取 staging 预览
 *
 * 用户可以去做别的事再回来；关闭浏览器也不影响后台生成（Worker 在服务端）。
 * 提交的作业 id 记在 sessionStorage 里，**刷新页面后仍能接上进度**——
 * 否则"可以离开"就是一句空话（实测发现：结果只在组件状态里，一刷新就没了）。
 */

/** 轮询间隔。生成通常十几秒，1.5 秒一次足够，也不会给 SQLite 添压力。 */
const POLL_INTERVAL_MS = 1500
/** 轮询上限：约 5 分钟。超过就认为作业卡住了，提示用户去活动中心看。 */
const POLL_MAX_MS = 5 * 60 * 1000

const TERMINAL = new Set(['SUCCEEDED', 'FAILED', 'CANCELLED'])

export function AppsPage() {
  const [apps, setApps] = useState<AppInfo[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [prompt, setPrompt] = useState('')
  const [generating, setGenerating] = useState(false)
  const [genError, setGenError] = useState<string | null>(null)
  const [staged, setStaged] = useState<StagedApp | null>(null)
  /** 正在轮询的作业。用于显示"可以去做别的事"和提供取消入口。 */
  const [activeJob, setActiveJob] = useState<JobDetail | null>(null)
  /**
   * 提交时发现相似应用：先问用户要不要复用，而不是直接生成。
   * 保留原始描述，用户选择"仍然重新生成"时要用它再提交一次。
   */
  const [reuseHint, setReuseHint] = useState<{ prompt: string; apps: SimilarApp[] } | null>(
    null,
  )

  /** 尚未安装的生成结果（跨标签页可找回）。 */
  const [pending, setPending] = useState<StagedApp[]>([])

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

  const loadPending = useCallback(async () => {
    const result = await api.apps.pending()
    if (mounted.current && result.ok) setPending(result.data.items)
  }, [])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    // oxlint-disable-next-line react/set-state-in-effect
    void loadPending()
    // 刷新后接上未完成的生成。这一步让"可以离开这个页面"成为真话。
    // oxlint-disable-next-line react/set-state-in-effect
    void resumePendingJob()
    return () => {
      mounted.current = false
    }
    // resumePendingJob 是稳定的（只读存储 + 调接口），不需要进依赖。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [load])

  async function handleGenerate(force = false) {
    const text = prompt.trim()
    if (!text || generating) return

    setGenerating(true)
    setGenError(null)
    setNotice(null)
    setStaged(null)

    const submitted = await api.apps.generate(text, force)
    if (!mounted.current) return

    if (!submitted.ok) {
      // 前置筛查（超范围、空描述）在这里就会失败，不必等一次作业。
      setGenerating(false)
      setGenError(submitted.message)
      return
    }

    // 「优先复用」：后端发现已安装的应用已能满足需求时**没有提交作业**。
    // 这里只提示，决定权交给用户——不替他决定"用旧的"或"造新的"。
    if (submitted.data.skipped) {
      setGenerating(false)
      setReuseHint({ prompt: text, apps: submitted.data.similar })
      return
    }
    setReuseHint(null)

    const { job_id: jobId, staging_id: stagingId } = submitted.data
    rememberPendingJob({ jobId, stagingId })

    const finished = await pollJob(jobId)
    if (!mounted.current) return
    setGenerating(false)
    setActiveJob(null)
    forgetPendingJob()

    if (finished === null) {
      // 轮询超时。**不说"失败"**——作业可能还在跑，说失败就是在撒谎。
      setNotice('生成仍在后台进行。可以稍后到「活动」页查看结果。')
      return
    }
    // 作业成功：从 staging 读取摘要。预览内容仍只经 iframe 加载。
    void handleFinishedJob(finished, stagingId)
  }

  /**
   * 刷新后接上未完成的生成。
   *
   * 只在组件挂载时调用。作业可能已经结束（哪怕用户离开了很久），
   * 所以先查一次状态：成功就直接展示预览，不必再从"生成中"开始。
   */
  async function resumePendingJob() {
    const pending = loadPendingJob()
    if (pending === null) return

    setGenerating(true)
    const detail = await api.jobs.get(pending.jobId)
    if (!mounted.current) return

    if (!detail.ok) {
      // 作业记录不见了（数据库被清理？）：不再纠缠，让用户重新提交。
      forgetPendingJob()
      setGenerating(false)
      if (detail.code !== 'NOT_FOUND' && detail.code !== 'HTTP_404') {
        setGenError(detail.message)
      }
      return
    }

    const job = detail.data
    if (TERMINAL.has(job.status)) {
      forgetPendingJob()
      setGenerating(false)
      void handleFinishedJob(job, pending.stagingId)
      return
    }

    const finished = await pollJob(pending.jobId)
    if (!mounted.current) return
    forgetPendingJob()
    setGenerating(false)
    setActiveJob(null)
    if (finished === null) {
      setNotice('生成仍在后台进行。可以稍后到「活动」页查看结果。')
      return
    }
    void handleFinishedJob(finished, pending.stagingId)
  }

  /** 终态作业的统一处理：成功展示预览，取消/失败给出原因。 */
  async function handleFinishedJob(job: JobDetail, stagingId: string) {
    if (job.status === 'CANCELLED') {
      setNotice('已取消这次生成，没有保存任何东西。')
      return
    }
    if (job.status !== 'SUCCEEDED') {
      setGenError(job.last_error ?? '生成失败，请稍后重试。')
      return
    }
    await showStaged(stagingId)
  }

  /** 轮询到作业结束。返回 null 表示超时（不表示失败）。 */
  async function pollJob(jobId: string): Promise<JobDetail | null> {
    const deadline = Date.now() + POLL_MAX_MS
    while (Date.now() < deadline) {
      const result = await api.jobs.get(jobId)
      if (!mounted.current) return null
      if (!result.ok) {
        setGenError(result.message)
        return null
      }
      const detail = result.data
      setActiveJob(detail)
      if (TERMINAL.has(detail.status)) return detail
      await sleep(POLL_INTERVAL_MS)
      if (!mounted.current) return null
    }
    return null
  }

  /**
   * 读取一次生成的完整摘要（迭代 3）。
   *
   * 直接使用服务端返回的字段：标题、用途、要素、版本预测与改动幅度都由后端
   * 算好。此前这里只能拿到 title 与 size，其余字段被填成空串——
   * 界面因此无法说明"这是什么、装上会变成哪个版本"。
   */
  async function showStaged(stagingId: string) {
    const detail = await api.apps.staged(stagingId)
    if (!mounted.current) return
    if (!detail.ok) {
      setGenError(detail.message)
      return
    }
    setStaged(detail.data)
    // 打开预览后重算"最近的生成"：正在看的那条要从列表里消失。
    void loadPending()
  }

  async function handleCancelJob() {
    if (!activeJob) return
    const result = await api.jobs.cancel(activeJob.id)
    if (!mounted.current) return
    if (!result.ok) setGenError(result.message)
    // 不在这里把界面切回空闲：轮询会在下一个周期读到 CANCELLED，
    // 由它统一收尾——否则两处各写一次状态，容易出现不一致。
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
      await loadPending()
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
      await loadPending()
    } else {
      setGenError(result.message)
    }
  }

  /** 丢弃"最近的生成"里的某一条（不是当前预览的那一条）。 */
  async function handleDiscardStaging(stagingId: string) {
    setBusyId(stagingId)
    const result = await api.apps.discard(stagingId)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setNotice('已丢弃这次生成，没有保存任何东西。')
      await loadPending()
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

      <FlowSteps
        steps={computeSteps({
          hasPrompt: prompt.trim().length > 0,
          generating,
          failure: genError,
          staged,
          installing: busyId === 'install',
        })}
      />

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
          <div className="app-builder__progress" role="status">
            <p>
              生成在后台进行，可以<strong>离开这个页面</strong>，回来时结果仍在这里。
              进度也可以到 <Link to="/jobs">后台作业</Link> 里看。
            </p>
            <p className="app-builder__stage">{stageLabel(activeJob)}</p>
            {activeJob && (
              <button
                type="button"
                className="app-builder__cancel"
                onClick={() => void handleCancelJob()}
              >
                取消生成
              </button>
            )}
          </div>
        )}
      </form>

      {genError && (
        <div className="alert alert--error" role="alert">
          <p>{genError}</p>
        </div>
      )}

      {reuseHint && (
        <div className="app-reuse" role="status">
          <p className="app-reuse__title">你已经有一个可能满足需求的应用：</p>
          <ul className="app-reuse__list">
            {reuseHint.apps.map((item) => (
              <li key={item.app_id} className="app-reuse__item">
                <span className="app-reuse__name">{item.name}</span>
                <span className="app-reuse__desc">{item.description}</span>
                <button
                  type="button"
                  className="button"
                  onClick={() => {
                    setReuseHint(null)
                    setNotice(`已使用「${item.name}」。它就在下面的列表里。`)
                  }}
                >
                  用这个
                </button>
              </li>
            ))}
          </ul>
          <div className="app-reuse__actions">
            <span className="app-reuse__hint">
              重新生成会再花一次模型调用，并新增一个独立的应用。
            </span>
            <button
              type="button"
              className="button"
              onClick={() => {
                setReuseHint(null)
                void handleGenerate(true)
              }}
            >
              仍然重新生成
            </button>
            <button type="button" className="button" onClick={() => setReuseHint(null)}>
              取消
            </button>
          </div>
        </div>
      )}

      {notice && (
        <div className="alert alert--info" role="status">
          <p>{notice}</p>
        </div>
      )}

      {staged && (
        <StagedPreview
          staged={staged}
          contentSrc={api.apps.stagingContentUrl(staged.staging_id)}
          busy={busyId !== null}
          installing={busyId === 'install'}
          onInstall={() => void handleInstall()}
          onDiscard={() => void handleDiscard()}
        />
      )}

      <PendingStaging
        items={pending}
        activeId={staged?.staging_id ?? null}
        busyId={busyId}
        onOpen={(stagingId) => {
          setGenError(null)
          setNotice(null)
          void showStaged(stagingId)
        }}
        onDiscard={(stagingId) => void handleDiscardStaging(stagingId)}
      />

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
 * 当前阶段的中文说明（迭代 3）。
 *
 * 阶段来自服务端的作业事件（`progress` 事件带固定文案：正在生成页面 /
 * 已通过安全检查）。此前只有一句笼统的"正在解析需求并生成页面"，
 * 用户看不出进行到哪一步。
 *
 * 仍然**不显示**模型输出：那是不可信内容，也不该干扰用户。
 */
function stageLabel(job: JobDetail | null): string {
  if (job === null) return '正在提交…'
  if (job.status === 'PENDING') return '排队中，马上开始…'

  // 取最近一条带文案的事件：服务端的阶段说明比前端猜的准。
  const latest = [...job.events].reverse().find((event) => event.message)
  if (latest) return latest.message

  if (job.status === 'RUNNING') return '正在生成页面…'
  return ''
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
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
