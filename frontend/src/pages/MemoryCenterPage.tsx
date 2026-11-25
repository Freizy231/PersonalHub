import { useState, type FormEvent } from 'react'
import { Link, useSearchParams } from 'react-router-dom'

import { MemoryCard } from '../components/MemoryCard'
import { useMemories, type MemoryFilter } from '../hooks/useMemories'

const FILTERS: { key: MemoryFilter; label: string }[] = [
  { key: 'PENDING', label: '待审核' },
  { key: 'APPROVED', label: '已采用' },
  { key: 'REJECTED', label: '已拒绝' },
  { key: 'DELETED', label: '已删除' },
]

const FILTER_PATH: Record<MemoryFilter, string> = {
  PENDING: '/memory',
  APPROVED: '/memory/approved',
  REJECTED: '/memory/rejected',
  DELETED: '/memory/deleted',
}

const EMPTY_TEXT: Record<MemoryFilter, string> = {
  PENDING: '没有待审核的记忆。与 AI 多聊几次，它会提出候选记忆供你确认。',
  APPROVED: '还没有已采用的记忆。',
  REJECTED: '还没有拒绝过的记忆。',
  DELETED: '还没有删除过的记忆。',
}

interface MemoryCenterPageProps {
  filter: MemoryFilter
}

/**
 * 记忆中心（AGENT.md §2.5 Memory Center，迭代 2 第 3、4 项）。
 *
 * 四条硬性提示写在界面上，避免用户误判系统行为：
 * - 未批准的候选不会进入对话上下文；
 * - 敏感标记不阻止外发（如实呈现，不暗示"本地=没传出过本机"）；
 * - 拒绝或删除过的内容不会被再次提案；
 * - 近似候选只是提示，系统不自动合并。
 *
 * 搜索词放在 URL 上（`?q=`）：刷新、后退、分享链接都能保持同一个视图。
 */
export function MemoryCenterPage({ filter }: MemoryCenterPageProps) {
  // 搜索词存在 URL 里，刷新与浏览器后退都能复原。
  const [params, setParams] = useSearchParams()
  const query = params.get('q') ?? ''
  const [draft, setDraft] = useState(query)

  const { state, actionError, busyId, reload, approve, reject, update, remove, clearActionError } =
    useMemories(filter, query)

  function submitSearch(event: FormEvent) {
    event.preventDefault()
    const next = new URLSearchParams(params)
    const trimmed = draft.trim()
    if (trimmed) next.set('q', trimmed)
    else next.delete('q')
    setParams(next, { replace: true })
  }

  function clearSearch() {
    setDraft('')
    const next = new URLSearchParams(params)
    next.delete('q')
    setParams(next, { replace: true })
  }

  return (
    <section className="memory-center">
      <header className="memory-center__header">
        <h2>记忆中心</h2>
        {state.pendingCount > 0 && (
          <span className="memory-center__pending">{state.pendingCount} 条待审核</span>
        )}
      </header>

      <p className="memory-center__notice">
        只有<strong>已采用</strong>的记忆会作为背景信息进入对话。
        敏感标记仅作提示，<strong>不会阻止</strong>该内容随对话发送给模型。
        拒绝或删除过的内容不会被再次提案。
      </p>

      <nav className="memory-center__filters" aria-label="记忆状态">
        {FILTERS.map((item) => (
          <Link
            key={item.key}
            to={FILTER_PATH[item.key]}
            className={
              item.key === filter ? 'memory-center__filter is-active' : 'memory-center__filter'
            }
            aria-current={item.key === filter ? 'page' : undefined}
          >
            {item.label}
            {item.key === 'PENDING' && state.pendingCount > 0 && (
              <span className="memory-center__count">{state.pendingCount}</span>
            )}
          </Link>
        ))}
      </nav>

      <form className="memory-center__search" role="search" onSubmit={submitSearch}>
        <label className="visually-hidden" htmlFor="memory-search">
          搜索记忆内容
        </label>
        <input
          id="memory-search"
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="搜索记忆内容，多个词用空格分隔"
          maxLength={200}
        />
        <button type="submit">搜索</button>
        {query && (
          <button type="button" onClick={clearSearch}>
            清除
          </button>
        )}
      </form>

      {query && (
        <p className="memory-center__searching" role="status">
          正在搜索「{query}」，命中 {state.loading ? '…' : state.total} 条。
        </p>
      )}

      {actionError && (
        <div className="alert alert--error" role="alert">
          <span>{actionError}</span>
          <button type="button" onClick={clearActionError} aria-label="关闭提示">
            ✕
          </button>
        </div>
      )}

      {state.loadError ? (
        <div className="alert alert--error" role="alert">
          <p>{state.loadError}</p>
          <button type="button" onClick={() => void reload()} disabled={state.loading}>
            重试
          </button>
        </div>
      ) : state.loading && state.items.length === 0 ? (
        <p className="memory-center__empty">正在加载记忆…</p>
      ) : state.items.length === 0 ? (
        <div className="memory-center__empty">
          <p>{query ? `没有包含「${query}」的${FILTERS.find((f) => f.key === filter)?.label ?? ''}记忆。` : EMPTY_TEXT[filter]}</p>
          {query ? (
            <button type="button" onClick={clearSearch}>
              清除搜索
            </button>
          ) : (
            filter === 'PENDING' && <Link to="/conversations">去会话聊聊</Link>
          )}
        </div>
      ) : (
        <ul className="memory-list">
          {state.items.map((memory) => (
            <MemoryCard
              key={memory.id}
              memory={memory}
              busy={busyId === memory.id}
              onApprove={approve}
              onReject={reject}
              onUpdate={update}
              onDelete={remove}
            />
          ))}
        </ul>
      )}
    </section>
  )
}
