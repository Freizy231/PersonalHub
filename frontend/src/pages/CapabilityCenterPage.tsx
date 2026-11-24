import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { Capability, DependencyHealth, McpServerList } from '../types/api'

const TYPE_LABEL: Record<string, string> = {
  tool: '工具',
  mcp: 'MCP',
  skill: '技能',
  workflow: '工作流',
  plugin: '插件',
  app: '应用',
}

const SOURCE_LABEL: Record<string, string> = {
  builtin: '内建',
  installed: '已安装',
  community: '社区',
}

const RUNTIME_LABEL: Record<string, string> = {
  builtin: '随服务运行',
  mcp: 'MCP 服务',
  process: '独立进程',
}

/**
 * 能力中心（AGENT.md §4.9、§7.2、M8 第 1–2 项）。
 *
 * 只读：展示"系统里有哪些能力"及其声明。
 * 刻意不在这一页放安装/调用按钮 —— 那需要单独的授权流程（§8.5），
 * 不能和"查看"混在一起先做出来。
 *
 * 界面必须如实表达：**声明权限不等于已获授权**，否则用户会以为
 * 列在这里的写操作能力已经可以在后台静默执行。
 */
export function CapabilityCenterPage() {
  const [items, setItems] = useState<Capability[]>([])
  const [notice, setNotice] = useState('')
  const [health, setHealth] = useState<DependencyHealth | null>(null)
  const [mcp, setMcp] = useState<McpServerList | null>(null)
  const [query, setQuery] = useState('')
  const [typeFilter, setTypeFilter] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const mounted = useRef(true)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    const [list, deps, mcpList] = await Promise.all([
      api.capabilities.list({
        ...(query.trim() ? { q: query.trim() } : {}),
        ...(typeFilter ? { type: typeFilter } : {}),
      }),
      api.capabilities.dependencyHealth(),
      api.mcp.servers(),
    ])
    if (!mounted.current) return

    if (list.ok) {
      setItems(list.data.items)
      setNotice(list.data.notice)
    } else {
      setError(list.message)
    }
    setHealth(deps.ok ? deps.data : null)
    setMcp(mcpList.ok ? mcpList.data : null)
    setLoading(false)
  }, [query, typeFilter])

  useEffect(() => {
    mounted.current = true
    // 与外部系统同步，结果异步返回。
    // oxlint-disable-next-line react/set-state-in-effect
    void load()
    return () => {
      mounted.current = false
    }
  }, [load])

  return (
    <section className="capability-center">
      <header className="capability-center__header">
        <h2>能力</h2>
        <span className="capability-center__count">
          {loading ? '加载中…' : `共 ${items.length} 个`}
        </span>
      </header>

      <p className="capability-center__notice">
        {notice || '这里展示的是能力声明，声明权限不代表已获授权。'}
      </p>

      {health && !health.healthy && (
        <div className="alert alert--error" role="alert">
          <p>
            依赖不完整：{Object.keys(health.missing).length} 个能力缺少依赖
            {health.cycle.length > 0 && `；检测到依赖环：${health.cycle.join(' → ')}`}
          </p>
        </div>
      )}

      {mcp && mcp.items.length > 0 && <McpServerPanel mcp={mcp} />}

      <div className="capability-center__filters">
        <label className="visually-hidden" htmlFor="capability-search">
          搜索能力
        </label>
        <input
          id="capability-search"
          type="text"
          value={query}
          placeholder="按名称或标识搜索"
          onChange={(event) => setQuery(event.target.value)}
        />
        <label className="visually-hidden" htmlFor="capability-type">
          按类型筛选
        </label>
        <select
          id="capability-type"
          value={typeFilter}
          onChange={(event) => setTypeFilter(event.target.value)}
        >
          <option value="">全部类型</option>
          {Object.entries(TYPE_LABEL).map(([value, label]) => (
            <option key={value} value={value}>
              {label}
            </option>
          ))}
        </select>
      </div>

      {error ? (
        <div className="alert alert--error" role="alert">
          <p>{error}</p>
          <button type="button" onClick={() => void load()} disabled={loading}>
            重试
          </button>
        </div>
      ) : items.length === 0 ? (
        <p className="capability-center__empty">
          {loading ? '正在加载能力…' : '没有匹配的能力。'}
        </p>
      ) : (
        <ul className="capability-list">
          {items.map((item) => (
            <CapabilityCard key={`${item.id}@${item.version}`} capability={item} />
          ))}
        </ul>
      )}
    </section>
  )
}

