import { act, renderHook } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { useConversationDetail } from './useConversationDetail'

vi.mock('../services/api', () => ({
  api: {
    conversations: {
      get: vi.fn(),
      messages: { list: vi.fn(), listBefore: vi.fn() },
    },
    approvals: { decide: vi.fn() },
  },
}))

vi.mock('../services/stream', () => ({
  streamChat: vi.fn(),
}))

vi.mock('../services/session', () => ({
  getSessionToken: () => 'test-token',
}))

const { api } = await import('../services/api')
const { streamChat } = await import('../services/stream')

const CONVERSATION = {
  id: 'c1',
  title: '测试会话',
  created_at: '2026-11-25T00:00:00Z',
  updated_at: '2026-11-25T00:00:00Z',
}

function emptyList() {
  return { ok: true as const, data: { items: [], total: 0, limit: 50, offset: 0 } }
}

beforeEach(() => {
  vi.mocked(api.conversations.get).mockReset()
  vi.mocked(api.conversations.messages.list).mockReset()
  vi.mocked(streamChat).mockReset()
  vi.mocked(api.conversations.get).mockResolvedValue({ ok: true, data: CONVERSATION })
  vi.mocked(api.conversations.messages.list).mockResolvedValue(emptyList())
})

async function renderReady() {
  const hook = renderHook(() => useConversationDetail('c1'))
  // 等首屏加载完成（loading 变 false）。
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
  return hook
}

describe('useConversationDetail.send：原文何时该还给用户', () => {
  it('服务端在落库前拒绝（未配置密钥）时返回 false，原文需还回输入框', async () => {
    const { result } = await renderReady()

    // 非 200：stream.ts 会调用 onError，但不会调用 onAccepted。
    vi.mocked(streamChat).mockImplementation(async (_id, _text, handlers) => {
      handlers.onError({
        code: 'PROVIDER_NOT_CONFIGURED',
        message: '未配置 DEEPSEEK_API_KEY。',
        retryable: false,
      })
    })

    let accepted: boolean | undefined
    await act(async () => {
      accepted = await result.current.send('这条不该丢')
    })

    // 这是本测试的核心：消息没有入库，调用方（输入框）必须把原文还回去。
    expect(accepted).toBe(false)
    expect(result.current.sendState.phase).toBe('interrupted')
    expect(result.current.sendState.failure?.code).toBe('PROVIDER_NOT_CONFIGURED')
    // 不可重试时不提供重试入口。
    expect(result.current.sendState.retryText).toBeNull()
  })

  it('流中途出错（消息已入库）时返回 true，不把原文还回去造成重复发送', async () => {
    const { result } = await renderReady()

    vi.mocked(streamChat).mockImplementation(async (_id, _text, handlers) => {
      handlers.onAccepted?.()
      handlers.onDelta('已经回复了一半')
      handlers.onError({
        code: 'PROVIDER_UNREACHABLE',
        message: '无法连接到模型服务。',
        retryable: true,
      })
    })

    let accepted: boolean | undefined
    await act(async () => {
      accepted = await result.current.send('这条已经入库')
    })

    expect(accepted).toBe(true)
    expect(result.current.sendState.phase).toBe('interrupted')
    // 消息已在服务端：重试是"再发一次"而不是"补发"，因此允许给重试入口。
    expect(result.current.sendState.retryText).toBe('这条已经入库')
    expect(result.current.sendState.failure?.partial).toBe(true)
  })
})
