import { computed, onBeforeUnmount, watch } from 'vue'

/**
 * One place that owns the "unsaved draft" contract with the app shell:
 * an emit('draft', bool) to the parent and a beforeunload guard.
 */
export function useUnsaved(isDirty: () => boolean, emit?: (event: 'draft', value: boolean) => void) {
  const dirty = computed(isDirty)
  let emitted = false

  function unload(event: BeforeUnloadEvent) {
    if (!dirty.value) return
    event.preventDefault()
    event.returnValue = ''
  }

  if (typeof window !== 'undefined' && typeof window.addEventListener === 'function') {
    window.addEventListener('beforeunload', unload)
  }

  const stopWatch = watch(dirty, (value) => {
    if (emitted === value) return
    emitted = value
    emit?.('draft', value)
  }, { immediate: true })

  onBeforeUnmount(() => {
    stopWatch()
    if (typeof window !== 'undefined' && typeof window.removeEventListener === 'function') {
      window.removeEventListener('beforeunload', unload)
    }
    if (emitted) emit?.('draft', false)
    emitted = false
  })

  return { dirty }
}
