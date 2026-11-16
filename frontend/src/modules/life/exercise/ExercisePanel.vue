<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import ResourcePicker from '../../../shared/ResourcePicker.vue'
import { api } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'

defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Plan { id: string; local_date: string; title: string; kind: string; duration_seconds: number; distance_meters: number; cancelled: number; task?: { id: string; title: string }; event?: { id: string; title: string }; memo?: string; version: number }
interface Record_ { id: string; local_date: string; kind: string; duration_seconds: number; distance_meters: number; plan_id?: string; memo?: string; version: number }
interface Stats { kind: string; sessions: number; duration_seconds: number; distance_meters: number }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const month = ref(new Date().toLocaleDateString('sv-SE').slice(0, 7))
const trash = ref(false)
const loading = ref(false)
const error = ref('')
const data = ref<{ month: string; plans: Plan[]; records: Record_[]; statistics: Stats[] }>({ month: '', plans: [], records: [], statistics: [] })

const kinds = [
  { value: 'run', label: '跑步' }, { value: 'walk', label: '步行' }, { value: 'strength', label: '力量' },
  { value: 'cycle', label: '骑行' }, { value: 'other', label: '其他' },
]
function kindLabel(value: string) { return kinds.find(k => k.value === value)?.label || value }
function duration(seconds: number) { return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分钟${seconds % 60 ? ` ${seconds % 60} 秒` : ''}` }
function distance(meters: number) { return meters >= 1000 ? `${Number((meters / 1000).toFixed(3))} 公里` : `${meters} 米` }

async function load() {
  loading.value = true; error.value = ''
  try { data.value = await latest.track(() => api<typeof data.value>(`/exercise/month?month=${month.value}&trash=${trash.value}`)) }
  catch (reason) { if (!isStale(reason)) error.value = reason instanceof Error ? reason.message : '运动记录暂不可用。' }
  finally { loading.value = false }
}
onMounted(load)

type Area = 'plans' | 'records' | ''
const area = ref<Area>('')
const editing = ref<Plan | Record_>()
const planForm = ref({ title: '', local_date: new Date().toLocaleDateString('sv-SE'), kind: 'run', duration_seconds: 1800, distance_meters: 0, task_id: '', event_id: '', cancelled: false })
const recordForm = ref({ local_date: new Date().toLocaleDateString('sv-SE'), kind: 'run', duration_seconds: 1800, distance_meters: 0, plan_id: '', memo: '' })
const baseline = ref('')
const dirty = computed(() => area.value !== '' && JSON.stringify(snapshot()) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)
function snapshot() { return [area.value, editing.value?.id || '', planForm.value, recordForm.value] }

const activePlans = computed(() => data.value.plans.filter(p => !p.cancelled))

async function open(next: Exclude<Area, ''>, row?: Plan | Record_) {
  if (area.value && !(await confirmDiscard('草稿'))) return
  writes.discard()
  area.value = next; editing.value = row
  if (next === 'plans') {
    const plan = row as Plan | undefined
    planForm.value = {
      title: plan?.title || '', local_date: plan?.local_date || new Date().toLocaleDateString('sv-SE'),
      kind: plan?.kind || 'run', duration_seconds: plan?.duration_seconds ?? 1800, distance_meters: plan?.distance_meters ?? 0,
      task_id: plan?.task?.id || '', event_id: plan?.event?.id || '', cancelled: !!plan?.cancelled,
    }
  } else {
    const record = row as Record_ | undefined
    recordForm.value = {
      local_date: record?.local_date || new Date().toLocaleDateString('sv-SE'),
      kind: record?.kind || 'run', duration_seconds: record?.duration_seconds ?? 1800, distance_meters: record?.distance_meters ?? 0,
      plan_id: record?.plan_id || '', memo: record?.memo || '',
    }
  }
  baseline.value = JSON.stringify(snapshot())
}
async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的运动修改'))) return
  writes.discard(); area.value = ''; editing.value = undefined
}

async function save() {
  const version = { expected_version: editing.value?.version || 0 }
  try {
    if (area.value === 'plans') {
      const path = `/exercise/plans${editing.value ? '/' + editing.value.id : ''}`
      // An unlinked task/event is null on the wire, never the empty string the select holds.
      const { task_id, event_id, ...plan } = planForm.value
      await writes.send('exercise', path, { ...plan, task_id: task_id || null, event_id: event_id || null, ...version }, editing.value ? 'PUT' : 'POST')
    } else {
      const path = `/exercise/records${editing.value ? '/' + editing.value.id : ''}`
      const { plan_id, ...record } = recordForm.value
      await writes.send('exercise', path, { ...record, plan_id: plan_id || null, ...version }, editing.value ? 'PUT' : 'POST')
    }
    area.value = ''; editing.value = undefined
    toast.push('已保存')
    await load()
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '保存失败') }
}

async function change(next: 'plans' | 'records', row: Plan | Record_) {
  try {
    await api(`/exercise/${next}/${row.id}/${trash.value ? 'restore' : 'trash'}`, {
      method: 'POST', body: JSON.stringify({ expected_version: row.version }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    toast.push(trash.value ? '已恢复' : '已移入回收站')
    await load()
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '操作失败') }
}

async function download() {
  try {
    const payload = await api<object>('/exercise/export', { method: 'POST' })
    const url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = 'exercise.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '导出失败') }
}
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="filter-row" style="padding: 0">
        <input v-model="month" type="month" aria-label="运动月份" @change="load()" />
        <button class="chip-button" :aria-pressed="!trash" @click="trash = false; load()">记录</button>
        <button class="chip-button" :aria-pressed="trash" @click="trash = true; load()">回收站</button>
      </div>
      <div class="panel-actions">
        <HubButton variant="quiet" size="sm" @click="download">导出</HubButton>
        <HubButton variant="secondary" size="sm" :disabled="!enabled" @click="open('plans')">＋ 计划</HubButton>
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="open('records')">＋ 记录</HubButton>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted fine-print" role="status">读取中…</p>

    <template v-else>
      <p v-if="!data.statistics.length" class="muted fine-print">本月还没有实际运动记录。</p>
      <div v-else class="stat-row">
        <div v-for="stat in data.statistics" :key="stat.kind">
          <strong>{{ stat.sessions }}</strong>
          <span>{{ kindLabel(stat.kind) }} · {{ duration(stat.duration_seconds) }} · {{ distance(stat.distance_meters) }}</span>
        </div>
      </div>

      <h3>训练计划</h3>
      <p v-if="!data.plans.length" class="empty-state">本月还没有计划。</p>
      <ul v-else class="task-list">
        <li v-for="plan in data.plans" :key="plan.id">
          <div class="task-main">
            <strong class="task-title">{{ plan.local_date }} · {{ plan.title }}<span v-if="plan.cancelled" class="muted">（已取消）</span></strong>
            <div class="task-meta">
              <span>{{ kindLabel(plan.kind) }}</span>
              <span>{{ duration(plan.duration_seconds) }}</span>
              <span>{{ distance(plan.distance_meters) }}</span>
              <router-link v-if="plan.task" :to="'/resource/' + plan.task.id" class="text-link">任务：{{ plan.task.title }}</router-link>
              <router-link v-if="plan.event" :to="'/resource/' + plan.event.id" class="text-link">日程：{{ plan.event.title }}</router-link>
            </div>
          </div>
          <div class="task-actions">
            <button v-if="!trash" class="plain-action" @click="open('plans', plan)">编辑</button>
            <button class="plain-action muted" @click="change('plans', plan)">{{ trash ? '恢复' : '删除' }}</button>
          </div>
        </li>
      </ul>

      <h3>实际记录</h3>
      <p v-if="!data.records.length" class="empty-state">本月还没有实际记录。</p>
      <ul v-else class="task-list">
        <li v-for="record in data.records" :key="record.id">
          <div class="task-main">
            <strong class="task-title">{{ record.local_date }} · {{ kindLabel(record.kind) }}</strong>
            <div class="task-meta">
              <span>{{ duration(record.duration_seconds) }}</span>
              <span>{{ distance(record.distance_meters) }}</span>
              <span v-if="record.memo">{{ record.memo }}</span>
            </div>
          </div>
          <div class="task-actions">
            <button v-if="!trash" class="plain-action" @click="open('records', record)">编辑</button>
            <button class="plain-action muted" @click="change('records', record)">{{ trash ? '恢复' : '删除' }}</button>
          </div>
        </li>
      </ul>
    </template>

    <HubDialog :show="area !== ''" :title="area === 'plans' ? '训练计划' : '实际运动记录'" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="exercise-form" @submit.prevent="save">
        <label v-if="area === 'plans'" class="form-field"><span>计划名称</span><input v-model="planForm.title" required maxlength="500" autofocus /></label>
        <div class="form-grid">
          <label class="form-field"><span>日期</span>
            <input v-if="area === 'plans'" v-model="planForm.local_date" type="date" required />
            <input v-else v-model="recordForm.local_date" type="date" required />
          </label>
          <label class="form-field"><span>类型</span>
            <select v-if="area === 'plans'" v-model="planForm.kind"><option v-for="k in kinds" :key="k.value" :value="k.value">{{ k.label }}</option></select>
            <select v-else v-model="recordForm.kind"><option v-for="k in kinds" :key="k.value" :value="k.value">{{ k.label }}</option></select>
          </label>
          <label class="form-field"><span>时长（秒）</span>
            <input v-if="area === 'plans'" v-model.number="planForm.duration_seconds" type="number" min="0" />
            <input v-else v-model.number="recordForm.duration_seconds" type="number" min="0" />
          </label>
          <label class="form-field"><span>距离（米）</span>
            <input v-if="area === 'plans'" v-model.number="planForm.distance_meters" type="number" min="0" />
            <input v-else v-model.number="recordForm.distance_meters" type="number" min="0" />
          </label>
        </div>
        <template v-if="area === 'plans'">
          <label class="form-field"><span>关联任务（可选）</span><ResourcePicker v-model="planForm.task_id" kind="task" :disabled="writes.busy.value" /></label>
          <label class="form-field"><span>关联日程（可选）</span><ResourcePicker v-model="planForm.event_id" kind="event" :disabled="writes.busy.value" /></label>
          <label class="checkbox-field"><input v-model="planForm.cancelled" type="checkbox" /> 已取消</label>
        </template>
        <template v-else>
          <label class="form-field"><span>关联本月计划（可选）</span>
            <select v-model="recordForm.plan_id">
              <option value="">不关联</option>
              <option v-for="plan in activePlans" :key="plan.id" :value="plan.id">{{ plan.local_date }} · {{ plan.title }}</option>
            </select>
          </label>
          <label class="form-field"><span>备注</span><textarea v-model="recordForm.memo" rows="2" maxlength="2000" /></label>
        </template>
        <p class="fine-print">关联不会改变任务或日程的完成状态。</p>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" @click="save">保存</HubButton>
      </template>
    </HubDialog>
  </section>
</template>
