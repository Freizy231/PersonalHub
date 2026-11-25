import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { HomePage } from './HomePage'

vi.mock('../services/api', () => ({
  api: {
    health: vi.fn(),
    conversations: { list: vi.fn() },
    approvals: { list: vi.fn() },
    userTasks: { list: vi.fn() },
    jobs: { list: vi.fn() },
  },
}))

const { api } = await import('../services/api')

const ok = <T,>(data: T) => ({ ok: true as const, data })

beforeEach(() => {
  vi.mocked(api.health).mockResolvedValue(
    ok({ status: 'ok', service: 'Personal Hub', version: '0.0.1', timestamp: '2026-11-25T00:00:00Z' }),
  )
  vi.mocked(api.conversations.list).mockResolvedValue(
    ok({ items: [], total: 0, limit: 5, offset: 0 }),
  )
  vi.mocked(api.approvals.list).mockResolvedValue(ok({ items: [], total: 0 }))
  vi.mocked(api.userTasks.list).mockResolvedValue(
    ok({ items: [], total: 0, limit: 50, offset: 0, open_count: 0 }),
  )
  vi.mocked(api.jobs.list).mockResolvedValue(ok({ items: [], total: 0 }))
})

function renderHome() {
  return render(
    <MemoryRouter>
      <HomePage />
    </MemoryRouter>,
  )
}

const baseJob = {
  attempts: 1,
  max_attempts: 3,
  last_error: null,
  available_at: '2026-11-25T00:00:00Z',
  created_at: '2026-11-25T00:00:00Z',
  updated_at: '2026-11-25T00:00:00Z',
}

describe('HomePage 工作台', () => {
  it('四个模块同时存在，并有各自的空状态入口', async () => {
    renderHome()

    expect(screen.getByRole('heading', { name: '我的任务' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '待确认' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '后台作业' })).toBeTruthy()
    expect(screen.getByRole('heading', { name: '继续对话' })).toBeTruthy()

    await waitFor(() => {
      expect(screen.getByText('没有未完成的任务。')).toBeTruthy()
      expect(screen.getByText('没有等待你确认的操作。')).toBeTruthy()
      expect(screen.getByText('没有正在进行或最近失败的作业。')).toBeTruthy()
      expect(screen.getByText('还没有会话。')).toBeTruthy()
    })
    // 空状态给出可执行入口，而不是只写"暂无数据"。
    expect(screen.getByRole('link', { name: '在会话里让 AI 记一件' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '生成一个小应用' })).toBeTruthy()
    expect(screen.getByRole('link', { name: '开始第一次对话' })).toBeTruthy()
  })

  it('用户任务与后台作业分属不同卡片', async () => {
    vi.mocked(api.userTasks.list).mockResolvedValue(
      ok({
        items: [
          {
            id: 't1',
            title: '学习 TypeScript',
            description: '',
            status: 'OPEN' as const,
            created_at: '2026-11-25T00:00:00Z',
            updated_at: '2026-11-25T00:00:00Z',
            created_via_approval_id: null,
          },
        ],
        total: 1,
        limit: 50,
        offset: 0,
        open_count: 1,
      }),
    )
    vi.mocked(api.jobs.list).mockResolvedValue(
      ok({
        items: [
          {
            ...baseJob,
            id: 'j1',
            job_type: 'generate_app',
            status: 'RUNNING' as const,
            summary: '做一个番茄钟',
            stage: 'progress',
          },
        ],
        total: 1,
      }),
    )

    renderHome()

    const tasksCard = screen.getByRole('heading', { name: '我的任务' }).closest('article') as HTMLElement
    const jobsCard = screen.getByRole('heading', { name: '后台作业' }).closest('article') as HTMLElement

    await waitFor(() => expect(within(tasksCard).getByText('学习 TypeScript')).toBeTruthy())
    expect(within(jobsCard).getByText('生成应用')).toBeTruthy()

    // 任务不会跑到作业卡片里，反之亦然。
    expect(within(jobsCard).queryByText('学习 TypeScript')).toBeNull()
    expect(within(tasksCard).queryByText('生成应用')).toBeNull()
  })

  it('一个模块失败不影响其他模块，并可在该卡片内重试', async () => {
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce({ ok: false, code: 'NETWORK_ERROR', message: '作业列表加载失败。' })
      .mockResolvedValueOnce(ok({ items: [], total: 0 }))
    vi.mocked(api.userTasks.list).mockResolvedValue(
      ok({ items: [], total: 0, limit: 50, offset: 0, open_count: 0 }),
    )

    renderHome()

    await waitFor(() => expect(screen.getByText('作业列表加载失败。')).toBeTruthy())
    // 其他模块仍然可用。
    expect(screen.getByText('没有未完成的任务。')).toBeTruthy()
    expect(screen.getByText('没有等待你确认的操作。')).toBeTruthy()

    const errorCard = screen.getByText('作业列表加载失败。').closest('article') as HTMLElement
    fireEvent.click(within(errorCard).getByRole('button', { name: '重试' }))

    await waitFor(() => expect(screen.getByText('没有正在进行或最近失败的作业。')).toBeTruthy())
  })

  it('待审批显示数量', async () => {
    vi.mocked(api.approvals.list).mockResolvedValue(
      ok({
        items: [
          {
            id: 'a1',
            tool_name: 'create_task',
            arguments_preview: '标题：学习 TypeScript',
            status: 'PENDING' as const,
            conversation_id: 'c1',
            expires_at: '2026-11-25T01:00:00Z',
            decided_at: null,
            consumed: 0,
          },
        ],
        total: 1,
      }),
    )

    renderHome()

    const card = screen.getByRole('heading', { name: '待确认' }).closest('article') as HTMLElement
    await waitFor(() => expect(within(card).getByText('1')).toBeTruthy())
    // 待确认项直接显示参数摘要，不再加"待确认："前缀（卡片标题已说明）。
    expect(within(card).getByText('标题：学习 TypeScript')).toBeTruthy()
  })
})
