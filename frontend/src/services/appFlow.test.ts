import { describe, expect, it } from 'vitest'

import type { StagedApp } from '../types/api'
import {
  computeSteps,
  installActionLabel,
  previewHeadline,
  previewNote,
} from './appFlow'

function staged(overrides: Partial<StagedApp> = {}): StagedApp {
  return {
    staging_id: 's1',
    app_id: 'app-1',
    title: '番茄钟',
    purpose: '计时',
    elements: [],
    capability: 'static_display',
    size_bytes: 1024,
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

const base = {
  hasPrompt: false,
  generating: false,
  failure: null,
  staged: null,
  installing: false,
}

function states(steps: ReturnType<typeof computeSteps>): string[] {
  return steps.map((step) => step.state)
}

describe('流程步骤', () => {
  it('空闲时只有第一步可能进行中', () => {
    expect(states(computeSteps({ ...base, hasPrompt: true }))).toEqual([
      'active',
      'pending',
      'pending',
      'pending',
      'pending',
    ])
    expect(states(computeSteps(base))[0]).toBe('pending')
  })

  it('生成中显示第 2 步进行中，检查与预览都还没开始', () => {
    const steps = computeSteps({ ...base, hasPrompt: true, generating: true })

    expect(states(steps)).toEqual(['done', 'active', 'pending', 'pending', 'pending'])
    // 关键：不能在服务端确认之前把"安全检查"标成已完成。
    expect(steps[2]?.state).toBe('pending')
  })

  it('有暂存结果时才把安全检查标为完成', () => {
    const steps = computeSteps({ ...base, hasPrompt: true, staged: staged() })

    expect(states(steps)).toEqual(['done', 'done', 'done', 'active', 'pending'])
  })

  it('安装中把最后一步标为进行中', () => {
    const steps = computeSteps({ ...base, staged: staged(), installing: true })

    expect(states(steps)).toEqual(['done', 'done', 'done', 'done', 'active'])
  })

  it('失败时第 2 步标记失败，后续步骤保持未开始', () => {
    const steps = computeSteps({ ...base, hasPrompt: true, failure: '模型没有返回内容。' })

    expect(states(steps)).toEqual(['done', 'failed', 'pending', 'pending', 'pending'])
  })

  it('失败不会掩盖已存在的预览（用户仍可安装上次的结果）', () => {
    const steps = computeSteps({ ...base, failure: '生成失败。', staged: staged() })

    expect(states(steps)).toEqual(['done', 'done', 'done', 'active', 'pending'])
  })
})

describe('预览文案', () => {
  it('首次安装说明"第一次"', () => {
    expect(previewHeadline(staged())).toBe('「番茄钟」是第一次安装')
  })

  it('新版本说明是"新版本"', () => {
    expect(previewHeadline(staged({ current_version: '1.0.0' }))).toBe('这是「番茄钟」的新版本')
  })

  it('修改建议点明基于哪个版本', () => {
    const item = staged({ is_update: true, base_version: '1.0.1', current_version: '1.0.1' })

    const headline = previewHeadline(item)

    expect(headline).toContain('修改建议')
    expect(headline).toContain('1.0.1')
  })
})

describe('安装按钮', () => {
  it('首次安装只写目标版本', () => {
    expect(installActionLabel(staged())).toBe('安装 1.0.0')
  })

  it('升级时同时写出当前版本与目标版本', () => {
    const label = installActionLabel(
      staged({ current_version: '1.0.0', next_version: '1.0.1' }),
    )

    expect(label).toContain('1.0.1')
    expect(label).toContain('1.0.0')
    expect(label).toContain('替换')
  })
})

describe('安装后果说明', () => {
  it('修改建议强调当前版本不会变', () => {
    const note = previewNote(staged({ is_update: true, base_version: '1.0.0' }))

    expect(note).toContain('当前在用的版本不会改变')
    expect(note).toContain('点击安装')
  })

  it('升级说明旧版本保留可回滚', () => {
    const note = previewNote(staged({ current_version: '1.0.0', next_version: '1.0.1' }))

    expect(note).toContain('回滚')
  })

  it('首次安装说明仍在沙箱里运行', () => {
    expect(previewNote(staged())).toContain('沙箱')
  })
})
