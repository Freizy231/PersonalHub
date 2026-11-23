import { Link } from 'react-router-dom'

import { MemoryCard } from '../components/MemoryCard'
import { useMemories, type MemoryFilter } from '../hooks/useMemories'

const FILTERS: { key: MemoryFilter; label: string }[] = [
  { key: 'PENDING', label: '待审核' },
  { key: 'APPROVED', label: '已批准' },
  { key: 'REJECTED', label: '已拒绝' },
]

interface MemoryCenterPageProps {
  filter: MemoryFilter
}

/**
 * 记忆中心（AGENT.md §2.5 Memory Center）。
 *
 * 三条硬性提示写在界面上，避免用户误判系统行为：
 * - 未批准的候选不会进入对话上下文
 * - 敏感标记不阻止外发
 * - 拒绝/删除后不会被重复提案
 */
export function MemoryCenterPage({ filter }: MemoryCenterPageProps) {
  const { state, actionError, busyId, approve, reject, update, remove, clearActionError } =
    useMemories(filter)

  return (
    <section className="memory-center">
      <header className="memory-center__header">
        <h2>记忆中心</h2>
        {state.pendingCount > 0 && (
          <span className="memory-center__pending">{state.pendingCount} 条待审核</span>
        )}
      </header>

      <p className="memory-center__notice">
        只有<strong>已批准</strong>的记忆会作为背景信息进入对话。
        敏感标记仅作提示，<strong>不会阻止</strong>该内容随对话发送给模型。
        拒绝或删除过的内容不会被再次提案。
      </p>

      <nav className="memory-center__filters" aria-label="记忆筛选">
        {FILTERS.map((item) => (
          <Link
            key={item.key}
            to={`/memory/${item.key.toLowerCase()}`}
            className={
              item.key === filter ? 'memory-center__filter is-active' : 'memory-center__filter'
            }
          >
            {item.label}
            {item.key === 'PENDING' && state.pendingCount > 0 && (
              <span className="memory-center__count">{state.pendingCount}</span>
            )}
          </Link>
        ))}
      </nav>

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
        </div>
      ) : state.loading && state.items.length === 0 ? (
        <p className="memory-center__empty">正在加载记忆…</p>
      ) : state.items.length === 0 ? (
        <p className="memory-center__empty">
          {filter === 'PENDING'
            ? '没有待审核的记忆。与 AI 多聊几次，它会提出候选记忆供你确认。'
            : '这个分类下还没有记忆。'}
        </p>
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
