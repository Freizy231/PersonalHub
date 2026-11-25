import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job, JobList } from '../types/api'
import { JobCenterPage } from './JobCenterPage'

vi.mock('../services/api', () => ({
  api: {
    jobs: {
      list: vi.fn(),
      cancel: vi.fn(),
      retry: vi.fn(),
    },
  },
}))

const { api } = await import('../services/api')

function job(overrides: Partial<Job> = {}): Job {
  return {
    id: 'j1',
    job_type: 'generate_app',
    status: 'RUNNING',
    attempts: 1,
    max_attempts: 3,
    last_error: null,
    available_at: '2026-11-25T00:00:00Z',
    created_at: '2026-11-25T00:00:00Z',
    updated_at: '2026-11-25T00:00:00Z',
    ...overrides,
  }
}

function listResult(items: Job[]): { ok: true; data: JobList } {
  return { ok: true, data: { items, total: items.length } }
}

function renderPage() {
  return render(
    <MemoryRouter>
      <JobCenterPage filter="ALL" />
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.mocked(api.jobs.list).mockReset()
  vi.mocked(api.jobs.cancel).mockReset()
  vi.mocked(api.jobs.retry).mockReset()
})

describe('JobCenterPage：作业列表的加载、失败与空状态', () => {
  it('加载中显示占位，不显示"还没有后台作业"', async () => {
    let resolve: (value: { ok: true; data: JobList }) => void = () => {}
    vi.mocked(api.jobs.list).mockReturnValue(
      new Promise((r) => {
        resolve = r
      }),
    )

    renderPage()

    expect(screen.getAllByText(/正在加载/).length).toBeGreaterThan(0)
    // 关键：加载期间不能断言"空"，否则用户会以为真的没有作业。
    expect(screen.queryByText('还没有后台作业。')).toBeNull()
    resolve(listResult([job()]))
    expect(await screen.findByText('共 1 个')).toBeTruthy()
  })

  it('加载失败显示原因并可重试，重试后恢复列表', async () => {
    const user = userEvent.setup()
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce({ ok: false, code: 'NETWORK_ERROR', message: '无法连接到本地后端服务。' })
      .mockResolvedValueOnce(listResult([job()]))

    renderPage()

    expect(await screen.findByText('无法连接到本地后端服务。')).toBeTruthy()
    await user.click(screen.getByRole('button', { name: '重试' }))

    expect(await screen.findByText('共 1 个')).toBeTruthy()
  })

  it('空状态说明作业从哪来，并指向「应用」页', async () => {
    vi.mocked(api.jobs.list).mockResolvedValue(listResult([]))

    renderPage()

    expect(await screen.findByText('还没有后台作业。')).toBeTruthy()
    expect(screen.getByRole('link', { name: '应用' })).toBeTruthy()
  })

  it('页面区分「我的任务」与「后台作业」，不把用户事项混进来', async () => {
    vi.mocked(api.jobs.list).mockResolvedValue(listResult([]))

    renderPage()

    await screen.findByText('还没有后台作业。')
    expect(screen.getByRole('link', { name: '我的任务' })).toBeTruthy()
    // 作业页没有"标记完成"这类用户任务动作。
    expect(screen.queryByRole('button', { name: '标记完成' })).toBeNull()
  })

  it('取消失败时就地显示原因，不假装已取消', async () => {
    const user = userEvent.setup()
    vi.mocked(api.jobs.list).mockResolvedValue(listResult([job()]))
    vi.mocked(api.jobs.cancel).mockResolvedValue({ ok: false, code: 'CONFLICT', message: '作业已结束，无法取消。' })

    renderPage()

    await user.click(await screen.findByRole('button', { name: '取消' }))

    await waitFor(() => {
      expect(screen.getByRole('alert').textContent).toContain('作业已结束，无法取消。')
    })
    expect(api.jobs.cancel).toHaveBeenCalledWith('j1')
    // 列表仍以服务端为准：失败后没有被本地乐观改掉。
    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy()
  })

  it('操作成功后重新拉取列表，以服务端状态为准', async () => {
    const user = userEvent.setup()
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce(listResult([job({ status: 'FAILED', last_error: '模型超时' })]))
      .mockResolvedValueOnce(listResult([job({ status: 'PENDING' })]))
    vi.mocked(api.jobs.retry).mockResolvedValue({ ok: true, data: job({ status: 'PENDING' }) })

    renderPage()

    await user.click(await screen.findByRole('button', { name: '重试' }))

    expect(api.jobs.retry).toHaveBeenCalledWith('j1')
    await waitFor(() => expect(api.jobs.list).toHaveBeenCalledTimes(2))
  })
})
