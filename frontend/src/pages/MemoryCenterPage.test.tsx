import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { MemoryItem, MemoryList } from '../types/api'
import { MemoryCenterPage } from './MemoryCenterPage'

vi.mock('../services/api', () => ({
  api: {
    memory: {
      list: vi.fn(),
      approve: vi.fn(),
      reject: vi.fn(),
      update: vi.fn(),
      remove: vi.fn(),
    },
  },
}))

const { api } = await import('../services/api')

function memory(overrides: Partial<MemoryItem> = {}): MemoryItem {
  return {
    id: 'm1',
    content: '用户偏好简洁的回复。',
    status: 'PROPOSED',
    kind: 'SEMANTIC',
    sensitivity: 'PERSONAL',
    confidence: 0.8,
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    sources: [],
    ...overrides,
  }
}

function listResult(
  items: MemoryItem[],
  pendingCount = items.filter((m) => m.status === 'PROPOSED').length,
): { ok: true; data: MemoryList } {
  return {
    ok: true,
    data: { items, total: items.length, limit: 100, offset: 0, pending_count: pendingCount },
  }
}

function renderPage(initial = '/memory') {
  return render(
    <MemoryRouter initialEntries={[initial]}>
      <MemoryCenterPage filter="PENDING" />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.memory.list).mockReset()
  vi.mocked(api.memory.approve).mockReset()
  vi.mocked(api.memory.reject).mockReset()
  vi.mocked(api.memory.update).mockReset()
  vi.mocked(api.memory.remove).mockReset()
})

describe('MemoryCenterPage：加载、失败与搜索状态', () => {
  it('加载中显示占位，不先喊"没有待审核的记忆"', async () => {
    let resolve: (value: { ok: true; data: MemoryList }) => void = () => {}
    vi.mocked(api.memory.list).mockReturnValue(
      new Promise((r) => {
        resolve = r
      }),
    )

    renderPage()

    expect(screen.getByText('正在加载记忆…')).toBeTruthy()
    expect(screen.queryByText(/没有待审核的记忆/)).toBeNull()

    resolve(listResult([memory()]))
    expect(await screen.findByText('用户偏好简洁的回复。')).toBeTruthy()
  })

  it('加载失败展示原因并可重试，重试后恢复', async () => {
    const user = userEvent.setup()
    vi.mocked(api.memory.list)
      .mockResolvedValueOnce({ ok: false, code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' })
      .mockResolvedValueOnce(listResult([memory()]))

    renderPage()

    expect(await screen.findByText('无法连接到本地后端服务。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText('用户偏好简洁的回复。')).toBeTruthy()
  })

  it('搜索词走服务端查询，并回报命中数量', async () => {
    const user = userEvent.setup()
    vi.mocked(api.memory.list).mockResolvedValue(listResult([memory()]))

    renderPage()

    await screen.findByText('用户偏好简洁的回复。')
    await user.type(screen.getByLabelText('搜索记忆内容'), '简洁')
    await user.click(screen.getByRole('button', { name: '搜索' }))

    await waitFor(() => {
      expect(api.memory.list).toHaveBeenLastCalledWith({ status: 'PROPOSED', q: '简洁' })
    })
    expect(screen.getByRole('status').textContent).toContain('简洁')
  })

  it('搜索无结果时说明是"没有包含该词"，而不是"还没有记忆"', async () => {
    vi.mocked(api.memory.list).mockResolvedValue(listResult([]))

    renderPage('/memory?q=不存在的词')

    expect(await screen.findByText(/没有包含「不存在的词」/)).toBeTruthy()
    // 与"这个状态下一共没有记忆"是两回事，不能混用同一句文案。
    expect(screen.queryByText(/没有待审核的记忆。/)).toBeNull()
    expect(screen.getByRole('button', { name: '清除搜索' })).toBeTruthy()
  })

  it('写操作成功后以服务端为准重新拉取', async () => {
    const user = userEvent.setup()
    vi.mocked(api.memory.list)
      .mockResolvedValueOnce(listResult([memory()]))
      .mockResolvedValueOnce(listResult([]))
    vi.mocked(api.memory.approve).mockResolvedValue({ ok: true, data: memory({ status: 'APPROVED' }) })

    renderPage()

    await user.click(await screen.findByRole('button', { name: '批准' }))

    expect(api.memory.approve).toHaveBeenCalledWith('m1')
    await waitFor(() => expect(api.memory.list).toHaveBeenCalledTimes(2))
    expect(await screen.findByText(/没有待审核的记忆/)).toBeTruthy()
  })

  it('操作失败就地显示原因，列表不被本地乐观改掉', async () => {
    const user = userEvent.setup()
    vi.mocked(api.memory.list).mockResolvedValue(listResult([memory()]))
    vi.mocked(api.memory.approve).mockResolvedValue({
      ok: false,
      code: 'CONFLICT',
      message: '这条记忆已被处理。',
    })

    renderPage()

    await user.click(await screen.findByRole('button', { name: '批准' }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('这条记忆已被处理。')
    })
    // 失败后条目仍在原状态，且未触发重新拉取。
    expect(screen.getByText('用户偏好简洁的回复。')).toBeTruthy()
    expect(api.memory.list).toHaveBeenCalledTimes(1)
  })
})
