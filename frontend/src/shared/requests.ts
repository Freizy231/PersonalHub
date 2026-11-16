import { onBeforeUnmount, reactive, ref } from 'vue'
import { api, ApiError } from './api'

export class StaleResponse extends Error {
  constructor() { super('STALE_RESPONSE') }
}

export function isStale(reason: unknown) { return reason instanceof Error && reason.message === 'STALE_RESPONSE' }

/**
 * One request sequence per component. A late response from an older request is
 * rejected instead of overwriting newer state, so rapid tab switches and
 * pagination cannot render stale data.
 */
export function useLatest() {
  let sequence = 0
  let mounted = true
  onBeforeUnmount(() => { mounted = false; sequence++ })

  async function track<T>(run: () => Promise<T>): Promise<T> {
    const token = ++sequence
    const value = await run()
    if (!mounted || token !== sequence) throw new StaleResponse()
    return value
  }

  function invalidate() { sequence++ }

  return {
    track,
    invalidate,
    get active() { return mounted },
  }
}

type Pending = { key: string; body: string }

/**
 * Idempotent write helper. A failed or interrupted attempt keeps its original
 * idempotency key and body, so retrying can never create a duplicate. Only a
 * definite client rejection (4xx) discards the reservation.
 */
export function useIdempotent() {
  const pending = reactive<Record<string, Pending>>({})
  const busy = ref(false)
  const uncertain = ref(false)

  async function send<T>(slot: string, path: string, body: unknown, method: 'POST' | 'PUT' | 'PATCH' = 'POST'): Promise<T> {
    const serialized = JSON.stringify(body)
    // A slot names the kind of action; the path names its target. Keying by both means an unconfirmed
    // write to one row cannot block, or be mistaken for, the same action on another row.
    const id = slot + ' ' + method + ' ' + path
    const previous = pending[id]
    if (previous && previous.body !== serialized) {
      throw new Error('上次请求结果尚未确认。请先用原内容重试，或放弃草稿并重新读取核对。')
    }
    if (!previous) pending[id] = { key: crypto.randomUUID(), body: serialized }
    const request = pending[id]!
    busy.value = true
    try {
      const result = await api<T>(path, { method, body: request.body, headers: { 'Idempotency-Key': request.key } })
      delete pending[id]
      uncertain.value = false
      return result
    } catch (reason) {
      if (reason instanceof ApiError && reason.status < 500) {
        delete pending[id]
        uncertain.value = false
      } else if (!(reason instanceof ApiError)) {
        uncertain.value = true
      }
      throw reason
    } finally {
      busy.value = false
    }
  }

  function discard() {
    for (const key of Object.keys(pending)) delete pending[key]
    uncertain.value = false
  }

  return { send, discard, busy, uncertain, hasPending: () => Object.keys(pending).length > 0 }
}
