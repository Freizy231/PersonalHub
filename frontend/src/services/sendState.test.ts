import { describe, expect, it } from 'vitest'

import {
  INITIAL_SEND_STATE,
  canRetry,
  canStop,
  failureText,
  isBusy,
  phaseLabel,
  type SendState,
} from './sendState'

function state(overrides: Partial<SendState> = {}): SendState {
  return { ...INITIAL_SEND_STATE, ...overrides }
}

describe('发送状态机', () => {
  it('空闲时没有状态文案，也没有停止按钮', () => {
    expect(phaseLabel(INITIAL_SEND_STATE)).toBeNull()
    expect(isBusy(INITIAL_SEND_STATE.phase)).toBe(false)
    expect(canStop(INITIAL_SEND_STATE.phase)).toBe(false)
  })

  it('发送中与回复中有各自的文案', () => {
    expect(phaseLabel(state({ phase: 'sending' }))).toBe('正在发送…')
    expect(phaseLabel(state({ phase: 'streaming' }))).toBe('AI 正在回复…')
    expect(phaseLabel(state({ phase: 'stopping' }))).toBe('正在停止…')
  })

  it('停止中仍然算忙碌，但停止按钮不可再点', () => {
    expect(isBusy('stopping')).toBe(true)
    expect(canStop('stopping')).toBe(false)
    // 点了没反应的按钮会让用户以为卡住。
    expect(canStop('sending')).toBe(true)
    expect(canStop('streaming')).toBe(true)
  })

  it('中断时不再算忙碌：用户可以继续输入', () => {
    expect(isBusy('interrupted')).toBe(false)
    expect(phaseLabel(state({ phase: 'interrupted' }))).toBeNull()
  })
})

describe('重试入口', () => {
  const failure = { code: 'PROVIDER_RATE_LIMIT', message: '请求过于频繁。', retryable: true, partial: false }

  it('只有中断 + 可重试 + 有原文时才给重试', () => {
    expect(canRetry(state({ phase: 'interrupted', failure, retryText: '你好' }))).toBe(true)
  })

  it('不可重试时不给重试（配置类错误重发不会成功）', () => {
    const configError = { ...failure, code: 'PROVIDER_NOT_CONFIGURED', retryable: false }
    expect(
      canRetry(state({ phase: 'interrupted', failure: configError, retryText: '你好' })),
    ).toBe(false)
  })

  it('没有待重发的原文时不给重试', () => {
    expect(canRetry(state({ phase: 'interrupted', failure, retryText: null }))).toBe(false)
  })

  it('非中断状态下即使有 failure 也不给重试', () => {
    expect(canRetry(state({ phase: 'idle', failure, retryText: '你好' }))).toBe(false)
  })
})

describe('失败文案', () => {
  it('有部分回复时说明"可能不完整"', () => {
    const text = failureText({
      code: 'STREAM_INTERRUPTED',
      message: '连接中断。',
      retryable: true,
      partial: true,
    })

    expect(text).toContain('连接中断。')
    expect(text).toContain('可能不完整')
  })

  it('完全没有回复时不说"不完整"（那会让人以为收到了东西）', () => {
    const text = failureText({
      code: 'NETWORK_ERROR',
      message: '无法连接到本地后端服务。',
      retryable: true,
      partial: false,
    })

    expect(text).toBe('无法连接到本地后端服务。')
    expect(text).not.toContain('不完整')
  })
})
