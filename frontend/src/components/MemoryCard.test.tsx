import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { MemoryItem } from '../types/api'
import { MemoryCard } from './MemoryCard'

function memory(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: 'm1',
    content: '我习惯项目驱动学习',
    status: 'PROPOSED',
    kind: 'SEMANTIC',
    sensitivity: 'PERSONAL',
    confidence: 80,
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    sources: [{ source_type: 'conversation', source_id: 'c1', excerpt: '我习惯项目驱动学习' }],
    similar: [],
    ...overrides,
  }
}

function renderCard(item: MemoryItem, handlers: Partial<Parameters<typeof MemoryCard>[0]> = {}) {
  return render(
    <MemoryCard
      memory={item}
      busy={false}
      onApprove={vi.fn().mockResolvedValue(true)}
      onReject={vi.fn().mockResolvedValue(true)}
      onUpdate={vi.fn().mockResolvedValue(true)}
      onDelete={vi.fn().mockResolvedValue(true)}
      {...handlers}
    />,
  )
}

describe('MemoryCard：近似候选提示', () => {
  it('待审核记忆展示近似候选，并说明不会自动合并', () => {
    renderCard(
      memory({
        similar: [{ id: 'm2', content: '我用项目驱动的方式学习', score: 0.8 }],
      }),
    )

    const note = screen.getByRole('note')
    expect(within(note).getByText(/有 1 条待审核记忆与这条很像/)).toBeTruthy()
    expect(within(note).getByText(/「我用项目驱动的方式学习」/)).toBeTruthy()
    expect(within(note).getByText(/不会自动合并/)).toBeTruthy()
    // 提示不改变操作：批准与拒绝仍然可用。
    expect(screen.getByRole('button', { name: '批准' })).toBeTruthy()
    expect(screen.getByRole('button', { name: '拒绝' })).toBeTruthy()
  })

  it('已采用的记忆不显示近似提示（没有可执行动作）', () => {
    renderCard(
      memory({
        status: 'APPROVED',
        similar: [{ id: 'm2', content: '我习惯项目驱动学习', score: 1 }],
      }),
    )

    expect(screen.queryByRole('note')).toBeNull()
  })

  it('没有近似项时不渲染提示区块', () => {
    renderCard(memory())

    expect(screen.queryByRole('note')).toBeNull()
  })
})

describe('MemoryCard：状态与操作', () => {
  it('已删除是终态：没有批准、修改或删除按钮，并说明不会再使用', () => {
    renderCard(memory({ status: 'DELETED' }))

    expect(screen.getByText(/已删除，不会再出现在任何视图或对话中/)).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准' })).toBeNull()
    expect(screen.queryByRole('button', { name: '修改' })).toBeNull()
    expect(screen.queryByRole('button', { name: '删除' })).toBeNull()
  })

  it('删除需要两段确认，并引用被删除的内容', async () => {
    const user = userEvent.setup()
    const onDelete = vi.fn().mockResolvedValue(true)
    renderCard(memory(), { onDelete })

    await user.click(screen.getByRole('button', { name: '删除' }))

    expect(onDelete).not.toHaveBeenCalled()
    expect(screen.getByText(/确定删除「我习惯项目驱动学习」/).textContent).toContain(
      'AI 不会再使用它',
    )

    await user.click(screen.getByRole('button', { name: '确认删除' }))
    expect(onDelete).toHaveBeenCalledWith('m1')
  })

  it('长内容在删除确认里被截断，不挤掉按钮', async () => {
    const user = userEvent.setup()
    renderCard(memory({ content: '很长的记忆内容'.repeat(20) }))

    await user.click(screen.getByRole('button', { name: '删除' }))

    const text = screen.getByText(/确定删除/).textContent ?? ''
    expect(text.length).toBeLessThan(80)
    expect(text).toContain('…')
  })

  it('已采用的记忆可以撤回批准', () => {
    renderCard(memory({ status: 'APPROVED' }))

    expect(screen.getByRole('button', { name: '撤回批准' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '批准' })).toBeNull()
  })

  it('已拒绝的记忆可以重新批准', () => {
    renderCard(memory({ status: 'REJECTED' }))

    expect(screen.getByRole('button', { name: '重新批准' })).toBeTruthy()
  })

  it('显示来源，敏感级别说明它不阻断外发', () => {
    renderCard(memory({ sensitivity: 'SENSITIVE' }))

    expect(screen.getByText(/来源：对话/)).toBeTruthy()
    const badge = screen.getByText('敏感')
    expect(badge.getAttribute('title')).toContain('不会阻止')
  })

  it('修改后的记忆显示最近修改时间', () => {
    renderCard(
      memory({
        status: 'APPROVED',
        created_at: '2026-11-20T00:00:00Z',
        updated_at: '2026-11-25T00:00:00Z',
      }),
    )

    expect(screen.getByText(/最近修改/)).toBeTruthy()
  })
})
