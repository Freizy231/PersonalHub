import { useCallback, useEffect, useRef, useState } from 'react'

import { api } from '../services/api'
import type { HealthResponse, Result } from '../types/api'

export type HealthState =
  | { status: 'checking' }
  | { status: 'online'; health: HealthResponse }
  | { status: 'offline'; message: string; code: string; requestId?: string }

/** 把 API 结果转换为展示状态。纯函数，便于单独推理与测试。 */
function toHealthState(result: Result<HealthResponse>): HealthState {
  if (result.ok) {
    return { status: 'online', health: result.data }
  }
  return {
    status: 'offline',
    message: result.message,
    code: result.code,
    ...(result.requestId ? { requestId: result.requestId } : {}),
  }
}

/**
 * 查询后端健康状态。
 *
 * 要点：
 * - 初次挂载时状态本来已是 checking，不再重复 setState，避免多余渲染。
 * - 手动刷新时才切回 checking，让用户看到反馈。
 * - 组件卸载后不得 setState（mounted ref）。
 * - 连续刷新时只采纳最后一次结果（generation 序号），避免旧响应覆盖新结果。
 * - 不自动轮询：M1 只需要用户可触发的状态检查。
 */
export function useHealth() {
  const [state, setState] = useState<HealthState>({ status: 'checking' })
  const mounted = useRef(true)
  const generation = useRef(0)

  const request = useCallback((showChecking: boolean) => {
    const current = ++generation.current
    if (showChecking) setState({ status: 'checking' })

    void api.health().then((result) => {
      if (!mounted.current || current !== generation.current) return
      setState(toHealthState(result))
    })
  }, [])

  useEffect(() => {
    mounted.current = true
    // 这里正是"与外部系统同步"：向本地后端发起请求，结果到达后才 setState。
    // 请求为异步，不会在 effect 内同步触发级联渲染；该规则无法区分同步/异步回调，
    // 因此在此处精确豁免，而不是改写为更绕的结构。
    // oxlint-disable-next-line react/set-state-in-effect
    request(false)
    return () => {
      mounted.current = false
    }
  }, [request])

  const recheck = useCallback(() => request(true), [request])

  return { state, recheck }
}
