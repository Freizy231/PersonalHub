import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import { INITIAL_SEND_STATE, type SendState } from '../services/sendState'
import { SendStatusBar } from './SendStatusBar'

function renderBar(state: Partial<SendState>, handlers: Partial<Parameters<typeof SendStatusBar>[0]> = {}) {
  const onRetry = vi.fn()
  const onDismiss = vi.fn()
  const utils = render(
    <SendStatusBar
      state={{ ...INITIAL_SEND_STATE, ...state }}
      onRetry={onRetry}
      onDismiss={onDismiss}
      {...handlers}
    />,
  )
  return { ...utils, onRetry, onDismiss }
}

describe('SendStatusBar', () => {
  it('空闲时不渲染任何东西', () => {
    const { container } = renderBar({})
    expect(container.firstChild).toBeNull()
  })

  it('发送中显示持续状态（不是 alert）', () => {
    renderBar({ phase: 'sending' })

    const status = screen.getByRole('status')
    expect(status.textContent).toContain('正在发送…')
    expect(screen.queryByRole('alert')).toBeNull()
  })

  it('停止中显示"正在停止…"，让用户知道按到了', () => {
    renderBar({ phase: 'stopping' })
    expect(screen.getByText('正在停止…')).toBeTruthy()
  })

  it('可重试的中断：显示原因与完整重试入口', async () => {
    const user = userEvent.setup()
    const { onRetry, onDismiss } = renderBar({
      phase: 'interrupted',
      failure: {
        code: 'PROVIDER_RATE_LIMIT',
        message: '请求过于频繁，请稍后重试。',
        retryable: true,
        partial: false,
      },
      retryText: '你好',
    })

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('请求过于频繁')
    expect(alert.textContent).toContain('PROVIDER_RATE_LIMIT')

    await user.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalledTimes(1)

    await user.click(screen.getByRole('button', { name: '不用了' }))
    expect(onDismiss).toHaveBeenCalledTimes(1)
  })

  it('不可重试的错误：说明要检查配置，不给重试按钮', () => {
    renderBar({
      phase: 'interrupted',
      failure: {
        code: 'PROVIDER_NOT_CONFIGURED',
        message: '还没有配置模型密钥。',
        retryable: false,
        partial: false,
      },
      retryText: null,
    })

    expect(screen.queryByRole('button', { name: '重试' })).toBeNull()
    expect(screen.getByText(/重发同样的内容不会成功/)).toBeTruthy()
    expect(screen.getByText(/设置/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '知道了' })).toBeTruthy()
  })

  it('有部分回复时提示内容可能不完整', () => {
    renderBar({
      phase: 'interrupted',
      failure: {
        code: 'STREAM_INTERRUPTED',
        message: '连接中断，回复可能不完整。',
        retryable: true,
        partial: true,
      },
      retryText: '你好',
    })

    expect(screen.getByRole('alert').textContent).toContain('可能不完整')
  })

  it('用户主动停止：说明回复没有保存，并允许重发', async () => {
    const user = userEvent.setup()
    const { onRetry } = renderBar({
      phase: 'interrupted',
      failure: {
        code: 'USER_ABORTED',
        message: '已停止生成，这次的回复没有保存。',
        retryable: true,
        partial: false,
      },
      retryText: '你好',
    })

    expect(screen.getByRole('alert').textContent).toContain('没有保存')
    await user.click(screen.getByRole('button', { name: '重试' }))
    expect(onRetry).toHaveBeenCalled()
  })
})
