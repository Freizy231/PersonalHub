<script setup lang="ts">
import { computed } from 'vue'
import { useToast, type Toast } from './toast'

const { items, dismiss, undo } = useToast()
const list = computed(() => items.value)

async function run(toast: Toast) {
  try { await undo(toast) } catch { /* The toast disappears; the row is re-read on next load. */ }
}
</script>

<template>
  <div class="toast-stack" role="status" aria-live="polite">
    <div v-for="toast in list" :key="toast.id" class="toast" :class="`is-${toast.tone}`">
      <span class="toast-text">{{ toast.message }}</span>
      <button v-if="toast.undo" type="button" class="toast-undo" @click="run(toast)">撤销</button>
      <button type="button" class="toast-close" aria-label="关闭提示" @click="dismiss(toast.id)">
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
      </button>
    </div>
  </div>
</template>

<style scoped>
.toast-stack {
  position: fixed;
  left: 50%;
  bottom: var(--space-5);
  z-index: 80;
  display: flex;
  flex-direction: column;
  gap: var(--space-2);
  transform: translateX(-50%);
  width: min(460px, calc(100vw - 32px));
  pointer-events: none;
}
.toast {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 10px var(--space-3) 10px var(--space-4);
  background: var(--text);
  color: var(--bg);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow);
  font-size: var(--step-0);
  pointer-events: auto;
  animation: toast-in .16s ease-out;
}
.toast.is-error { background: var(--danger); color: var(--on-danger); }
.toast-text { flex: 1; min-width: 0; }
.toast-undo {
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  font-weight: 600;
  text-decoration: underline;
  text-underline-offset: 2px;
  cursor: pointer;
  padding: 0;
}
.toast-close {
  display: grid;
  place-items: center;
  width: 22px;
  height: 22px;
  border: 0;
  border-radius: 4px;
  background: none;
  color: inherit;
  opacity: .6;
  cursor: pointer;
}
.toast-close:hover { opacity: 1; }
@keyframes toast-in { from { opacity: 0; transform: translateY(6px); } }
@media (prefers-reduced-motion: reduce) { .toast { animation: none; } }
</style>
