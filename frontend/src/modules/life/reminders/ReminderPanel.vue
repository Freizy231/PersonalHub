<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import HubButton from '../../../shared/HubButton.vue'
import { api } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'
import { browserZone, downloadJSON, localInput, parseLocalInput, type Page } from '../events/types'

interface Reminder { id: string; resource_id: string; title: string; remind_at: string; status: string; version: number; acknowledged_at: string | null }
const props = withDefaults(defineProps<{ enabled: boolean; resource?: { id: string; title: string; version: number }; embedded?: boolean }>(), { embedded: false })

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const page = ref<Page<Reminder>>({ items: [], total: 0, offset: 0, limit: 20 })
const history = ref(false)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const when = ref(localInput(new Date(Date.now() + 600000).toISOString()))

const statusLabels: Record<string, string> = { pending: '待提醒', delivered: '已到期', missed: '已错过', cancelled: '已取消' }
const dirty = computed(() => !!props.resource && writes.hasPending())
useUnsaved(() => dirty.value)

function message(reason: unknown) { return reason instanceof Error ? reason.message : '操作未完成，请重试。' }

async function load() {
  if (!props.enabled) return
  loading.value = true
  try {
    const scope = props.resource || history.value ? 'history' : 'inbox'
    const query = new URLSearchParams({ scope, limit: '20', offset: String(offset.value) })
    if (props.resource) query.set('resource_id', props.resource.id)
    const result = await latest.track(() => api<Page<Reminder>>(`/reminders?${query}`))
    page.value = result; error.value = ''
    if (!result.items.length && offset.value > 0) { offset.value = Math.max(0, offset.value - 20); await load() }
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { loading.value = false }
}

watch(() => [props.enabled, props.resource?.id, history.value], () => { offset.value = 0; void load() })
let pollTimer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  void load()
  // An embedded panel inside a dialog polls slower than the standalone inbox.
  const interval = props.embedded ? 30000 : 15000
  pollTimer = setInterval(() => { if (latest.active && !writes.busy.value) void load() }, interval)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })

async function schedule() {
  if (!props.resource) return
  try {
    await writes.send<Reminder>('reminder', '/reminders', {
      resource_id: props.resource.id,
      expected_resource_version: props.resource.version,
      remind_at: parseLocalInput(when.value),
    })
    toast.push('提醒已设置')
    await load()
  } catch (reason) { toast.error(message(reason)) }
}

async function change(row: Reminder, action: 'ack' | 'cancel') {
  try {
    await api(`/reminders/${row.id}/${action}`, { method: 'POST', body: JSON.stringify({ expected_version: row.version }) })
    await load()
  } catch (reason) { toast.error(message(reason)) }
}

async function exportData() {
  try { downloadJSON(await api<object>('/reminders/export'), 'personal-hub-reminders.json') }
  catch (reason) { toast.error(message(reason)) }
}

function turn(direction: number) { offset.value = Math.max(0, offset.value + direction * 20); void load() }
</script>

<template>
  <section class="panel" :class="{ 'is-embedded': embedded }">
    <div v-if="!embedded" class="panel-toolbar">
      <h2>{{ resource ? '提醒' : '提醒收件箱' }}</h2>
      <div class="panel-actions">
        <HubButton v-if="!resource" variant="quiet" size="sm" @click="history = !history">{{ history ? '未读' : '历史' }}</HubButton>
        <HubButton v-if="!resource" variant="quiet" size="sm" @click="exportData">导出</HubButton>
      </div>
    </div>

    <p v-if="!resource" class="fine-print">
      服务运行时处理；停机后 24 小时内补入，更早的标为已错过。仅站内提醒。
    </p>

    <form v-if="resource && enabled" class="inline-form" @submit.prevent="schedule">
      <label class="form-field grow"><span>提醒时间 · {{ browserZone() }}</span>
        <input v-model="when" type="datetime-local" required :disabled="writes.busy.value" />
      </label>
      <HubButton variant="primary" type="submit" :loading="writes.busy.value">添加提醒</HubButton>
    </form>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading && !page.items.length" class="muted fine-print" role="status">读取中…</p>
    <p v-else-if="!page.items.length" class="empty-state">{{ resource || history ? '暂无提醒记录。' : '没有未读提醒。' }}</p>

    <ul v-else class="task-list">
      <li v-for="row in page.items" :key="row.id">
        <div class="task-main">
          <strong class="task-title">{{ row.title }}</strong>
          <div class="task-meta">
            <span>{{ new Date(row.remind_at).toLocaleString('zh-CN') }}</span>
            <span>{{ statusLabels[row.status] || row.status }}</span>
            <span v-if="row.acknowledged_at">已读</span>
          </div>
        </div>
        <div class="task-actions">
          <button v-if="row.status === 'pending'" class="plain-action" @click="change(row, 'cancel')">取消</button>
          <button v-else-if="['delivered', 'missed'].includes(row.status) && !row.acknowledged_at" class="plain-action" @click="change(row, 'ack')">标为已读</button>
        </div>
      </li>
    </ul>

    <div v-if="page.total > 20" class="pager">
      <HubButton variant="quiet" size="sm" :disabled="!offset || writes.busy.value" @click="turn(-1)">上一页</HubButton>
      <span class="muted">{{ Math.floor(offset / 20) + 1 }} / {{ Math.ceil(page.total / 20) }}</span>
      <HubButton variant="quiet" size="sm" :disabled="offset + 20 >= page.total || writes.busy.value" @click="turn(1)">下一页</HubButton>
    </div>
  </section>
</template>
