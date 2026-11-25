import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { EarlierMessages } from './EarlierMessages'

function renderEarlier(props: Partial<Parameters<typeof EarlierMessages>[0]> = {}) {
  const onLoad = vi.fn()
  const utils = render(
    <EarlierMessages
      visible
      hasEarlier
      loading={false}
      error={null}
      onLoad={onLoad}
      {...props}
    />,
  )
  return { ...utils, onLoad }
}

describe('EarlierMessages', () => {
  it('还有更早消息时显示加载按钮', async () => {
    const user = userEvent.setup()
    const { onLoad } = renderEarlier()

    await user.click(screen.getByRole('button', { name: '加载更早的消息' }))

    expect(onLoad).toHaveBeenCalledTimes(1)
  })

  it('没有更早消息时说明"已经到开头"，不显示按钮', () => {
    renderEarlier({ hasEarlier: false })

    expect(screen.queryByRole('button')).toBeNull()
    expect(screen.getByText('已经到对话的开头了。')).toBeTruthy()
  })

  it('加载中禁用按钮并说明', () => {
    renderEarlier({ loading: true })

    const button = screen.getByRole('button') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect(button.textContent).toContain('正在加载…')
  })

  it('加载失败时提示可重试，按钮仍然可用', () => {
    renderEarlier({ error: '无法连接到本地后端服务。' })

    expect(screen.getByRole('alert').textContent).toContain('可以再试一次')
    expect((screen.getByRole('button') as HTMLButtonElement).disabled).toBe(false)
  })

  it('空会话不渲染（没有"更早"可言）', () => {
    const { container } = renderEarlier({ visible: false })
    expect(container.firstChild).toBeNull()
  })
})
