import { ref } from 'vue'

/**
 * Promise-based replacement for window.confirm. The dialog is rendered once by
 * <ConfirmHost/> in the app shell and names the concrete consequence, so the
 * person is not asked a generic "are you sure?".
 *
 * When no host is mounted (unit tests, embedded use) it falls back to the
 * browser dialog, so callers never have to care.
 */
export interface ConfirmRequest {
  id: number
  title: string
  body: string
  confirmLabel: string
  cancelLabel: string
  danger: boolean
  resolve: (value: boolean) => void
}

export interface ConfirmOptions {
  title?: string
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
}

let nextId = 1
export const pendingConfirm = ref<ConfirmRequest | null>(null)
let hostMounted = 0

export function registerConfirmHost() { hostMounted++; return () => { hostMounted = Math.max(0, hostMounted - 1) } }

export function confirmAction(body: string, options: ConfirmOptions = {}): Promise<boolean> {
  if (!hostMounted) return Promise.resolve(typeof window !== 'undefined' && typeof window.confirm === 'function' ? window.confirm(body) : true)
  // A second request while one is open cancels the first rather than stacking dialogs.
  pendingConfirm.value?.resolve(false)
  return new Promise<boolean>(resolve => {
    pendingConfirm.value = {
      id: nextId++,
      title: options.title ?? '请确认',
      body,
      confirmLabel: options.confirmLabel ?? '确认',
      cancelLabel: options.cancelLabel ?? '取消',
      danger: options.danger ?? false,
      resolve: (value: boolean) => { pendingConfirm.value = null; resolve(value) },
    }
  })
}

/** Standard wording for "discard my unsaved draft". */
export function confirmDiscard(what = '未保存的修改') {
  return confirmAction(`${what}将被丢弃，且无法恢复。`, { title: '放弃修改？', confirmLabel: '放弃修改', cancelLabel: '继续编辑', danger: true })
}

export function useConfirm() { return { confirm: confirmAction, discard: confirmDiscard } }
