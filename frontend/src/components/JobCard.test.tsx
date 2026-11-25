import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Job } from '../types/api'
import { JobCard } from './JobCard'

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

function renderJob(overrides: Partial<Job> = {}, busy = false) {
  return render(
    <MemoryRouter>
      <ul>
        <JobCard
          job={job(overrides)}
          busy={busy}
          onCancel={() => {}}
          onRetry={() => {}}
        />
      </ul>
    </MemoryRouter>,
  )
}

beforeEach(() => {
  vi.restoreAllMocks()
})

describe('JobCard：把内部作业翻译成用户可读信息', () => {
  it('主标题是用户可读名称，不是内部标识 generate_app', () => {
    renderJob()

    expect(screen.getByText('生成应用')).toBeTruthy()
    expect(screen.queryByText('generate_app')).toBeNull()
  })

  it('未知类型退回原名（不假装有中文名）', () => {
    renderJob({ job_type: 'some_future_job' })

    expect(screen.getByText('some_future_job')).toBeTruthy()
  })

  it('显示服务端提供的输入摘要', () => {
    renderJob({ summary: '做一个番茄钟计时器' })

    expect(screen.getByText('做一个番茄钟计时器')).toBeTruthy()
  })

  it('执行中的作业显示当前阶段', () => {
    renderJob({ stage: 'progress', stage_message: 'generating' })

    const item = screen.getByText('生成应用').closest('li') as HTMLElement
    expect(within(item).getByText(/当前阶段：生成中/)).toBeTruthy()
    expect(within(item).getByText(/generating/)).toBeTruthy()
  })

  it('已结束的作业不显示当前阶段（不是仍在进行）', () => {
    renderJob({ status: 'SUCCEEDED', stage: 'progress', stage_message: 'generating' })

    expect(screen.queryByText(/当前阶段/)).toBeNull()
  })

  it('失败作业显示失败原因并可重试', () => {
    renderJob({ status: 'FAILED', last_error: '模型未配置' })

    expect(screen.getByText('失败原因：模型未配置')).toBeTruthy()
    expect(screen.getByRole('button', { name: '重试' })).toBeTruthy()
    // 已结束的作业没有"取消"。
    expect(screen.queryByRole('button', { name: '取消' })).toBeNull()
  })

  it('作业只提供作业动作：取消/重试，没有"标记完成"', () => {
    renderJob({ status: 'RUNNING' })

    expect(screen.getByRole('button', { name: '取消' })).toBeTruthy()
    expect(screen.queryByRole('button', { name: '标记完成' })).toBeNull()
  })

  it('显示尝试次数与最近更新时间', () => {
    renderJob({ attempts: 2, max_attempts: 3 })

    expect(screen.getByText('尝试 2/3')).toBeTruthy()
    expect(screen.getByText(/更新于/)).toBeTruthy()
  })
})
