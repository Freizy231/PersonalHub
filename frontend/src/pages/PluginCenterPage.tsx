import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Plugin, PluginList, PluginPreview } from '../types/api'

/**
 * 插件中心（M8 第 6–7 项）。
 *
 * 本期插件是**声明式**的：不含任何可执行代码，只是把已有工具的组合。
 * 界面上要如实说明这一点，否则用户会以为装了一个插件就等于获得了新能力。
 *
 * 安装流程刻意分两步（§8.5「安装前显示来源、权限、依赖和风险」）：
 *   1. 预览：展示清单摘要与风险提示；
 *   2. 确认安装。
 * 不做"点一下就装好"。
 */
export function PluginCenterPage() {
  const [state, setState] = useState<PluginList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)

  const [importName, setImportName] = useState('')
  const [preview, setPreview] = useState<PluginPreview | null>(null)
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [busyId, setBusyId] = useState<string | null>(null)

  const mounted = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    const result = await api.plugins.list()
    if (!mounted.current) return
    if (result.ok) {
      setState(result.data)
      setError(null)
    } else {
      setError(result.message)
    }
    setLoading(false)
  }, [])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，��果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    return () => {
      mounted.current = false
    }
  }, [load])

  async function handlePreview() {
    const name = importName.trim()
    if (!name) return
    setPreviewError(null)
    setPreview(null)
    const result = await api.plugins.preview(name)
    if (!mounted.current) return
    if (result.ok) setPreview(result.data)
    else setPreviewError(result.message)
  }

  async function handleInstall(name: string) {
    setBusyId('install')
    const result = await api.plugins.install(name)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setPreview(null)
      setImportName('')
      setNotice(`已安装 ${result.data.name}。默认未启用，需要手动开启。`)
      await load()
    } else {
      setPreviewError(result.message)
    }
  }

  async function toggle(plugin: Plugin) {
    setBusyId(plugin.id)
    const result = plugin.enabled
      ? await api.plugins.disable(plugin.id)
      : await api.plugins.enable(plugin.id)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setNotice(null)
      await load()
    } else {
      setError(result.message)
    }
  }

  async function uninstall(plugin: Plugin) {
    setBusyId(plugin.id)
    const result = await api.plugins.remove(plugin.id)
    if (!mounted.current) return
    setBusyId(null)
    if (result.ok) {
      setNotice(result.data.message)
      await load()
    } else {
      setError(result.message)
    }
  }

  return (
    <section className="plugin-center">
      <header className="plugin-center__header">
        <h2>插件</h2>
        <span className="plugin-center__count">
          {loading ? '加载中…' : `共 ${state?.total ?? 0} 个`}
        </span>
      </header>

      <p className="plugin-center__notice">
        {state?.notice ??
          '本期插件为声明式：不含任何可执行代码，只是复用已有工具的固定组合。'}
      </p>

      {state && !state.sandbox_available && (
        <div className="alert alert--error" role="alert">
          <p>
            <strong>沙箱不可用</strong>，进程插件将无法执行（声明式插件不受影响）。
          </p>
          <p className="plugin-center__sandbox-reason">{state.sandbox_reason}</p>
        </div>
      )}

      {notice && (
        <div className="alert alert--info" role="status">
          <p>{notice}</p>
        </div>
      )}

      {error && (
        <div className="alert alert--error" role="alert">
          <p>{error}</p>
        </div>
      )}

      <PluginInstaller
        importName={importName}
        onNameChange={setImportName}
        onPreview={handlePreview}
        preview={preview}
        previewError={previewError}
        onInstall={handleInstall}
        onCancel={() => {
          setPreview(null)
          setPreviewError(null)
        }}
        busy={busyId === 'install'}
      />

      {state && state.items.length === 0 ? (
        <p className="plugin-center__empty">
          {loading ? '正在加载插件…' : '还没有安装插件。'}
        </p>
      ) : (
        <ul className="plugin-list">
          {(state?.items ?? []).map((plugin) => (
            <PluginCard
              key={plugin.id}
              plugin={plugin}
              busy={busyId === plugin.id}
              onToggle={() => void toggle(plugin)}
              onUninstall={() => void uninstall(plugin)}
            />
          ))}
        </ul>
      )}
    </section>
  )
}

