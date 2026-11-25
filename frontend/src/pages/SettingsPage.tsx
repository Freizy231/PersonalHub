import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'

import { formatBytes } from '../components/formatBytes'
import { api } from '../services/api'
import type { SettingsView } from '../types/api'

/**
 * 设置（AGENT.md §2.5 第 6 项）：模型配置、数据目录、记忆策略与系统信息。
 *
 * **只读**。页面展示的是这份后端进程**真正在用**的配置；
 * 修改方式写在各区块的说明里（改环境变量后重启），而不是做成输入框。
 *
 * 为什么不做成可编辑：一个本机页面若能改模型地址或数据目录，
 * 就等于绕过了"配置修改需重启"这条约束，而且页面上看到的配置
 * 会与进程实际状态不一致——那比不让改更危险。
 *
 * 密钥永远不会出现在这个页面上：接口只回传"是否已配置"与变量名。
 */
export function SettingsPage() {
  const [state, setState] = useState<SettingsView | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    const result = await api.settings.get()
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
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    return () => {
      mounted.current = false
    }
  }, [load])

  if (loading && state === null) {
    return (
      <section className="settings">
        <p className="settings__loading">正在加载…</p>
      </section>
    )
  }

  if (error) {
    return (
      <section className="settings">
        <div className="alert alert--error" role="alert">
          <p>{error}</p>
        </div>
        <button type="button" className="button" onClick={() => void load()}>
          重试
        </button>
      </section>
    )
  }

  if (state === null) return null

  const { provider, data, memory_policy: policy, counts, runtime } = state

  return (
    <section className="settings">
      <header className="settings__header">
        <h2>设置</h2>
        <button type="button" className="button" onClick={() => void load()}>
          刷新
        </button>
      </header>

      <p className="settings__notice">
        这里的值来自后端启动时的环境变量。要修改请改环境变量后<strong>重启后端</strong>——
        运行期改动会让页面显示与实际行为不一致。
      </p>

      <SettingsSection title="模型">
        <Row label="供应商" value={provider.name} />
        <Row label="模型" value={provider.model} />
        <Row label="接口地址" value={provider.base_url || '未设置'} mono />
        <Row
          label="密钥"
          value={
            provider.api_key_configured
              ? `已配置（来自环境变量 ${provider.api_key_env}）`
              : `未配置（需要设置 ${provider.api_key_env}）`
          }
          tone={provider.api_key_configured ? 'ok' : 'warn'}
        />
        {!provider.ready && (
          <p className="settings__hint settings__hint--warn">{provider.hint}</p>
        )}
        <p className="settings__hint">
          密钥只由后端读取，不会出现在这个页面、浏览器存储或日志中。
          加密方式由系统决定；设置环境变量后重启即可。
        </p>
      </SettingsSection>

      <SettingsSection title="数据">
        <Row label="数据目录" value={data.data_dir} mono />
        <Row label="数据库" value={data.db_path} mono />
        <Row label="数据库大小" value={formatBytes(data.db_size_bytes)} />
        <Row label="可用空间" value={formatBytes(data.free_disk_bytes)} />
        <p className="settings__hint">
          所有数据默认只存在这台机器上。备份与导出功能尚未实现（见路线图 M10）。
        </p>
      </SettingsSection>

      <SettingsSection title="记忆策略">
        <ul className="settings__policy">
          <li>
            {policy.requires_approval ? '✅' : '❌'} AI 提取的只是候选，
            <strong>必须经你批准</strong>才会被使用
          </li>
          <li>
            {policy.only_approved_injected ? '✅' : '❌'} 只有已批准的记忆会进入对话上下文
          </li>
          <li>
            {policy.secret_never_injected ? '✅' : '❌'} 标记为「秘密」的内容
            <strong>永不</strong>进入模型上下文
          </li>
          <li>上下文预算：约 {policy.context_budget_chars.toLocaleString('zh-CN')} 字符</li>
        </ul>
        <p className="settings__hint">{policy.note}</p>
      </SettingsSection>

      <SettingsSection title="数据概览">
        <div className="settings__counts">
          <Count label="会话" value={counts.conversations} />
          <Count label="消息" value={counts.messages} />
          <Count label="已批准记忆" value={counts.memories_approved} />
          <Count label="待审核" value={counts.memories_proposed} />
          <Count label="任务" value={counts.tasks} />
          <Count label="应用" value={counts.apps} />
        </div>
      </SettingsSection>

      <SettingsSection title="系统">
        <Row label="版本" value={runtime.version} />
        <Row label="监听地址" value={`${runtime.host}:${runtime.port}`} mono />
        <Row label="应用上限" value={`${runtime.max_apps} 个`} />
        <Row label="单个应用版本上限" value={`${runtime.max_versions_per_app} 个`} />
      </SettingsSection>
    </section>
  )
}

function SettingsSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="settings-section">
      <h3 className="settings-section__title">{title}</h3>
      <div className="settings-section__body">{children}</div>
    </section>
  )
}

function Row({
  label,
  value,
  mono = false,
  tone,
}: {
  label: string
  value: string
  mono?: boolean
  tone?: 'ok' | 'warn'
}) {
  const className = [
    'settings-row',
    tone ? `settings-row--${tone}` : '',
    mono ? 'settings-row--mono' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <div className={className}>
      <dt className="settings-row__label">{label}</dt>
      <dd className="settings-row__value">{value}</dd>
    </div>
  )
}

function Count({ label, value }: { label: string; value: number }) {
  return (
    <div className="settings-count">
      <span className="settings-count__value">{value}</span>
      <span className="settings-count__label">{label}</span>
    </div>
  )
}
