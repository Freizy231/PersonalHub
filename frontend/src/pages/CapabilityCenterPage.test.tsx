import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import type { Capability } from '../types/api'
import { CapabilityCenterPage } from './CapabilityCenterPage'

vi.mock('../services/api', () => ({
  api: {
    capabilities: {
      list: vi.fn(),
      types: vi.fn(),
      dependencyHealth: vi.fn(),
    },
    mcp: { servers: vi.fn() },
  },
}))

const { api } = await import('../services/api')

function capability(overrides: Partial<Capability> = {}): Capability {
  return {
    id: 'builtin.create_task',
    name: '创建任务',
    version: '1.0.0',
    type: 'tool',
    description: '为用户创建一条任务。',
    input_schema: { type: 'object', properties: {} },
    output_schema: {},
    runtime: 'builtin',
    permissions: ['tool:create_task:write'],
    dependencies: [],
    compatibility: '>=0.2.0',
    source: 'builtin',
    signature: null,
    ...overrides,
  }
}

function ok<T>(data: T) {
  return { ok: true as const, data }
}

beforeEach(() => {
  vi.mocked(api.capabilities.list).mockResolvedValue(
    ok({ items: [capability()], total: 1, notice: '声明权限不代表已获授权。' }),
  )
  vi.mocked(api.capabilities.types).mockResolvedValue(
    ok({ items: [{ type: 'tool', count: 5 }], total: 5 }),
  )
  vi.mocked(api.capabilities.dependencyHealth).mockResolvedValue(
    ok({ missing: {}, cycle: [], healthy: true }),
  )
  vi.mocked(api.mcp.servers).mockResolvedValue(
    ok({ items: [], total: 0, errors: [], notice: '' }),
  )
})

function renderPage() {
  return render(
    <MemoryRouter>
      <CapabilityCenterPage />
    </MemoryRouter>,
  )
}

describe('CapabilityCenterPage', () => {
  it('类型筛选只列出有数据的类型', async () => {
    renderPage()

    const select = (await screen.findByLabelText('按类型筛选')) as HTMLSelectElement
    const labels = [...select.options].map((option) => option.textContent)

    expect(labels).toEqual(['全部类型', '工具（5）'])
    // 回归：写死清单里的"工作流"不该出现，它永远筛不出东西。
    expect(labels.some((label) => label?.includes('工作流'))).toBe(false)
  })

  it('写操作能力标为"需要确认"，并说明声明非授权', async () => {
    renderPage()

    const item = (await screen.findByRole('listitem')).closest('li') as HTMLElement
    expect(within(item).getByText('需要确认')).toBeTruthy()
    expect(within(item).getByText('实际调用时会先询问你')).toBeTruthy()
    // 声明权限翻译成人话，不是内部串。
    expect(within(item).getByText('写入：create_task')).toBeTruthy()
    expect(within(item).getAllByText('（声明，非授权）').length).toBeGreaterThan(0)
  })

  it('只读能力标为"只读"', async () => {
    vi.mocked(api.capabilities.list).mockResolvedValue(
      ok({
        items: [
          capability({
            id: 'builtin.list_tasks',
            name: '查看任务',
            permissions: ['tool:list_tasks:read'],
          }),
        ],
        total: 1,
        notice: '',
      }),
    )

    renderPage()

    const item = (await screen.findByRole('listitem')).closest('li') as HTMLElement
    expect(within(item).getByText('只读')).toBeTruthy()
    expect(within(item).getByText('调用时不需要确认')).toBeTruthy()
  })

  it('描述按纯文本显示，不出现 Markdown 星号', async () => {
    vi.mocked(api.capabilities.list).mockResolvedValue(
      ok({
        items: [capability({ description: '修改后的完整单文件 HTML（不是补丁）。' })],
        total: 1,
        notice: '',
      }),
    )

    renderPage()

    const desc = await screen.findByText('修改后的完整单文件 HTML（不是补丁）。')
    expect(desc.tagName).toBe('P')
    expect(desc.textContent).not.toContain('**')
  })

  it('统计口径区分"筛选命中"与"系统总数"', async () => {
    renderPage()

    expect(await screen.findByText(/共 1 个/)).toBeTruthy()
    expect(screen.getByText(/系统共 5 个/)).toBeTruthy()
  })

  it('筛选为空时说明是类型下没有能力，而不是笼统的"没有匹配"', async () => {
    vi.mocked(api.capabilities.list).mockResolvedValue(ok({ items: [], total: 0, notice: '' }))
    vi.mocked(api.capabilities.types).mockResolvedValue(
      ok({ items: [{ type: 'skill', count: 0 }], total: 0 }),
    )

    renderPage()

    expect(await screen.findByText('没有匹配的能力。')).toBeTruthy()
  })

  it('类型接口失败时筛选框禁用，页面仍可用', async () => {
    vi.mocked(api.capabilities.types).mockResolvedValue({
      ok: false,
      code: 'NETWORK_ERROR',
      message: '无法连接。',
    })

    renderPage()

    const select = (await screen.findByLabelText('按类型筛选')) as HTMLSelectElement
    expect(select.disabled).toBe(true)
    // 能力列表本身照常显示。
    expect(await screen.findByText('创建任务')).toBeTruthy()
  })
})