function PluginInstaller({
  importName,
  onNameChange,
  onPreview,
  preview,
  previewError,
  onInstall,
  onCancel,
  busy,
}: {
  importName: string
  onNameChange: (value: string) => void
  onPreview: () => void
  preview: PluginPreview | null
  previewError: string | null
  onInstall: (name: string) => void
  onCancel: () => void
  busy: boolean
}) {
  return (
    <section className="plugin-installer">
      <h3>安装插件</h3>
      <p className="plugin-installer__hint">
        先把插件目录放进数据目录的 <code>plugin-import/</code>，再在下面填写目录名。
        这里不接受任意路径。
      </p>

      <form
        className="plugin-installer__form"
        onSubmit={(event) => {
          event.preventDefault()
          onPreview()
        }}
      >
        <label className="visually-hidden" htmlFor="plugin-import-name">
          插件目录名
        </label>
        <input
          id="plugin-import-name"
          type="text"
          value={importName}
          placeholder="目录名，例如 notes-plugin"
          onChange={(event) => onNameChange(event.target.value)}
        />
        <button type="submit" disabled={!importName.trim() || busy}>
          预览
        </button>
      </form>

      {previewError && (
        <div className="alert alert--error" role="alert">
          <p>{previewError}</p>
        </div>
      )}

      {preview && (
        <div className="plugin-preview" role="region" aria-label="安装预览">
          <p className="plugin-preview__title">
            <strong>{preview.name}</strong> <code>{preview.id}</code> v{preview.version}
          </p>
          {preview.description && <p className="plugin-preview__desc">{preview.description}</p>}

          <div className="plugin-preview__row">
            <span className="plugin-preview__label">工具</span>
            <ul className="plugin-preview__tools">
              {preview.tools.map((tool) => (
                <li key={tool.name}>
                  <strong>{tool.display_name}</strong>
                  <span className={`plugin-tool__risk plugin-tool__risk--${tool.risk_hint === '只读' ? 'read' : 'write'}`}>
                    {tool.risk_hint}
                  </span>
                  <span className="plugin-tool__composes">
                    组合：{tool.composes.join(' → ')}
                  </span>
                </li>
              ))}
            </ul>
          </div>

          {preview.warnings.length > 0 && (
            <ul className="plugin-preview__warnings">
              {preview.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          )}

          <div className="plugin-preview__actions">
            <button type="button" onClick={() => onInstall(preview.id ? importName : importName)} disabled={busy}>
              {busy ? '安装中…' : '确认安装'}
            </button>
            <button type="button" onClick={onCancel} disabled={busy}>
              取消
            </button>
          </div>
        </div>
      )}
    </section>
  )
}

function PluginCard({
  plugin,
  busy,
  onToggle,
  onUninstall,
}: {
  plugin: Plugin
  busy: boolean
  onToggle: () => void
  onUninstall: () => void
}) {
  const [confirming, setConfirming] = useState(false)

  if (plugin.error) {
    return (
      <li className="plugin plugin--broken">
        <div className="plugin__main">
          <span className="plugin__name">{plugin.id}</span>
          <span className="plugin__status plugin__status--broken">清单有问题</span>
        </div>
        <p className="plugin__error">{plugin.error}</p>
        <button type="button" className="danger" onClick={onUninstall} disabled={busy}>
          卸载
        </button>
      </li>
    )
  }

  return (
    <li className={`plugin ${plugin.enabled ? 'plugin--enabled' : ''}`}>
      <div className="plugin__main">
        <span className="plugin__name">{plugin.name}</span>
        <code className="plugin__id">{plugin.id}</code>
        <span className="plugin__version">v{plugin.version}</span>
        <span className={`plugin__status ${plugin.enabled ? 'plugin__status--on' : ''}`}>
          {plugin.enabled ? '已启用' : '未启用'}
        </span>
      </div>

      {plugin.description && <p className="plugin__desc">{plugin.description}</p>}

      <ul className="plugin__tools">
        {plugin.tools.map((tool) => (
          <li key={tool.name}>
            {tool.display_name}
            <span className="plugin-tool__composes">组合：{tool.composes.join(' → ')}</span>
          </li>
        ))}
      </ul>

      {plugin.permissions.length > 0 && (
        <p className="plugin__permissions">
          声明权限：{plugin.permissions.join('、')}
          <em>（声明，非授权）</em>
        </p>
      )}

      <div className="plugin__actions">
        {confirming ? (
          <>
            {/* 两段确认：卸载会取消注册，目录保留但不再可用。 */}
            <span className="plugin__confirm-text">卸载后目录会保留，可重新安装。</span>
            <button type="button" className="danger" onClick={onUninstall} disabled={busy}>
              确认卸载
            </button>
            <button type="button" onClick={() => setConfirming(false)} disabled={busy}>
              取消
            </button>
          </>
        ) : (
          <>
            <button type="button" onClick={onToggle} disabled={busy}>
              {busy ? '处理中…' : plugin.enabled ? '禁用' : '启用'}
            </button>
            <button type="button" className="danger" onClick={() => setConfirming(true)} disabled={busy}>
              卸载
            </button>
          </>
        )}
      </div>
    </li>
  )
}
