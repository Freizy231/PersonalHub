<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import HubButton from './HubButton.vue'
import { dialogStack, enterDialog } from './layer'

const props = withDefaults(defineProps<{
  show: boolean
  title: string
  busy?: boolean
  confirmLabel?: string
  cancelLabel?: string
  danger?: boolean
  wide?: boolean
}>(), { busy: false, confirmLabel: '', cancelLabel: '关闭', danger: false, wide: false })

const emit = defineEmits<{
  'update:show': [value: boolean]
  confirm: []
  cancel: []
  /** Ctrl/Cmd+S or Ctrl/Cmd+Enter inside the dialog. */
  submit: []
}>()

const panel = ref<HTMLElement>()
let previousFocus: HTMLElement | null = null
let layer: ReturnType<typeof enterDialog> | null = null
// How many dialogs were already open when this one appeared. A confirmation raised
// from inside a form must sit above that form, not tie with it.
const stackDepth = ref(0)

watch(dialogStack, () => { if (layer) stackDepth.value = layer.depth() })

function close() {
  if (props.busy) return
  emit('update:show', false)
  emit('cancel')
}

function onKeydown(event: KeyboardEvent) {
  if (!props.show) return
  if (event.key === 'Escape') {
    event.preventDefault()
    close()
    return
  }
  if ((event.ctrlKey || event.metaKey) && !event.isComposing && (event.key.toLowerCase() === 's' || event.key === 'Enter')) {
    // Never let the browser's "save page" dialog open on top of an editor.
    event.preventDefault()
    if (!props.busy) emit('submit')
    return
  }
  if (event.key !== 'Tab' || !panel.value) return
  const focusable = panel.value.querySelectorAll<HTMLElement>(
    'button:not(:disabled), input:not(:disabled), select:not(:disabled), textarea:not(:disabled), a[href], [tabindex]:not([tabindex="-1"])',
  )
  if (!focusable.length) return
  const first = focusable[0]!
  const last = focusable[focusable.length - 1]!
  if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && document.activeElement === first) {
    event.preventDefault()
    last.focus()
  }
}

watch(() => props.show, async (open) => {
  if (open) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    layer = enterDialog()
    stackDepth.value = layer.depth()
    await nextTick()
    const target = panel.value?.querySelector<HTMLElement>('[autofocus], input, textarea, select') || panel.value
    target?.focus()
  } else {
    layer?.leave()
    layer = null
    stackDepth.value = 0
    previousFocus?.focus()
    previousFocus = null
  }
  // A dialog mounted already open must register too: otherwise it never joins the
  // stack, never freezes the page and never takes focus.
}, { immediate: true })

onBeforeUnmount(() => {
  layer?.leave()
  layer = null
})
</script>

<template>
  <Teleport to="body">
    <div
      v-if="show"
      class="hub-overlay"
      :style="{ zIndex: 60 + stackDepth }"
      @mousedown.self="close"
      @keydown="onKeydown"
    >
      <div
        ref="panel"
        class="hub-dialog"
        :class="{ 'is-wide': wide }"
        role="dialog"
        aria-modal="true"
        :aria-label="title"
        tabindex="-1"
      >
        <header class="hub-dialog-head">
          <h2>{{ title }}</h2>
          <button type="button" class="hub-dialog-close" :disabled="busy" aria-label="关闭" @click="close">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
          </button>
        </header>
        <div class="hub-dialog-body"><slot /></div>
        <footer v-if="$slots.actions || confirmLabel" class="hub-dialog-actions">
          <slot name="actions">
            <HubButton variant="quiet" :disabled="busy" @click="close">{{ cancelLabel }}</HubButton>
            <HubButton :variant="danger ? 'danger' : 'primary'" :loading="busy" @click="emit('confirm')">{{ confirmLabel }}</HubButton>
          </slot>
        </footer>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.hub-overlay {
  position: fixed;
  inset: 0;
  /* Stack position is inline (open order); this is the base of the dialog band. */
  z-index: 60;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 6vh var(--space-4) var(--space-4);
  animation: dialog-fade .12s ease-out;
  background: var(--overlay);
  overflow: hidden;
}
.hub-dialog {
  width: min(640px, 100%);
  max-height: 88vh;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  outline: none;
}
.hub-dialog.is-wide { width: min(1040px, 100%); }
.hub-dialog:focus-visible { outline: 2px solid var(--accent); }
.hub-dialog-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-4) var(--space-5);
  border-bottom: 1px solid var(--border);
}
.hub-dialog-head h2 { margin: 0; font-size: var(--step-2); }
.hub-dialog-close {
  display: grid;
  place-items: center;
  width: 28px;
  height: 28px;
  border: 0;
  border-radius: var(--radius-sm);
  background: none;
  color: var(--muted);
  cursor: pointer;
}
.hub-dialog-close:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
.hub-dialog-body {
  padding: var(--space-5);
  overflow-y: auto;
  /* A flex item's default min-height is its content, which would push the action row
     below the dialog's max-height and make 取消 / 保存 unreachable. */
  min-height: 0;
  flex: 1 1 auto;
}
.hub-dialog-head { flex: 0 0 auto; }
.hub-dialog-actions {
  flex: 0 0 auto;
  display: flex;
  justify-content: flex-end;
  gap: var(--space-2);
  padding: var(--space-3) var(--space-5);
  border-top: 1px solid var(--border);
  background: var(--surface);
}
@keyframes dialog-fade { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .hub-overlay { animation: none; } }
@media (max-height: 720px) {
  /* Short windows: the dialog gets the whole viewport height so the actions stay visible. */
  .hub-overlay { padding: var(--space-2) var(--space-4); }
  .hub-dialog { max-height: calc(100vh - var(--space-4)); }
}
@media (max-width: 640px) {
  .hub-overlay { padding: var(--space-2); align-items: flex-end; }
  .hub-dialog { max-height: 92vh; }
  .hub-dialog-body, .hub-dialog-head { padding: var(--space-4); }
}
</style>
