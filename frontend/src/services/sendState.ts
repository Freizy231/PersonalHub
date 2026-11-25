/**
 * 对话发送状态机（迭代 2 第 1 项）。
 *
 * 为什么单独成模块：状态的含义、文案与"哪些按钮可用"必须在一处定死。
 * 此前这些散落在 hook 的布尔值和页面里，结果是：
 * - "停止中"与"发送中"长得一样，用户不知道自己按的停止是否生效；
 * - 出错后没有任何状态，只多出一行红字，输入框照旧——用户不知道能不能再发；
 * - 断线时已经收到的半段回复被直接丢弃，看起来像 AI 什么也没说。
 *
 * 状态转移（箭头上的条件是这条转移的**唯一**原因）：
 *
 *     idle ──send──▶ sending ──首个 delta / 工具事件──▶ streaming
 *       ▲              │                                   │
 *       │              │  出错或取消（且已收到部分文本）      │
 *       │              ▼                                   ▼
 *       └── reset ◀── interrupted ◀────────────────────────┘
 *                      │
 *                      └── stop 请求 ──▶ stopping ──▶ interrupted
 *
 * `interrupted` 是**唯一**允许"重试"的状态，且是否真的可重试由服务端的
 * `retryable` 决定（见 services/stream.ts）。不能重试时界面说清楚原因，
 * 而不是给一个点了必然失败的按钮。
 */

export type SendPhase = 'idle' | 'sending' | 'streaming' | 'stopping' | 'interrupted'

/** 一次失败/中断的说明。`retryable` 由服务端给出，缺省按不可重试处理。 */
export interface SendFailure {
  code: string
  message: string
  retryable: boolean
  /** 是否已经收到了一部分回复（决定措辞："可能不完整" vs "没有收到回复"）。 */
  partial: boolean
}

export interface SendState {
  phase: SendPhase
  failure: SendFailure | null
  /** 可以"原样重发"的文本。只在 interrupted 且 retryable 时有值。 */
  retryText: string | null
}

export const INITIAL_SEND_STATE: SendState = {
  phase: 'idle',
  failure: null,
  retryText: null,
}

/** 该状态下面板显示什么。文案在这里统一，避免页面各写一份。 */
export function phaseLabel(state: SendState): string | null {
  switch (state.phase) {
    case 'idle':
      return null
    case 'sending':
      return '正在发送…'
    case 'streaming':
      return 'AI 正在回复…'
    case 'stopping':
      return '正在停止…'
    case 'interrupted':
      return null
  }
}

/** 是否处于"正在生成"：此时发送按钮禁用、显示停止按钮。 */
export function isBusy(phase: SendPhase): boolean {
  return phase === 'sending' || phase === 'streaming' || phase === 'stopping'
}

/**
 * 停止按钮是否可点。
 *
 * `stopping` 时禁用：后端已经收到中断信号，再点一次没有意义，
 * 而一个"点了没反应"的按钮会让用户以为卡住了。
 */
export function canStop(phase: SendPhase): boolean {
  return phase === 'sending' || phase === 'streaming'
}

/** 中断/失败时的文案。区分"有部分回复"与"完全没有"。 */
export function failureText(failure: SendFailure): string {
  if (failure.partial) {
    return `${failure.message} 已收到的内容可能不完整，可以重试或继续对话。`
  }
  return failure.message
}

/** 失败是否值得给"重试"入口。 */
export function canRetry(state: SendState): boolean {
  return state.phase === 'interrupted' && Boolean(state.failure?.retryable) && Boolean(state.retryText)
}
