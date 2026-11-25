import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { StagedApp } from '../types/api'
import { FlowSteps, PendingStaging, StagedPreview } from './AppFlow'

function staged(overrides: Partial<StagedApp> = {}): StagedApp {
  return {
    staging_id: 's1',
    app_id: 'app-1',
    title: '番茄钟',
    purpose: '一个计时器',
    elements: ['开始按钮', '倒计时'],
    capability: 'local_compute',
    size_bytes: 2048,
    generated_at: '2026-11-25T00:00:00Z',
    is_update: false,
    base_version: null,
    change_reason: '',
    current_version: null,
    next_version: '1.0.0',
    change_stats: null,
    ...overrides,
  }
}

describe('FlowSteps', () => {
  it('把五步按顺序列出，并标出当前步', () => {
    render(
      <FlowSteps
        steps={[
          { key: 'describe', label: '描述需求', state: 'done' },
          { key: 'generate', label: '后台生成', state: 'active' },
          { key: 'check', label: '安全检查', state: 'pending' },
          { key: 'preview', label: '预览结果', state: 'pending' },
          { key: 'install', label: '你确认安装', state: 'pending' },
        ]}
      />,
    )

    const items = screen.getAllByRole('listitem')
    expect(items.map((item) => item.textContent)).toEqual([
      expect.stringContaining('描述需求'),
      expect.stringContaining('后台生成'),
      expect.stringContaining('安全检查'),
      expect.stringContaining('预览结果'),
      expect.stringContaining('你确认安装'),
    ])
    expect(items[1]?.getAttribute('aria-current')).toBe('step')
  })

  it('状态不只靠颜色：每一格都有文字说明', () => {
    render(
      <FlowSteps
        steps={[
          { key: 'describe', label: '描述需求', state: 'done' },
          { key: 'generate', label: '后台生成', state: 'failed' },
        ]}
      />,
    )

    expect(screen.getByText('（已完成）')).toBeTruthy()
    expect(screen.getByText('（失败）')).toBeTruthy()
  })
})

describe('StagedPreview', () => {
  it('首次安装：说明会装成什么，并让用户确认', () => {
    const onInstall = vi.fn()
    render(
      <StagedPreview
        staged={staged()}
        contentSrc="/api/v1/apps/staging/s1/content"
        busy={false}
        installing={false}
        onInstall={onInstall}
        onDiscard={() => {}}
      />,
    )

    expect(screen.getByText('「番茄钟」是第一次安装')).toBeTruthy()
    expect(screen.getByText('尚未安装')).toBeTruthy()
    expect(screen.getByRole('button', { name: '安装 1.0.0' })).toBeTruthy()
    expect(screen.getByText(/沙箱/)).toBeTruthy()
  })

  it('升级：安装前就写明当前版本与目标版本', () => {
    render(
      <StagedPreview
        staged={staged({ current_version: '1.0.0', next_version: '1.0.1' })}
        contentSrc="/x"
        busy={false}
        installing={false}
        onInstall={() => {}}
        onDiscard={() => {}}
      />,
    )

    const button = screen.getByRole('button', { name: /安装为 1\.0\.1/ })
    expect(button.textContent).toContain('1.0.0')
    expect(screen.getByText('1.0.0')).toBeTruthy()
  })

  it('修改建议：显示改动幅度与"当前版本不会改变"', () => {
    render(
      <StagedPreview
        staged={staged({
          is_update: true,
          base_version: '1.0.0',
          current_version: '1.0.0',
          next_version: '1.0.1',
          change_reason: '加上暂停按钮',
          change_stats: { added_lines: 3, removed_lines: 1 },
        })}
        contentSrc="/x"
        busy={false}
        installing={false}
        onInstall={() => {}}
        onDiscard={() => {}}
      />,
    )

    expect(screen.getByText(/修改建议/)).toBeTruthy()
    expect(screen.getByText(/新增 3 行、删除 1 行/)).toBeTruthy()
    expect(screen.getByText(/当前在用的版本不会改变/)).toBeTruthy()
  })

  it('模型自述的修改原因标明"未经核实"', () => {
    render(
      <StagedPreview
        staged={staged({
          is_update: true,
          base_version: '1.0.0',
          current_version: '1.0.0',
          change_reason: '我已修好所有问题',
        })}
        contentSrc="/x"
        busy={false}
        installing={false}
        onInstall={() => {}}
        onDiscard={() => {}}
      />,
    )

    const reason = screen.getByText(/我已修好所有问题/).textContent ?? ''
    expect(reason).toContain('未经核实')
  })

  it('iframe 只用 allow-scripts，不加 allow-same-origin', () => {
    render(
      <StagedPreview
        staged={staged()}
        contentSrc="/api/v1/apps/staging/s1/content"
        busy={false}
        installing={false}
        onInstall={() => {}}
        onDiscard={() => {}}
      />,
    )

    const frame = screen.getByTitle('预览：番茄钟') as HTMLIFrameElement
    expect(frame.getAttribute('sandbox')).toBe('allow-scripts')
    expect(frame.getAttribute('referrerpolicy')).toBe('no-referrer')
  })

  it('安装中禁用按钮并改文案', () => {
    render(
      <StagedPreview
        staged={staged()}
        contentSrc="/x"
        busy
        installing
        onInstall={() => {}}
        onDiscard={() => {}}
      />,
    )

    expect(screen.getByText('安装中…')).toBeTruthy()
    const buttons = screen.getAllByRole('button')
    expect(buttons.every((button) => (button as HTMLButtonElement).disabled)).toBe(true)
  })

  it('丢弃是独立动作，不与安装混淆', async () => {
    const user = userEvent.setup()
    const onDiscard = vi.fn()
    render(
      <StagedPreview
        staged={staged()}
        contentSrc="/x"
        busy={false}
        installing={false}
        onInstall={() => {}}
        onDiscard={onDiscard}
      />,
    )

    await user.click(screen.getByRole('button', { name: '丢弃这次结果' }))
    expect(onDiscard).toHaveBeenCalledTimes(1)
  })
})

