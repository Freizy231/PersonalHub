<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import { api, ApiError } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'
import { localDate } from '../tasks/types'
import ReminderPanel from '../reminders/ReminderPanel.vue'
import { browserZone, calendarDays, downloadJSON, eventLabel, localInput, parseLocalInput, shiftDate, type HubEvent, type Page } from './types'

const props = defineProps<{ enabled: boolean; initialId?: string }>()
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const labels = { scheduled: '已安排', completed: '已完成', cancelled: '已取消' } as const
const selected = ref(localDate())
const month = ref(localDate().slice(0, 7))
const trash = ref(false)
const offset = ref(0)
const limit = 30
const days = computed(() => calendarDays(month.value + '-01'))
const page = ref<Page<HubEvent>>({ items: [], total: 0, offset: 0, limit })
const loading = ref(false)
const error = ref('')

function message(reason: unknown) { return reason instanceof Error ? reason.message : '操作未完成，请重试。' }

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const query = new URLSearchParams({
      start_date: selected.value,
      end_date: shiftDate(selected.value, 1),
      timezone: browserZone(),
      scope: trash.value ? 'trash' : 'calendar',
      limit: String(limit),
      offset: String(offset.value),
    })
    const result = await latest.track(() => api<Page<HubEvent>>(`/events?${query}`))
    page.value = result
    if (!result.items.length && offset.value) { offset.value = Math.max(0, offset.value - limit); await load() }
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { loading.value = false }
}

watch(() => [props.enabled, selected.value, trash.value], () => { offset.value = 0; void load() })
watch(month, v => { if (!v.startsWith(selected.value.slice(0, 7))) selected.value = v + '-01' })
onMounted(() => { void load(); if (props.initialId) void loadOne(props.initialId) })

function moveMonth(delta: number) {
  month.value = shiftDate(month.value + '-01', delta < 0 ? -1 : 32).slice(0, 7)
}
function goToday() { month.value = localDate().slice(0, 7); selected.value = localDate() }

/* ------------------------------------------------------------------ editor */
const show = ref(false)
const editing = ref<HubEvent>()
const reminding = ref<HubEvent>()
const form = ref({ title: '', description: '', location: '', all_day: true, start: '', end: '', status: 'scheduled' as HubEvent['status'], task_id: '' })
const baseline = ref(JSON.stringify(form.value))
const formError = ref('')
const conflict = ref(false)
const taskChoices = ref<{ id: string; title: string }[]>([])
const tasksLoading = ref(false)
let tasksLoaded = false

const dirty = computed(() => show.value && JSON.stringify(form.value) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

async function loadOne(id: string) {
  try {
    const row = await api<HubEvent>(`/events/${id}`)
    if (row.start_date) selected.value = row.start_date
    else if (row.start_at) selected.value = localInput(row.start_at).slice(0, 10)
    month.value = selected.value.slice(0, 7)
    open(row)
  } catch (reason) { error.value = message(reason) }
}

async function loadTasks() {
  if (tasksLoaded) return
  tasksLoading.value = true
  try { taskChoices.value = (await api<Page<{ id: string; title: string }>>('/tasks?limit=100')).items; tasksLoaded = true }
  catch { /* Association is optional. */ }
  finally { tasksLoading.value = false }
}

function open(row?: HubEvent) {
  editing.value = row
  form.value = {
    title: row?.title || '',
    description: row?.description || '',
    location: row?.location || '',
    all_day: row?.is_all_day ?? true,
    status: row?.status || 'scheduled',
    task_id: row?.task_id || '',
    start: row ? (row.is_all_day ? row.start_date! : localInput(row.start_at!)) : selected.value,
    end: row ? (row.is_all_day ? shiftDate(row.end_date!, -1) : localInput(row.end_at!)) : selected.value,
  }
  baseline.value = JSON.stringify(form.value)
  writes.discard(); formError.value = ''; conflict.value = false; show.value = true
  void loadTasks()
}

function toggleAllDay() {
  const first = form.value.start.slice(0, 10) || selected.value
  const last = form.value.end.slice(0, 10) || first
  form.value.start = first + (form.value.all_day ? '' : 'T09:00')
  form.value.end = last + (form.value.all_day ? '' : 'T10:00')
}

async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的日程修改'))) return
  writes.discard(); show.value = false
}

