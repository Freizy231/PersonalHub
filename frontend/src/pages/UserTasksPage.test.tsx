import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { UserTask } from '../types/api'
import { UserTasksPage } from './UserTasksPage'

vi.mock('../services/api', () => ({
  api: {
    userTasks: {
      list: vi.fn(),
      complete: vi.fn(),
      reopen: vi.fn(),
      remove: vi.fn(),
    },
  },
}))

const { api } = await import('../services/api')

function task(overrides: Partial<UserTask> = {}): UserTask {
  return {
    id: 't1',
    title: '学习 TypeScript',
    description: '',
    status: 'OPEN',
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    created_via_approval_id: null,
    ...overrides,
  }
}

function listResult(items: UserTask[], openCount = items.filter((t) => t.status === 'OPEN').length) {
  return {
    ok: true as const,
    data: { items, total: items.length, limit: 50, offset: 0, open_count: openCount },
  }
}

function renderPage(filter: 'ALL' | 'OPEN' | 'DONE' = 'ALL') {
  return render(
    <MemoryRouter>
      <UserTasksPage filter={filter} />
    </MemoryRouter>,
  )
}

/** 定位某个任务条目。
 *
 * 筛选标签也叫「未完成 / 已完成」，全局文本查询会同时命中标签，
 * 因此状态断言必须限定在条目内部。
 */
function findItemByTitle(title: string): HTMLElement {
  const heading = screen.getByRole('heading', { name: title })
  const item = heading.closest('li')
  if (!item) throw new Error(`找不到任务条目：${title}`)
  return item as HTMLElement
}

beforeEach(() => {
  vi.mocked(api.userTasks.list).mockReset()
  vi.mocked(api.userTasks.complete).mockReset()
  vi.mocked(api.userTasks.reopen).mockReset()
  vi.mocked(api.userTasks.remove).mockReset()
})

describe('UserTasksPage', () => {
  it('只展示用户任务，并标明来源是对话中的确认', async () => {
    vi.mocked(api.userTasks.list).mockResolvedValue(
      listResult([task({ created_via_approval_id: 'a'.repeat(32) })]),
    )

    renderPage()

    const heading = await screen.findByRole('heading', { name: '学习 TypeScript' })
    const item = heading.closest('li') as HTMLElement
    expect(within(item).getByText('未完成')).toBeTruthy()
    expect(within(item).getByText('来自对话中的操作确认')).toBeTruthy()
    // 用户任务没有执行阶段、取消、重试这些作业动作。
    expect(within(item).queryByText('取消')).toBeNull()
    expect(within(item).queryByText('重试')).toBeNull()
    expect(screen.queryByText(/当前阶段/)).toBeNull()
  })

  it('标记完成会调用接口并重新拉取列表', async () => {
    const user = userEvent.setup()
    vi.mocked(api.userTasks.list)
      .mockResolvedValueOnce(listResult([task()]))
      .mockResolvedValueOnce(listResult([task({ status: 'DONE' })]))
    vi.mocked(api.userTasks.complete).mockResolvedValue({ ok: true, data: task({ status: 'DONE' }) })

    renderPage()
    await user.click(await screen.findByRole('button', { name: '标记完成' }))

    expect(api.userTasks.complete).toHaveBeenCalledWith('t1')
    // "刷新后仍保持完成"：列表以服务端为准，重新拉取后显示已完成。
    await waitFor(() => {
      const item = findItemByTitle('学习 TypeScript')
      expect(within(item).getByText('已完成')).toBeTruthy()
    })
    expect(screen.getByRole('button', { name: '重新打开' })).toBeTruthy()
  })

  it('已完成的任务可以重新打开', async () => {
    const user = userEvent.setup()
    vi.mocked(api.userTasks.list)
      .mockResolvedValueOnce(listResult([task({ status: 'DONE' })]))
      .mockResolvedValueOnce(listResult([task()]))
    vi.mocked(api.userTasks.reopen).mockResolvedValue({ ok: true, data: task() })

    renderPage('DONE')
    await user.click(await screen.findByRole('button', { name: '重新打开' }))

    expect(api.userTasks.reopen).toHaveBeenCalledWith('t1')
    await waitFor(() => {
      const item = findItemByTitle('学习 TypeScript')
      expect(within(item).getByText('未完成')).toBeTruthy()
    })
  })

  it('删除需要两段确认，说明后果', async () => {
    const user = userEvent.setup()
    vi.mocked(api.userTasks.list).mockResolvedValue(listResult([task()]))
    vi.mocked(api.userTasks.remove).mockResolvedValue({ ok: true, data: undefined })

    renderPage()
    await user.click(await screen.findByRole('button', { name: '删除' }))

    // 第一次点击只进入确认态，不能已调用删除。
    expect(api.userTasks.remove).not.toHaveBeenCalled()
    expect(screen.getByText(/删除后无法恢复/)).toBeTruthy()

    const confirmGroup = screen.getByRole('group', { name: '确认删除任务' })
    await user.click(within(confirmGroup).getByRole('button', { name: '删除' }))

    expect(api.userTasks.remove).toHaveBeenCalledWith('t1')
  })

  it('取消删除不调用接口', async () => {
    const user = userEvent.setup()
    vi.mocked(api.userTasks.list).mockResolvedValue(listResult([task()]))

    renderPage()
    await user.click(await screen.findByRole('button', { name: '删除' }))
    const confirmGroup = screen.getByRole('group', { name: '确认删除任务' })
    await user.click(within(confirmGroup).getByRole('button', { name: '取消' }))

    expect(api.userTasks.remove).not.toHaveBeenCalled()
    expect(api.userTasks.list).toHaveBeenCalledTimes(1)
  })

  it('空状态给出去会话的下一步，而不是空白页', async () => {
    vi.mocked(api.userTasks.list).mockResolvedValue(listResult([]))

    renderPage()

    expect(await screen.findByText('还没有任务。')).toBeTruthy()
    expect(screen.getAllByRole('link', { name: '去会话' }).length).toBeGreaterThan(0)
  })

  it('加载失败展示错误并可重试，重试后恢复', async () => {
    const user = userEvent.setup()
    vi.mocked(api.userTasks.list)
      .mockResolvedValueOnce({ ok: false, code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' })
      .mockResolvedValueOnce(listResult([task()]))

    renderPage()

    expect(await screen.findByText('无法连接到本地后端服务。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByRole('heading', { name: '学习 TypeScript' })).toBeTruthy()
  })

  it('按筛选取状态参数，并显示未完成统计口径', async () => {
    vi.mocked(api.userTasks.list).mockResolvedValue(listResult([task({ status: 'DONE' })], 4))

    renderPage('DONE')

    await screen.findByRole('heading', { name: '学习 TypeScript' })
    expect(api.userTasks.list).toHaveBeenCalledWith('DONE')
    expect(screen.getByText('4 项未完成 · 共 1 项')).toBeTruthy()
  })
})
