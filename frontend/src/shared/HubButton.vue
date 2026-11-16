<script setup lang="ts">
withDefaults(defineProps<{
  variant?: 'primary' | 'secondary' | 'quiet' | 'danger'
  type?: 'button' | 'submit'
  disabled?: boolean
  loading?: boolean
  size?: 'sm' | 'md'
}>(), { variant: 'secondary', type: 'button', disabled: false, loading: false, size: 'md' })
</script>

<template>
  <button
    :type="type"
    class="hub-button"
    :class="[`is-${variant}`, `is-${size}`, { 'is-loading': loading }]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <span v-if="loading" class="hub-button-spinner" aria-hidden="true" />
    <slot />
  </button>
</template>

<style scoped>
.hub-button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 6px;
  border: 1px solid transparent;
  border-radius: var(--radius-sm);
  font: inherit;
  font-size: var(--step-0);
  font-weight: 500;
  line-height: 1.3;
  cursor: pointer;
  white-space: nowrap;
  transition: background .12s ease, border-color .12s ease, color .12s ease;
}
.hub-button.is-md { padding: 8px 14px; }
.hub-button.is-sm { padding: 5px 10px; font-size: var(--step--1); }
.hub-button.is-primary { background: var(--accent); color: var(--bg); }
.hub-button.is-primary:hover:not(:disabled) { background: var(--accent-hover); }
.hub-button.is-secondary { background: var(--surface); border-color: var(--border-strong); color: var(--text); }
.hub-button.is-secondary:hover:not(:disabled) { border-color: var(--accent); color: var(--accent); }
.hub-button.is-quiet { background: none; color: var(--text-soft); padding-left: 6px; padding-right: 6px; }
.hub-button.is-quiet:hover:not(:disabled) { background: var(--surface-2); color: var(--text); }
.hub-button.is-danger { background: none; border-color: var(--border-strong); color: var(--danger); }
.hub-button.is-danger:hover:not(:disabled) { border-color: var(--danger); background: color-mix(in srgb, var(--danger) 8%, transparent); }
.hub-button:disabled { opacity: .5; cursor: default; }
.hub-button-spinner {
  width: 11px;
  height: 11px;
  border: 2px solid currentColor;
  border-right-color: transparent;
  border-radius: 50%;
  animation: hub-spin .7s linear infinite;
}
@keyframes hub-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) {
  .hub-button-spinner { animation-duration: 2s; }
}
</style>