describe('PendingStaging', () => {
  it('列出未安装的结果，并说明会被自动清理', () => {
    render(
      <PendingStaging
        items={[staged({ staging_id: 's2', title: '记账本' })]}
        activeId={null}
        busyId={null}
        onOpen={() => {}}
        onDiscard={() => {}}
      />,
    )

    expect(screen.getByText('记账本')).toBeTruthy()
    expect(screen.getByText(/1 小时内都可以回来预览或安装/)).toBeTruthy()
    expect(screen.getByRole('button', { name: '预览' })).toBeTruthy()
  })

  it('正在预览的那一条不在列表里重复出现', () => {
    render(
      <PendingStaging
        items={[staged({ staging_id: 's1', title: '番茄钟' })]}
        activeId="s1"
        busyId={null}
        onOpen={() => {}}
        onDiscard={() => {}}
      />,
    )

    expect(screen.queryByText('番茄钟')).toBeNull()
  })

  it('区分"新安装"与"修改建议"', () => {
    render(
      <PendingStaging
        items={[
          staged({ staging_id: 's2', title: 'A', next_version: '1.0.0' }),
          staged({
            staging_id: 's3',
            title: 'B',
            is_update: true,
            base_version: '1.0.0',
            next_version: '1.0.1',
          }),
        ]}
        activeId={null}
        busyId={null}
        onOpen={() => {}}
        onDiscard={() => {}}
      />,
    )

    const [first, second] = screen.getAllByRole('listitem')
    expect(first).toBeDefined()
    expect(second).toBeDefined()
    expect(within(first as HTMLElement).getByText(/新安装 1\.0\.0/)).toBeTruthy()
    expect(within(second as HTMLElement).getByText(/修改建议 → 1\.0\.1/)).toBeTruthy()
  })

  it('没有待安装结果时不渲染整个区块', () => {
    const { container } = render(
      <PendingStaging
        items={[]}
        activeId={null}
        busyId={null}
        onOpen={() => {}}
        onDiscard={() => {}}
      />,
    )

    expect(container.firstChild).toBeNull()
  })
})
