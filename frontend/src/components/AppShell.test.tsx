import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { AppShell } from './AppShell'

vi.mock('../services/api', () => ({
  api: { health: vi.fn() },
}))

const { api } = await import('../services/api')

function renderShell(route = '/') {
  return render(
    <MemoryRouter initialEntries={[route]}>
      <AppShell>
        <p>页面内容</p>
      </AppShell>
    </MemoryRouter>,
  )
}

const nav = () => screen.getByRole('navigation', { name: '主导航' })

beforeEach(() => {
  vi.mocked(api.health).mockResolvedValue({
    ok: true,
    data: { status: 'ok', service: 'Personal Hub', version: '0.0.1', timestamp: '2026-11-25T00:00:00Z' },
  })
})

describe('AppShell：左侧应用导航', () => {
  it('导航项使用面向用户的名称，不是内部队列名', () => {
    renderShell()

    for (const label of ['工作台', '对话', '记忆', '我的任务', '后台作业', '活动']) {
      expect(within(nav()).getByRole('link', { name: label })).toBeTruthy()
    }
    // 「我的任务」与「后台作业」必须都能找到——它们是两个不同实体（D-013）。
    expect(within(nav()).getByRole('link', { name: '我的任务' })).toBeTruthy()
    expect(within(nav()).getByRole('link', { name: '后台作业' })).toBeTruthy()
  })

  it('扩展能力单独分组，设置不占主导航', () => {
    renderShell()

    const extensions = screen.getByRole('list', { name: '扩展' })
    expect(within(extensions).getByRole('link', { name: '能力' })).toBeTruthy()
    expect(within(extensions).getByRole('link', { name: '应用' })).toBeTruthy()
    // 设置是底部入口，不属于主导航列表。
    expect(within(nav()).queryByRole('link', { name: '设置' })).toBeNull()
    expect(screen.getByRole('link', { name: '设置' })).toBeTruthy()
  })

  it('当前页面在导航里被标记（aria-current）', () => {
    renderShell('/jobs')

    // NavLink 用 aria-current="page" 表达选中态——不只靠颜色。
    expect(screen.getByRole('link', { name: '后台作业' }).getAttribute('aria-current')).toBe('page')
    expect(screen.getByRole('link', { name: '我的任务' }).getAttribute('aria-current')).toBeNull()
  })

  it('工作台用精确匹配：进入 /conversations 时工作台不再选中', () => {
    renderShell('/conversations')

    expect(screen.getByRole('link', { name: '工作台' }).getAttribute('aria-current')).toBeNull()
    expect(screen.getByRole('link', { name: '对话' }).getAttribute('aria-current')).toBe('page')
  })

  it('服务状态是辅助信息，不作为主导航项', async () => {
    renderShell()

    await waitFor(() => {
      expect(screen.getByText(/本地服务/)).toBeTruthy()
    })
    expect(within(nav()).queryByText(/本地服务/)).toBeNull()
  })
})

describe('AppShell：手机抽屉', () => {
  it('菜单按钮有可访问名称，并声明控制的是哪个元素', () => {
    renderShell()

    const toggle = screen.getByRole('button', { name: '打开导航' })
    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    expect(toggle.getAttribute('aria-controls')).toBe('app-sidebar')
    expect(document.querySelector('#app-sidebar')).toBeTruthy()
  })

  it('打开后焦点移进抽屉，Esc 关闭并把焦点还给菜单按钮', async () => {
    const user = userEvent.setup()
    renderShell()

    const toggle = screen.getByRole('button', { name: '打开导航' })
    await user.click(toggle)

    expect(toggle.getAttribute('aria-expanded')).toBe('true')
    // 焦点进入抽屉，键盘用户不必再 Tab 很多次。
    await waitFor(() => {
      expect(document.activeElement?.textContent).toBe('工作台')
    })

    await user.keyboard('{Escape}')

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
    // 关键：焦点必须回到按钮。留在已滑出视口的链接上，用户会彻底迷路。
    expect(document.activeElement).toBe(toggle)
  })

  it('点遮罩关闭抽屉', async () => {
    const user = userEvent.setup()
    renderShell()

    const toggle = screen.getByRole('button', { name: '打开导航' })
    await user.click(toggle)
    await user.click(screen.getByRole('button', { name: '关闭导航' }))

    expect(toggle.getAttribute('aria-expanded')).toBe('false')
  })

  it('抽屉打开时有明确关闭方式（遮罩带可访问名称）', async () => {
    const user = userEvent.setup()
    renderShell()

    await user.click(screen.getByRole('button', { name: '打开导航' }))

    const scrim = screen.getByRole('button', { name: '关闭导航' })
    // 遮罩不可 Tab 聚焦（否则键盘顺序里会多一个隐形停靠点），
    // 但仍可被读屏软件按名称找到。
    expect(scrim.getAttribute('tabindex')).toBe('-1')
  })
})
