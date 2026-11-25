import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { ApprovalRequestEvent } from '../types/api'
import { ApprovalCard } from './ApprovalCard'

function request(overrides: Partial<ApprovalRequestEvent> = {}): ApprovalRequestEvent {
  return {
    approval_id: 'a1',
    tool: 'create_task',
    title: '创建任务',
    impact: '在你的「我的任务」里新建一条待办事项。',
    summary: [
      { label: '标题', value: '预约牙医' },
      { label: '说明', value: '下周三上午' },
    ],
    arguments: { title: '预约牙医', description: '下周三上午' },
    preview: '标题：预约牙医',
    expires_in: 600,
    ...overrides,
  }
}

function renderCard(props: Partial<Parameters<typeof ApprovalCard>[0]> = {}) {
  return render(
    <ApprovalCard
      request={request()}
      busy={false}
      onDecide={() => {}}
      {...props}
    />,
  )
}

describe('ApprovalCard', () => {
  it('用人话展示工具名、影响与逐项摘要，而不是 JSON', () => {
    renderCard()

    expect(screen.getByText('创建任务')).toBeTruthy()
    expect(screen.getByText('在你的「我的任务」里新建一条待办事项。')).toBeTruthy()
    expect(screen.getByText('预约牙医')).toBeTruthy()
    // 摘要区里不应出现 JSON 括号（原始参数只放在折叠区）。
    const summary = screen.getByText('标题').closest('dl') as HTMLElement
    expect(summary.textContent).not.toContain('{"')
  })

  it('原始参数默认折叠', () => {
    renderCard()

    const details = screen.getByText('查看原始参数').closest('details') as HTMLDetailsElement
    expect(details.open).toBe(false)
  })

  it('工具没有声明影响时不编造影响区块', () => {
    renderCard({ request: request({ impact: '' }) })

    expect(screen.queryByText('会发生什么')).toBeNull()
    // 其余信息仍然完整。
    expect(screen.getByText('创建任务')).toBeTruthy()
  })

  it('批准后显示已批准，并说明结果稍后出现在回复里', async () => {
    const user = userEvent.setup()
    const onDecide = vi.fn()
    renderCard({ onDecide })

    await user.click(screen.getByRole('button', { name: '批准' }))

    expect(onDecide).toHaveBeenCalledWith('approve', false)
  })

  it('已批准终态：不声称已完成，且不再接受操作', () => {
    renderCard({ outcome: { kind: 'approved', tool: 'create_task', title: '创建任务' } })

    const notice = screen.getByText(/已批准「创建任务」/)
    expect(notice.textContent).toContain('正在执行')
    expect(notice.textContent).toContain('结果会出现在下面的回复里')
    expect(notice.textContent).not.toContain('已完成')
    expect(screen.queryByRole('button', { name: '批准' })).toBeNull()
  })

  it('拒绝终态：明确说明操作没有执行', () => {
    renderCard({ outcome: { kind: 'rejected', tool: 'create_task', title: '创建任务' } })

    const notice = screen.getByText(/已拒绝「创建任务」/)
    expect(notice.textContent).toContain('没有被执行')
    expect(screen.queryByRole('button', { name: '拒绝' })).toBeNull()
  })

  it('过期终态：卡片保留并说明未执行', () => {
    renderCard({ outcome: { kind: 'expired', tool: 'create_task', title: '创建任务' } })

    expect(screen.getByText(/已经失效/).textContent).toContain('操作没有被执行')
    // 关键回归：卡片不能凭空消失。
    expect(screen.getByRole('alertdialog')).toBeTruthy()
  })

  it('决策失败就地显示原因与下一步', () => {
    renderCard({
      outcome: {
        kind: 'failed',
        tool: 'create_task',
        title: '创建任务',
        message: '服务返回了未预期的响应。',
      },
    })

    const alert = screen.getByRole('alert')
    expect(alert.textContent).toContain('操作未完成')
    expect(alert.textContent).toContain('服务返回了未预期的响应。')
    expect(alert.textContent).toContain('请重试')
  })

  it('终态时不显示倒计时', () => {
    renderCard({ outcome: { kind: 'rejected', tool: 'create_task', title: '创建任务' } })

    expect(screen.queryByText(/后失效/)).toBeNull()
  })

  it('勾选"本次会话内不再询问"会随批准一起提交', async () => {
    const user = userEvent.setup()
    const onDecide = vi.fn()
    renderCard({ onDecide })

    await user.click(screen.getByRole('checkbox'))
    await user.click(screen.getByRole('button', { name: '批准' }))

    expect(onDecide).toHaveBeenCalledWith('approve', true)
  })

  it('处理中时所有按钮禁用', () => {
    renderCard({ busy: true })

    const buttons = screen.getAllByRole('button')
    expect(buttons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true)
    expect(screen.getByText('处理中…')).toBeTruthy()
  })
})