async function save() {
  if (!form.value.title.trim()) { formError.value = '请填写日程名称。'; return }
  formError.value = ''
  try {
    const fields: Record<string, unknown> = {
      title: form.value.title.trim(), description: form.value.description || null, location: form.value.location || null,
      is_all_day: form.value.all_day, status: form.value.status,
      timezone: editing.value?.timezone || browserZone(),
      start_date: null, end_date: null, start_at: null, end_at: null,
    }
    if (form.value.all_day) {
      if (!form.value.start || !form.value.end || form.value.end < form.value.start) throw new Error('结束日期不能早于开始日期。')
      fields.start_date = form.value.start; fields.end_date = shiftDate(form.value.end, 1)
    } else {
      fields.start_at = parseLocalInput(form.value.start); fields.end_at = parseLocalInput(form.value.end)
      if (new Date(String(fields.end_at)).getTime() <= new Date(String(fields.start_at)).getTime()) throw new Error('结束时间必须晚于开始时间。')
    }
    if (!editing.value || form.value.task_id !== (editing.value.task_id || '')) fields.task_id = form.value.task_id || null
    if (editing.value) await writes.send<HubEvent>('event', `/events/${editing.value.id}`, { ...fields, expected_version: editing.value.version }, 'PUT')
    else await writes.send<HubEvent>('event', '/events', fields, 'POST')
    show.value = false
    toast.push(editing.value ? '日程已保存' : '日程已添加')
    await load()
  } catch (reason) {
    formError.value = message(reason)
    conflict.value = reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
  }
}

