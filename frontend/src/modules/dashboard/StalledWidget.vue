<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { useToast } from '../../shared/toast'

interface StalledRow {
  id: string; kind: string; kind_label: string; title: string; version: number
  days: number; since: string; basis: string; detail: string; destination: string
  last_decision?: string; last_reviewed?: string; note?: string
}

type Decision = 'done' | 'continue' | 'snooze' | 'drop'

const props = withDefaults(defineProps<{ enabled: boolean; limit?: number }>(), { limit: 6 })
const toast = useToast()
const rows = ref<StalledRow[]>([])
const total = ref(0)
const notice = ref('')
const loading = ref(false)
const error = ref('')
const working = ref('')          // resource id currently being decided
const confirming = ref('')       // resource id with an open "drop" confirmation

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

/**
 * What each kind can honestly be asked to do here. Learning nodes become "verified" only
 * through an assessment that keeps its evidence, so this widget never offers that shortcut.
 */
function actionsFor(kind: string): { decision: Decision; label: string; hint: string }[] {
  if (kind === 'knowledge_node') {
    return [
      { decision: 'continue', label: '继续', hint: '定一个再看的日期，期间不再出现' },
      { decision: 'snooze', label: '以后再说', hint: '按下一次的正常节奏再来问' },
      { decision: 'drop', label: '不学了', hint: '移到回收站，可恢复' },
    ]
  }
  return [
    { decision: 'done', label: kind === 'project' ? '已完成' : '完成', hint: '记为完成，从清单里移除' },
    { decision: 'continue', label: '继续', hint: '重新安排到未来某天' },
    { decision: 'snooze', label: '以后再说', hint: '按下一次的正常节奏再来问' },
    { decision: 'drop', label: kind === 'project' ? '归档' : '放弃', hint: '不再提醒，记录保留' },
  ]
}

async function decide(row: StalledRow, decision: Decision, nextDate?: string) {
  // Dropping is the only decision that takes something away from the list for good.
  if (decision === 'drop' && confirming.value !== row.id) {
    confirming.value = row.id
    return
  }
  working.value = row.id; confirming.value = ''
  try {
    await api(`/reviews/${row.id}`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: row.version, decision, next_date: nextDate ?? null }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    rows.value = rows.value.filter(item => item.id !== row.id)
    total.value = Math.max(0, total.value - 1)
    toast.push(decided(decision, row.title))
  } catch (reason) {
    toast.error(errorMessage(reason))
    if (reason instanceof Error && /版本|VERSION/.test(reason.message)) await load()
  } finally { working.value = '' }
}

function decided(decision: Decision, title: string) {
  const short = title.length > 12 ? title.slice(0, 12) + '…' : title
  return { done: `「${short}」已完成`, snooze: `「${short}」以后再说`, drop: `「${short}」已移出清单`,
           continue: `「${short}」已重新安排` }[decision]
}

function cancelDrop() { confirming.value = '' }

/** The report carries the last decision so the list can say what was already tried. */
function reviewed(decision?: string) {
  return ({ done: '标记完成', continue: '安排继续', snooze: '决定稍后', drop: '移出' } as Record<string, string>)[decision || ''] || '处理'
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
      <li v-for="row in shown" :key="row.id" :class="{ 'is-working': working === row.id }">
        <router-link :to="row.destination" class="text-link stalled-title">{{ row.title }}</router-link>
        <span class="stalled-age" :title="`${row.basis} · ${row.since.slice(0, 10)}`">{{ waited(row.days) }}</span>
        <span class="muted fine-print stalled-detail">
          {{ row.kind_label }} · {{ row.detail }}
          <template v-if="row.last_reviewed"> · 上次{{ reviewed(row.last_decision) }}于 {{ row.last_reviewed }}</template>
        </span>

        <div v-if="confirming === row.id" class="stalled-actions stalled-confirm">
          <span class="fine-print">确定？这条会从清单里去掉。</span>
          <button class="plain-action" :disabled="!!working" @click="decide(row, 'drop', undefined)">确定</button>
          <button class="plain-action" :disabled="!!working" @click="cancelDrop">算了</button>
        </div>
        <div v-else class="stalled-actions">
          <button
            v-for="action in actionsFor(row.kind)"
            :key="action.decision"
            class="plain-action"
            :class="{ 'is-quiet': action.decision === 'drop' }"
            :disabled="!!working"
            :title="action.hint"
            @click="decide(row, action.decision)"
          >{{ action.label }}</button>
        </div>
      </li>
    </ul>

    <p v-if="hidden" class="fine-print muted">还有 {{ hidden }} 项未显示。</p>
    <p v-if="rows.length" class="fine-print stalled-note">{{ notice }}</p>
  </section>
</template>

<style scoped>
.stalled-list > li { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 2px var(--space-3); align-items: baseline; padding: var(--space-2) 0; }
.stalled-list > li.is-working { opacity: .55; }
.stalled-title { overflow-wrap: anywhere; }
.stalled-age { color: var(--warning); font-size: var(--step--1); font-variant-numeric: tabular-nums; white-space: nowrap; }
.stalled-detail { grid-column: 1 / -1; }
.stalled-actions { grid-column: 1 / -1; display: flex; flex-wrap: wrap; align-items: baseline; gap: var(--space-1) var(--space-3); }
.stalled-confirm { color: var(--warning); }
.stalled-actions .is-quiet { color: var(--muted); }
.stalled-note { margin-top: var(--space-2); color: var(--muted); }
</style>
