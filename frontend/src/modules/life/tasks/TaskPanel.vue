<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import { api, ApiError } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'
import { confirmDiscard } from '../../../shared/confirm'
import PurgeDialog from '../../../shared/PurgeDialog.vue'
import { localDate, type Task, type TaskPage, type TaskScope, type TaskStatus } from './types'

const props = withDefaults(defineProps<{ enabled: boolean; initialId?: string; compact?: boolean; autoCreate?: boolean }>(), { compact: false, autoCreate: false })
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const scopes: { value: TaskScope; label: string }[] = [
  { value: 'today', label: '今天' },
  { value: 'upcoming', label: '接下来' },
  { value: 'overdue', label: '逾期' },
  { value: 'all', label: '全部' },
  { value: 'completed', label: '已完成' },
  { value: 'trash', label: '回收站' },
]
const priorities = [
  { value: 3, label: '高' },
  { value: 2, label: '中' },
  { value: 1, label: '低' },
  { value: 0, label: '无' },
]
const statuses: { value: TaskStatus; label: string }[] = [
  { value: 'todo', label: '待办' },
  { value: 'doing', label: '进行中' },
  { value: 'done', label: '已完成' },
  { value: 'cancelled', label: '已取消' },
]
const limit = props.compact ? 8 : 30

const scope = ref<TaskScope>('today')
const offset = ref(0)
const page = ref<TaskPage>({ items: [], total: 0, offset: 0, limit })
const loading = ref(false)
const error = ref('')
const tagFilter = ref('')
const tagChoices = ref<{ id: string; name: string }[]>([])
let tagsLoaded = false

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const query = new URLSearchParams({ scope: scope.value, on_date: localDate(), limit: String(limit), offset: String(offset.value) })
    if (tagFilter.value) query.set('tag_id', tagFilter.value)
    const result = await latest.track(() => api<TaskPage>(`/tasks?${query}`))
    page.value = result
    if (result.total > 0 && result.items.length === 0 && offset.value > 0) {
      offset.value = Math.max(0, offset.value - limit)
      await load()
    }
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { loading.value = false }
}
function message(reason: unknown) { return reason instanceof Error ? reason.message : '操作未完成，请重试。' }

async function loadTags() {
  if (tagsLoaded) return
  try { tagChoices.value = (await api<{ items: typeof tagChoices.value }>('/tags?limit=100')).items; tagsLoaded = true }
  catch { /* Filter stays usable with "全部标签" only. */ }
}

watch(scope, () => { offset.value = 0; void load() })
watch(tagFilter, () => { offset.value = 0; void load() })
watch(() => props.enabled, v => { if (v) void load() })
onMounted(() => { void load(); if (props.initialId) void openById(props.initialId); else if (props.autoCreate) startNew() })

/* ------------------------------------------------------------------ editor */
const show = ref(false)
const editing = ref<Task>()
const form = ref({ title: '', description: '', priority: 2, planned_date: '', due_date: '', status: 'todo' as TaskStatus })
const baseline = ref(JSON.stringify(form.value))
const formError = ref('')
const conflict = ref(false)

const dirty = computed(() => show.value && JSON.stringify(form.value) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

function openById(id: string) {
  api<Task>(`/tasks/${id}`).then(startEdit).catch(e => { error.value = message(e) })
}
function startNew() {
  editing.value = undefined
  form.value = { title: '', description: '', priority: 2, planned_date: scope.value === 'today' ? localDate() : '', due_date: '', status: 'todo' }
  baseline.value = JSON.stringify(form.value)
  writes.discard(); formError.value = ''; conflict.value = false; show.value = true
}
function startEdit(task: Task) {
  editing.value = task
  form.value = {
    title: task.title, description: task.description || '', priority: task.priority,
    planned_date: task.planned_date || '', due_date: task.due_date || '', status: task.status,
  }
  baseline.value = JSON.stringify(form.value)
  writes.discard(); formError.value = ''; conflict.value = false; show.value = true
}
async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('这项任务的未保存修改'))) return
  writes.discard(); show.value = false
}