async function change(row: HubEvent, action: 'trash' | 'restore') {
  try {
    const updated = await api<HubEvent>(`/events/${row.id}/${action}`, { method: 'POST', body: JSON.stringify({ expected_version: row.version }) })
    page.value = { ...page.value, items: page.value.items.filter(e => e.id !== row.id), total: Math.max(0, page.value.total - 1) }
    if (action === 'trash') {
      toast.push('日程已移入回收站', async () => {
        await api(`/events/${row.id}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: updated.version }) })
        await load()
      })
    } else toast.push('日程已恢复')
  } catch (reason) {
    if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') { toast.error('日程已变化，正在重新读取。'); await load() }
    else toast.error(message(reason))
  }
}

function turn(direction: number) { offset.value = Math.max(0, offset.value + direction * limit); void load() }

async function exportData() {
  try { downloadJSON(await api<object>('/events/export'), 'personal-hub-events.json') }
  catch (reason) { toast.error(message(reason)) }
}
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="calendar-heading">
        <HubButton variant="quiet" size="sm" aria-label="上个月" @click="moveMonth(-1)">←</HubButton>
        <strong>{{ month }}</strong>
        <HubButton variant="quiet" size="sm" aria-label="下个月" @click="moveMonth(1)">→</HubButton>
        <HubButton variant="quiet" size="sm" @click="goToday">今天</HubButton>
      </div>
      <div class="panel-actions">
        <HubButton variant="quiet" size="sm" @click="trash = !trash">{{ trash ? '回到日历' : '回收站' }}</HubButton>
        <HubButton variant="quiet" size="sm" @click="exportData">导出</HubButton>
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="open()">＋ 新建日程</HubButton>
      </div>
    </div>

    <div class="calendar-layout">
      <div>
        <div class="calendar-grid">
          <span v-for="d in ['一', '二', '三', '四', '五', '六', '日']" :key="d" class="muted">{{ d }}</span>
          <button
            v-for="day in days" :key="day" :aria-label="day"
            :aria-pressed="selected === day" :class="{ muted: !day.startsWith(month) }"
            @click="selected = day"
          >{{ Number(day.slice(-2)) }}<small v-if="day === localDate()">·</small></button>
        </div>
      </div>

      <div>
        <h3>{{ trash ? '回收站 · ' + selected : selected }}</h3>
        <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
        <p v-if="loading" class="muted fine-print" role="status">读取中…</p>
        <p v-else-if="!page.items.length && !error" class="empty-state">{{ trash ? '这一天没有已回收日程。' : '这一天还没有安排。' }}</p>
        <ul v-else class="task-list">
          <li v-for="row in page.items" :key="row.id">
            <span class="task-marker" aria-hidden="true" />
            <div class="task-main">
              <button class="task-title" @click="open(row)">{{ row.title }}</button>
              <div class="task-meta">
                <span>{{ eventLabel(row) }}</span>
                <span>{{ labels[row.status] }}</span>
                <span v-if="row.location">{{ row.location }}</span>
                <span v-if="row.linked_task">任务：{{ row.linked_task.title }}</span>
              </div>
              <p v-if="row.description" class="task-description">{{ row.description }}</p>
            </div>
            <div class="task-actions">
              <template v-if="trash"><button class="plain-action" @click="change(row, 'restore')">恢复</button></template>
              <template v-else>
                <button v-if="row.status === 'scheduled'" class="plain-action" @click="reminding = row">提醒</button>
                <button class="plain-action muted" @click="change(row, 'trash')">删除</button>
              </template>
            </div>
          </li>
        </ul>
        <div v-if="page.total > limit" class="pager">
          <HubButton variant="quiet" size="sm" :disabled="offset === 0 || loading" @click="turn(-1)">上一页</HubButton>
          <span class="muted">{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(page.total / limit) }}</span>
          <HubButton variant="quiet" size="sm" :disabled="offset + limit >= page.total || loading" @click="turn(1)">下一页</HubButton>
        </div>
      </div>
    </div>

    <HubDialog :show="show" :title="editing ? '编辑日程' : '新建日程'" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="event-form" @submit.prevent="save">
        <p v-if="formError" class="form-error" role="alert">{{ formError }}</p>
        <label class="form-field"><span>日程名称</span><input v-model="form.title" maxlength="500" autofocus /></label>
        <label class="form-field"><span>说明</span><textarea v-model="form.description" rows="3" maxlength="20000" /></label>
        <label class="checkbox-field"><input v-model="form.all_day" type="checkbox" @change="toggleAllDay" /> 全天日程</label>
        <div class="form-grid">
          <label class="form-field"><span>开始</span><input v-model="form.start" :type="form.all_day ? 'date' : 'datetime-local'" required /></label>
          <label class="form-field"><span>{{ form.all_day ? '结束（含当天）' : '结束' }}</span><input v-model="form.end" :type="form.all_day ? 'date' : 'datetime-local'" required /></label>
        </div>
        <label class="form-field"><span>地点</span><input v-model="form.location" maxlength="1000" /></label>
        <div class="form-grid">
          <label class="form-field"><span>状态</span>
            <select v-model="form.status"><option v-for="(label, key) in labels" :key="key" :value="key">{{ label }}</option></select>
          </label>
          <label class="form-field"><span>关联任务</span>
            <select v-model="form.task_id" :disabled="tasksLoading">
              <option value="">不关联</option>
              <option v-if="editing?.linked_task && !taskChoices.some(t => t.id === editing?.task_id)" :value="editing.task_id!">{{ editing.linked_task.title }}</option>
              <option v-for="t in taskChoices" :key="t.id" :value="t.id">{{ t.title }}</option>
            </select>
          </label>
        </div>
        <p class="fine-print">时区 {{ browserZone() }}。改期、完成或取消日程会取消待提醒，恢复后需重新设置。</p>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
        <HubButton variant="primary" type="submit" :loading="writes.busy.value" :disabled="conflict" @click="save">保存</HubButton>
      </template>
    </HubDialog>

    <HubDialog :show="!!reminding" title="设置提醒" @update:show="v => { if (!v) reminding = undefined }">
      <ReminderPanel v-if="reminding" :key="reminding.id" :resource="reminding" :enabled="enabled" embedded />
    </HubDialog>
  </section>
</template>
