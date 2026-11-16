<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'

interface StalledRow {
  id: string; kind: string; kind_label: string; title: string
  days: number; since: string; basis: string; detail: string; destination: string
}

const props = withDefaults(defineProps<{ enabled: boolean; limit?: number }>(), { limit: 6 })
const rows = ref<StalledRow[]>([])
const total = ref(0)
const notice = ref('')
const loading = ref(false)
const error = ref('')

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const result = await api<{ items: StalledRow[]; total: number; notice: string }>('/stalled?limit=50')
    rows.value = result.items
    total.value = result.total
    notice.value = result.notice
  } catch (reason) { error.value = errorMessage(reason) }
  finally { loading.value = false }
}

/** "23 天" is easier to judge than a timestamp; the exact basis stays in the title. */
function waited(days: number) {
  if (days < 30) return `${days} 天`
  const months = Math.floor(days / 30)
  return months < 12 ? `${months} 个月` : `${Math.floor(months / 12)} 年`
}

const shown = computed(() => rows.value.slice(0, props.limit))
const hidden = computed(() => Math.max(0, total.value - shown.value.length))

onMounted(load)
</script>

<template>
  <section class="stalled-widget">
    <div class="task-heading">
      <h2>搁置的事</h2>
      <span v-if="total" class="muted fine-print">{{ total }} 项</span>
    </div>

    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}</p>
    <p v-else-if="loading && !rows.length" class="fine-print">正在核对…</p>
    <p v-else-if="!rows.length" class="fine-print">
      没有长期没动的事。这里只统计超过各自期限仍未推进的任务、学习节点和项目。
    </p>

    <ul v-else class="plain-list stalled-list">
      <li v-for="row in shown" :key="row.id">
        <router-link :to="row.destination" class="text-link stalled-title">{{ row.title }}</router-link>
        <span class="stalled-age" :title="`${row.basis} · ${row.since.slice(0, 10)}`">{{ waited(row.days) }}</span>
        <span class="muted fine-print stalled-detail">{{ row.kind_label }} · {{ row.detail }}</span>
      </li>
    </ul>

    <p v-if="hidden" class="fine-print muted">还有 {{ hidden }} 项未显示。</p>
    <p v-if="rows.length" class="fine-print stalled-note">{{ notice }}</p>
  </section>
</template>

<style scoped>
.stalled-list > li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px var(--space-3); align-items: baseline; padding: var(--space-2) 0; }
.stalled-title { overflow-wrap: anywhere; }
.stalled-age { color: var(--warning); font-size: var(--step--1); font-variant-numeric: tabular-nums; white-space: nowrap; }
.stalled-detail { grid-column: 1 / -1; }
.stalled-note { margin-top: var(--space-2); color: var(--muted); }
</style>
