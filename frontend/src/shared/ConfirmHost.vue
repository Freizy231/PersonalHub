<script setup lang="ts">
import { onBeforeUnmount } from 'vue'
import HubDialog from './HubDialog.vue'
import { pendingConfirm, registerConfirmHost } from './confirm'

const unregister = registerConfirmHost()
onBeforeUnmount(() => { unregister(); pendingConfirm.value?.resolve(false) })

function answer(value: boolean) { pendingConfirm.value?.resolve(value) }
</script>

<template>
  <HubDialog
    :show="!!pendingConfirm"
    :title="pendingConfirm?.title || ''"
    :confirm-label="pendingConfirm?.confirmLabel || '确认'"
    :cancel-label="pendingConfirm?.cancelLabel || '取消'"
    :danger="pendingConfirm?.danger"
    @confirm="answer(true)"
    @cancel="answer(false)"
    @update:show="v => { if (!v) answer(false) }"
  >
    <p class="confirm-body">{{ pendingConfirm?.body }}</p>
  </HubDialog>
</template>

<style scoped>
.confirm-body { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.7; }
</style>
