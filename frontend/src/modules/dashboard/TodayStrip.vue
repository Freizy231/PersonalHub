<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { isStale, useLatest } from '../../shared/requests'
import { localDate, type TaskPage } from '../life/tasks/types'
import { browserZone, shiftDate, type HubEvent, type Page } from '../life/events/types'

/**
 * One-line summary of today: open tasks, next event, overdue count.
 * Every figure comes from the same endpoints the lists below use, so it can
 * never disagree with them. A failed read shows nothing instead of a wrong 0.
 */
const props = defineProps<{ enabled: boolean }>()
const latest = useLatest()

const open = ref<number | null>(null)
const overdue = ref<number | null>(null)
const nextEvent = ref<HubEvent | null | undefined>(undefined)
const failed = ref(false)
let refreshTimer: ReturnType<typeof setInterval> | undefined

async function load() {
  if (!props.enabled) return
  try {
    const day = localDate()
    const [today, late, events] = await latest.track(() => Promise.all([
      api<TaskPage>(`/tasks?scope=today&on_date=${day}&limit=100`),
      api<TaskPage>(`/tasks?scope=overdue&on_date=${day}&limit=1`),
      api<Page<HubEvent>>(`/events?start_date=${day}&end_date=${shiftDate(day, 1)}&timezone=${encodeURIComponent(browserZone())}&limit=20`),
    ]))
    open.value = today.items.filter(t => t.status === 'todo' || t.status === 'doing').length
    overdue.value = late.total
    const now = Date.now()
    const upcoming = events.items
      .filter(e => e.status === 'scheduled' && (e.is_all_day || (e.end_at && new Date(e.end_at).getTime() > now)))
      .sort((a, b) => (a.is_all_day ? 0 : 1) - (b.is_all_day ? 0 : 1) || String(a.start_at).localeCompare(String(b.start_at)))
    nextEvent.value = upcoming[0] ?? null
    failed.value = false
  } catch (reason) {
    if (!isStale(reason)) failed.value = true
  }
}

const nextLabel = computed(() => {
  const e = nextEvent.value
  if (!e) return '今天没有日程'
  const time = e.is_all_day ? '全天' : new Date(e.start_at!).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit', hour12: false })
  return `下一个日程 ${time} ${e.title}`
})

onMounted(() => { void load(); refreshTimer = setInterval(load, 60000); window.addEventListener('focus', load) })
onBeforeUnmount(() => { if (refreshTimer) clearInterval(refreshTimer); window.removeEventListener('focus', load) })
</script>

<template>
  <p v-if="!failed && open !== null" class="today-strip" role="status">
    <span><strong>{{ open }}</strong> 项待办</span>
    <span class="today-strip-sep" aria-hidden="true">·</span>
    <span>{{ nextLabel }}</span>
    <template v-if="overdue">
      <span class="today-strip-sep" aria-hidden="true">·</span>
      <router-link to="/tasks" class="today-strip-late">已逾期 {{ overdue }} 项</router-link>
    </template>
  </p>
</template>

<style scoped>
.today-strip {
  display: flex;
  align-items: baseline;
  flex-wrap: wrap;
  gap: var(--space-2);
  margin: 0 0 var(--space-4);
  color: var(--text-soft);
  font-size: var(--step-1);
}
.today-strip strong { color: var(--text); font-size: var(--step-3); font-variant-numeric: tabular-nums; font-weight: 600; }
.today-strip-sep { color: var(--border-strong); }
.today-strip-late { color: var(--danger); font-weight: 500; }
.today-strip-late:hover { text-decoration: underline; }
</style>