async function save() {
  if (!form.value.title.trim()) { formError.value = '请填写任务名称。'; return }
  formError.value = ''
  const fields = {
    title: form.value.title.trim(),
    description: form.value.description || null,
    priority: form.value.priority,
    planned_date: form.value.planned_date || null,
    due_date: form.value.due_date || null,
    due_at: editing.value?.due_at || null,
    timezone: editing.value?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
  }
  try {
    if (editing.value) await writes.send<Task>('task', `/tasks/${editing.value.id}`, { ...fields, status: form.value.status, expected_version: editing.value.version }, 'PATCH')
    else await writes.send<Task>('task', '/tasks', fields, 'POST')
    show.value = false
    toast.push(editing.value ? '任务已保存' : '任务已添加')
    await load()
  } catch (reason) {
    formError.value = message(reason)
    conflict.value = reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
    if (writes.uncertain.value) formError.value += ' 请保留原内容重试，或关闭后刷新列表核对。'
  }
}

async function reloadKeepingDraft() {
  await load()
  formError.value = '列表已刷新，你的草稿仍保留。请核对最新内容后再保存。'
}

/* --------------------------------------------------------------- quick add */
const quick = ref('')
const quickBusy = ref(false)
const quickWrites = useIdempotent()
useUnsaved(() => !!quick.value.trim() || quickWrites.hasPending(), undefined)

/**
 * "买牛奶 #杂货" is not tag syntax on the server, so the line is a title only.
 * Prefixes the person already thinks in set the planned date and priority:
 * "明天 …", "后天 …", "! …" (high priority).
 */
function parseQuick(raw: string) {
  let title = raw.trim(); let planned = scope.value === 'today' ? localDate() : ''; let priority = 2
  const shift = (days: number) => { const d = new Date(); d.setDate(d.getDate() + days); return localDate(d) }
  const prefix = /^(今天|明天|后天)\s+/.exec(title)
  if (prefix) { planned = shift(prefix[1] === '今天' ? 0 : prefix[1] === '明天' ? 1 : 2); title = title.slice(prefix[0].length) }
  if (/^[!！]\s*/.test(title)) { priority = 3; title = title.replace(/^[!！]\s*/, '') }
  return { title: title.trim(), planned, priority }
}
async function quickAdd() {
  const parsed = parseQuick(quick.value)
  if (!parsed.title || quickBusy.value) return
  quickBusy.value = true
  try {
    await quickWrites.send<Task>('quick', '/tasks', {
      title: parsed.title.slice(0, 500), priority: parsed.priority, planned_date: parsed.planned || null,
      timezone: Intl.DateTimeFormat().resolvedOptions().timeZone || 'Asia/Shanghai',
    }, 'POST')
    quick.value = ''
    toast.push(parsed.planned && parsed.planned !== localDate() ? `已添加，计划在 ${parsed.planned}` : '任务已添加')
    await load()
  } catch (reason) { toast.error(message(reason)) }
  finally { quickBusy.value = false }
}

/* -------------------------------------------------------------- row actions */
async function toggleDone(task: Task) {
  try {
    const updated = await api<Task>(`/tasks/${task.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ status: task.status === 'done' ? 'todo' : 'done', expected_version: task.version }),
    })
    replace(updated)
    toast.push(updated.status === 'done' ? '任务已完成' : '任务已重开', async () => {
      await api<Task>(`/tasks/${task.id}`, { method: 'PATCH', body: JSON.stringify({ status: task.status, expected_version: updated.version }) })
      await load()
    })
  } catch (reason) { await conflicted(task, reason) }
}

async function planToday(task: Task) {
  try {
    const updated = await api<Task>(`/tasks/${task.id}`, {
      method: 'PATCH',
      body: JSON.stringify({ planned_date: localDate(), expected_version: task.version }),
    })
    replace(updated)
    toast.push('已加入今天')
  } catch (reason) { await conflicted(task, reason) }
}

async function trash(task: Task) {
  try {
    const updated = await api<Task>(`/tasks/${task.id}/trash`, { method: 'POST', body: JSON.stringify({ expected_version: task.version }) })
    page.value = { ...page.value, items: page.value.items.filter(t => t.id !== task.id), total: Math.max(0, page.value.total - 1) }
    toast.push('已移入回收站', async () => {
      await api<Task>(`/tasks/${task.id}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: updated.version }) })
      await load()
    })
  } catch (reason) { await conflicted(task, reason) }
}