function CapabilityCard({ capability }: { capability: Capability }) {
  const [showSchema, setShowSchema] = useState(false)

  return (
    <li className="capability">
      <div className="capability__meta">
        <span className="capability__name">{capability.name}</span>
        <code className="capability__id">{capability.id}</code>
        <span className="capability__badge">{TYPE_LABEL[capability.type] ?? capability.type}</span>
        <span className="capability__badge">{SOURCE_LABEL[capability.source] ?? capability.source}</span>
        <span className="capability__version">v{capability.version}</span>
      </div>

      {capability.description && <p className="capability__desc">{capability.description}</p>}

      <div className="capability__row">
        <span className="capability__label">运行时</span>
        <span>{RUNTIME_LABEL[capability.runtime] ?? capability.runtime}</span>
      </div>

      {capability.permissions.length > 0 && (
        <div className="capability__row">
          <span className="capability__label">声明权限</span>
          <span className="capability__permissions">
            {capability.permissions.map((permission) => (
              <code key={permission}>{permission}</code>
            ))}
            <em className="capability__hint">（声明，非授权）</em>
          </span>
        </div>
      )}

      {capability.dependencies.length > 0 && (
        <div className="capability__row">
          <span className="capability__label">依赖</span>
          <span className="capability__permissions">
            {capability.dependencies.map((dependency) => (
              <code key={dependency}>{dependency}</code>
            ))}
          </span>
        </div>
      )}

      <details
        className="capability__schema"
        open={showSchema}
        onToggle={(event) => setShowSchema(event.currentTarget.open)}
      >
        <summary>查看参数结构</summary>
        <pre>{JSON.stringify(capability.input_schema, null, 2)}</pre>
      </details>
    </li>
  )
}

/**
 * MCP Server 状态面板（M8 第 4–5 项）。
 *
 * 只展示状态，不提供启停按钮：连接外部服务与启动子进程属于高影响操作，
 * 必须由用户直接编辑配置文件并重启服务，而不是点一个网页按钮。
 */
function McpServerPanel({ mcp }: { mcp: McpServerList }) {
  return (
    <section className="mcp-panel" aria-label="外部 MCP 服务">
      <header className="mcp-panel__header">
        <h3>外部 MCP 服务</h3>
        <span className="mcp-panel__count">{mcp.items.length} 个</span>
      </header>

      <p className="mcp-panel__notice">{mcp.notice}</p>

      {mcp.errors.length > 0 && (
        <ul className="mcp-panel__errors">
          {mcp.errors.map((error) => (
            <li key={error}>{error}</li>
          ))}
        </ul>
      )}

      <ul className="mcp-list">
        {mcp.items.map((server) => (
          <li key={server.name} className={`mcp mcp--${server.status}`}>
            <div className="mcp__main">
              <span className="mcp__name">{server.name}</span>
              <span className="mcp__transport">{server.transport}</span>
              <span className={`mcp__status mcp__status--${server.status}`}>
                {MCP_STATUS_LABEL[server.status] ?? server.status}
              </span>
              {server.tool_count > 0 && (
                <span className="mcp__tools">{server.tool_count} 个工具</span>
              )}
            </div>
            {server.reason && <p className="mcp__reason">{server.reason}</p>}
            {server.tools.length > 0 && (
              <p className="mcp__toolnames">
                {server.tools.map((tool) => (
                  <code key={tool}>{tool}</code>
                ))}
              </p>
            )}
          </li>
        ))}
      </ul>

      <p className="mcp-panel__hint">
        启用或修改需要在数据目录的 <code>mcp/servers.json</code> 中编辑，然后重启服务。
      </p>
    </section>
  )
}

const MCP_STATUS_LABEL: Record<string, string> = {
  ready: '可用',
  unavailable: '不可用',
  disabled: '未启用',
}
