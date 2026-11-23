import { useState, type FormEvent } from 'react'

import type { MemoryItem } from '../types/api'

interface MemoryCardProps {
  memory: MemoryItem
  busy: boolean
  onApprove: (id: string) => Promise<boolean>
  onReject: (id: string) => Promise<boolean>
  onUpdate: (id: string, content: string) => Promise<boolean>
  onDelete: (id: string) => Promise<boolean>
}

const KIND_LABEL: Record<string, string> = {
  SEMANTIC: '事实/偏好',
  EPISODIC: '经历',
  PROCEDURAL: '方法',
}

const SENSITIVITY_LABEL: Record<string, string> = {
  PUBLIC: '公开',
  PERSONAL: '个人',
  SENSITIVE: '敏感',
  SECRET: '机密',
}

const SOURCE_LABEL: Record<string, string> = {
  conversation: '对话',
  message: '消息',
  user_edit: '你的修改',
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return iso
  return date.toLocaleString('zh-CN', { dateStyle: 'short', timeStyle: 'short' })
}

/**
 * 单条记忆卡片。
 *
 * 展示来源与敏感级别：用户批准前必须能看清"这条是怎么来的"（§4.4）。
 * 敏感级别目前只是提示，不阻断任何行为——界面上要如实表达这一点，
 * 不能让用户误以为标了"敏感"就不会被发送出去。
 */
export function MemoryCard({
  memory,
  busy,
  onApprove,
  onReject,
  onUpdate,
  onDelete,
}: MemoryCardProps) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(memory.content)
  const [confirmingDelete, setConfirmingDelete] = useState(false)

  const trimmed = draft.trim()
  const canSave = trimmed.length > 0 && trimmed !== memory.content && !busy

  async function handleSave(event: FormEvent) {
    event.preventDefault()
    if (!canSave) return
    const ok = await onUpdate(memory.id, trimmed)
    if (ok) setEditing(false)
  }

  const source = memory.sources[0]

  return (
    <li className={`memory memory--${memory.status.toLowerCase()}`}>
      <div className="memory__meta">
        <span className="memory__badge">{KIND_LABEL[memory.kind] ?? memory.kind}</span>
        <span
          className={`memory__badge memory__badge--${memory.sensitivity.toLowerCase()}`}
          title="仅用于标记，不会阻止该内容随对话发送给模型"
        >
          {SENSITIVITY_LABEL[memory.sensitivity] ?? memory.sensitivity}
        </span>
        <span className="memory__confidence">置信度 {memory.confidence}</span>
        <time className="memory__time" dateTime={memory.created_at}>
          {formatTime(memory.created_at)}
        </time>
      </div>

      {editing ? (
        <form className="memory__edit" onSubmit={handleSave}>
          <label className="visually-hidden" htmlFor={`memory-edit-${memory.id}`}>
            修改记忆内容
          </label>
          <textarea
            id={`memory-edit-${memory.id}`}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            rows={3}
            disabled={busy}
            autoFocus
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                setDraft(memory.content)
                setEditing(false)
              }
            }}
          />
          <div className="memory__actions">
            <button type="submit" disabled={!canSave}>
              保存
            </button>
            <button
              type="button"
              onClick={() => {
                setDraft(memory.content)
                setEditing(false)
              }}
              disabled={busy}
            >
              取消
            </button>
          </div>
        </form>
      ) : (
        <p className="memory__content">{memory.content}</p>
      )}

      {source && (
        <p className="memory__source">
          来源：{SOURCE_LABEL[source.source_type] ?? source.source_type}
          {source.excerpt && source.excerpt !== memory.content && (
            <span className="memory__excerpt"> — 「{source.excerpt}」</span>
          )}
        </p>
      )}

      {!editing && (
        <div className="memory__actions">
          {memory.status === 'PROPOSED' && (
            <>
              <button type="button" onClick={() => void onApprove(memory.id)} disabled={busy}>
                批准
              </button>
              <button
                type="button"
                className="danger"
                onClick={() => void onReject(memory.id)}
                disabled={busy}
              >
                拒绝
              </button>
            </>
          )}
          {memory.status === 'APPROVED' && (
            <button
              type="button"
              className="danger"
              onClick={() => void onReject(memory.id)}
              disabled={busy}
            >
              撤回批准
            </button>
          )}
          {memory.status === 'REJECTED' && (
            <button type="button" onClick={() => void onApprove(memory.id)} disabled={busy}>
              重新批准
            </button>
          )}
          <button type="button" onClick={() => setEditing(true)} disabled={busy}>
            修改
          </button>

          {confirmingDelete ? (
            <>
              <span className="memory__confirm">确定删除？</span>
              <button
                type="button"
                className="danger"
                onClick={() => void onDelete(memory.id)}
                disabled={busy}
              >
                确认删除
              </button>
              <button type="button" onClick={() => setConfirmingDelete(false)} disabled={busy}>
                取消
              </button>
            </>
          ) : (
            <button
              type="button"
              className="danger"
              onClick={() => setConfirmingDelete(true)}
              disabled={busy}
            >
              删除
            </button>
          )}
        </div>
      )}
    </li>
  )
}