async function restore(task: Task) {
  try {
    await api<Task>(`/tasks/${task.id}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: task.version }) })
    page.value = { ...page.value, items: page.value.items.filter(t => t.id !== task.id), total: Math.max(0, page.value.total - 1) }
    toast.push('任务已恢复')
  } catch (reason) { await conflicted(task, reason) }
}

function replace(updated: Task) {
  page.value = { ...page.value, items: page.value.items.map(t => t.id === updated.id ? updated : t) }
}
async function conflicted(task: Task, reason: unknown) {
  if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') {
    toast.error('这项任务已被修改，已重新读取最新内容。')
    await load()
    return
  }
  toast.error(message(reason))
}

const purging = ref('')
function purged(id: string) {
  page.value = { ...page.value, items: page.value.items.filter(t => t.id !== id), total: Math.max(0, page.value.total - 1) }
  toast.push('已永久删除')
}

function turn(direction: number) {
  offset.value = Math.max(0, offset.value + direction * limit)
  void load()
}

const emptyText = computed(() => ({
  today: { title: '今天还没有安排', body: '从一件小事开始，或把已有任务加入今天。' },
  upcoming: { title: '还没有后续计划', body: '给本周之外的事留个位置。' },
  overdue: { title: '没有逾期任务', body: '这里只显示已过期且未完成的任务。' },
  all: { title: '这里还没有任务', body: '任务保存在这台设备上。' },
  completed: { title: '还没有完成的任务', body: '完成任务后会出现在这里。' },
  trash: { title: '回收站为空', body: '删除的任务会保留，可以随时恢复。' },
} as Record<TaskScope, { title: string; body: string }>)[scope.value])
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div v-if="!compact" class="chip-row" role="group" aria-label="任务范围">
        <button v-for="item in scopes" :key="item.value" :aria-pressed="scope === item.value" @click="scope = item.value">{{ item.label }}</button>
      </div>
      <h2 v-else class="compact-title">今日任务</h2>
      <div class="panel-actions">
        <router-link v-if="compact" to="/tasks" class="plain-action">全部任务 →</router-link>
        <HubButton v-else variant="quiet" :disabled="!enabled" size="sm" @click="startNew">详细新建</HubButton>
      </div>
    </div>

    <form v-if="scope !== 'trash'" class="quick-add" @submit.prevent="quickAdd">
      <input
        v-model="quick"
        :disabled="!enabled || quickBusy"
        maxlength="500"
        autocomplete="off"
        aria-label="快速添加任务"
        placeholder="添加任务，回车保存（如「明天 写周报」「! 交报告」）"
      />
      <HubButton variant="primary" size="sm" type="submit" :loading="quickBusy" :disabled="!enabled || !quick.trim()">添加</HubButton>
    </form>

    <div v-if="scope !== 'trash' && !compact" class="filter-row">
      <select v-model="tagFilter" aria-label="按标签筛选" @focus="loadTags">
        <option value="">全部标签</option>
        <option v-for="t in tagChoices" :key="t.id" :value="t.id">{{ t.name }}</option>
      </select>
      <span v-if="loading" class="muted fine-print" role="status">读取中…</span>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <div v-if="loading && !page.items.length" class="empty-state" role="status">正在读取本机任务…</div>
    <div v-else-if="!page.items.length && !error" class="empty-state">
      <h3>{{ emptyText.title }}</h3>
      <p>{{ emptyText.body }}</p>
    </div>

    <ul v-else class="task-list">
      <li v-for="task in page.items" :key="task.id" :class="{ 'task-done': task.status === 'done' }">
        <button
          v-if="scope !== 'trash'"
          class="task-checkbox"
          :class="{ checked: task.status === 'done' }"
          :aria-label="`${task.status === 'done' ? '重开' : '完成'}任务：${task.title}`"
          :aria-pressed="task.status === 'done'"
          @click="toggleDone(task)"
        >{{ task.status === 'done' ? '✓' : '' }}</button>
        <span v-else class="task-checkbox" aria-hidden="true" />
        <div class="task-main">
          <button class="task-title" @click="startEdit(task)">{{ task.title }}</button>
          <div class="task-meta">
            <span v-if="task.priority === 3" class="priority-high">高优先级</span>
            <span v-if="task.planned_date">{{ task.planned_date === localDate() ? '今天' : task.planned_date }}</span>
            <span v-if="task.due_date" :class="{ overdue: task.is_overdue }">{{ task.is_overdue ? '已逾期 ' : '截止 ' }}{{ task.due_date }}</span>
            <span v-if="task.due_at" :class="{ overdue: task.is_overdue }">截止 {{ new Date(task.due_at).toLocaleString('zh-CN', { timeZone: task.timezone }) }}</span>
            <span v-if="task.status !== 'todo'">{{ statuses.find(s => s.value === task.status)?.label }}</span>
            <span v-for="tag in task.tags || []" :key="tag.id">#{{ tag.name }}</span>
          </div>
          <p v-if="task.description" class="task-description">{{ task.description }}</p>
        </div>
        <div class="task-actions">
          <template v-if="scope === 'trash'">
            <button class="plain-action" @click="restore(task)">恢复</button>
            <button class="plain-action muted" @click="purging = task.id">永久删除</button>
          </template>
          <template v-else>
            <router-link :to="'/resource/' + task.id" class="plain-action">详情</router-link>
            <button v-if="task.planned_date !== localDate() && !['done', 'cancelled'].includes(task.status)" class="plain-action" @click="planToday(task)">加入今天</button>
            <button class="plain-action muted" @click="trash(task)">删除</button>
          </template>
        </div>
      </li>
    </ul>

    <div v-if="page.total > limit && !compact" class="pager">
      <HubButton variant="quiet" size="sm" :disabled="offset === 0 || loading" @click="turn(-1)">上一页</HubButton>
      <span class="muted">{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(page.total / limit) }}</span>
      <HubButton variant="quiet" size="sm" :disabled="offset + limit >= page.total || loading" @click="turn(1)">下一页</HubButton>
    </div>

    <HubDialog :show="show" :title="editing ? '编辑任务' : '新建任务'" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="task-form" @submit.prevent="save">
        <p v-if="formError" class="form-error" role="alert">
          {{ formError }}
          <button v-if="conflict" type="button" class="plain-action" @click="reloadKeepingDraft">刷新列表并保留草稿</button>
        </p>
        <label class="form-field"><span>任务名称</span>
          <input v-model="form.title" maxlength="500" autofocus aria-label="任务名称" placeholder="例如：完成 PE 导入表练习" />
        </label>
        <label class="form-field"><span>说明</span>
          <textarea v-model="form.description" rows="3" maxlength="20000" placeholder="可选，记录下一步或验收要求" />
        </label>
        <div class="form-grid">
          <label class="form-field"><span>优先级</span>
            <select v-model.number="form.priority"><option v-for="p in priorities" :key="p.value" :value="p.value">{{ p.label }}</option></select>
          </label>
          <label v-if="editing" class="form-field"><span>状态</span>
            <select v-model="form.status"><option v-for="s in statuses" :key="s.value" :value="s.value">{{ s.label }}</option></select>
          </label>
          <label class="form-field"><span>计划日</span><input v-model="form.planned_date" type="date" /></label>
          <label class="form-field"><span>截止日</span><input v-model="form.due_date" type="date" :disabled="!!editing?.due_at" /></label>
        </div>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
        <HubButton variant="primary" type="submit" :loading="writes.busy.value" :disabled="conflict" @click="save">保存</HubButton>
      </template>
    </HubDialog>
    <PurgeDialog :show="!!purging" :resource-id="purging" @update:show="v => { if (!v) purging = '' }" @purged="purged" />
  </section>
</template>
