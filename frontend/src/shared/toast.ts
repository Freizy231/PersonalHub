import { computed, ref } from 'vue'

export type Toast = { id: number; message: string; undo?: () => Promise<void> | void; tone: 'info' | 'error' }

let nextId = 1
const items = ref<Toast[]>([])

/**
 * Non-blocking replacement for window.confirm on reversible actions.
 * A reversible action runs immediately and offers an undo for a few seconds.
 */
export function useToast() {
  function push(message: string, undo?: Toast['undo']) {
    const id = nextId++
    items.value = [...items.value.filter(t => t.id !== id), { id, message, undo, tone: undo ? 'info' : 'info' }]
    if (typeof window !== 'undefined') {
      window.setTimeout(() => dismiss(id), undo ? 8000 : 5000)
    }
    return id
  }
  function error(message: string) {
    const id = nextId++
    items.value = [...items.value, { id, message, tone: 'error' }]
    if (typeof window !== 'undefined') window.setTimeout(() => dismiss(id), 8000)
    return id
  }
  function dismiss(id: number) {
    items.value = items.value.filter(t => t.id !== id)
  }
  async function undo(toast: Toast) {
    if (!toast.undo) return
    await toast.undo()
    dismiss(toast.id)
  }
  return { items: computed(() => items.value), push, error, dismiss, undo }
}

export function clearToasts() { items.value = [] }
