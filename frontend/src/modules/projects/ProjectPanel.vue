<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import RecordEditor from '../../shared/RecordEditor.vue'
import ResourceContext from '../knowledge/ResourceContext.vue'
import TagFilter from '../tags/TagFilter.vue'
import ToolLauncher from './ToolLauncher.vue'
import { api, ApiError } from '../../shared/api'
import { type Resource, type Field, type Page, errorMessage } from '../../shared/resource'
import { isStale, useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { tabKey } from '../../shared/useTabKeys'
import { useToast } from '../../shared/toast'

const props = defineProps<{ enabled: boolean; initialId?: string }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Progress { done: number; total: number; percent: number | null; denominator: string }
interface Project extends Resource { description: string; objective: string; status: string; local_directory: string; repository_url: string; start_date: string | null; target_date: string | null; progress: Progress }
interface Milestone { id: string; title: string; description: string; target_date: string | null; status: string; position: number; version: number; deleted_at: string | null; progress: Progress }
interface ProjectTask extends Resource { link_id: string; status: string; planned_date: string | null; milestone_id: string | null }
interface Entry { id: string; note_id: string; title: string; entry_kind: string; created_at: string; version: number }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const states = [
  { value: 'planned', label: '计划中' }, { value: 'active', label: '进行中' },
  { value: 'paused', label: '暂停' }, { value: 'completed', label: '完成' }, { value: 'archived', label: '归档' },
]
const entryNames: Record<string, string> = { log: '项目日志', experiment: '实验记录', summary: '阶段总结' }
const taskStatus: Record<string, string> = { todo: '待办', doing: '进行中', done: '已完成', cancelled: '已取消' }
const milestoneStatus: Record<string, string> = { todo: '待办', doing: '进行中', done: '已验收' }
const directoryLabels: Record<string, string> = {
  available: '目录存在', missing: '目录不存在', not_configured: '未登记目录', unavailable_or_unsafe: '目录不可访问',
}

const tabs = [
  { id: 'tasks', label: '任务' }, { id: 'milestones', label: '里程碑' },
  { id: 'entries', label: '记录' }, { id: 'resources', label: '关联' }, { id: 'tools', label: '工具' },
] as const

const fields: Field[] = [
  { key: 'description', label: '项目说明', type: 'textarea' },
  { key: 'objective', label: '目标与完成标准', type: 'textarea' },
  { key: 'status', label: '状态', type: 'select', options: states, default: 'planned' },
  { key: 'local_directory', label: '本地项目目录（绝对路径）' },
  { key: 'repository_url', label: '仓库 URL（可选）' },
  { key: 'start_date', label: '开始日期', type: 'date', nullable: true },
  { key: 'target_date', label: '目标日期', type: 'date', nullable: true },
]

const page = ref<Page<Project>>({ items: [], total: 0, limit: 20, offset: 0 })
const status = ref('')
const tag = ref('')
const trash = ref(false)
const loading = ref(false)
const error = ref('')

const selected = ref<Project>()
const tab = ref<string>('tasks')
const editorOpen = ref(false)
const editorDirty = ref(false)
const ownDirty = ref(false)
const contextDirty = ref(false)
const launcherDirty = ref(false)

const tasks = ref<Page<ProjectTask>>({ items: [], total: 0, limit: 20, offset: 0 })
const milestones = ref<Milestone[]>([])
const entries = ref<Page<Entry>>({ items: [], total: 0, limit: 20, offset: 0 })
const milestoneTrash = ref(false)
const directoryState = ref('')

const activeMilestones = computed(() => milestones.value.filter(m => !m.deleted_at))
const archived = computed(() => selected.value?.status === 'archived')
const dirty = computed(() => editorDirty.value || contextDirty.value || ownDirty.value || launcherDirty.value || writes.hasPending())
useUnsaved(() => dirty.value, emit)

const limit = 20

async function load(next = page.value.offset) {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ status: status.value, trash: String(trash.value), limit: String(limit), offset: String(next) })
    if (tag.value) params.set('tag_id', tag.value)
    const result = await latest.track(() => api<Page<Project>>(`/projects?${params}`))
    page.value = result
    if (result.total > 0 && result.items.length === 0 && next > 0) { await load(Math.max(0, next - limit)); return }
  } catch (reason) {
    if (!isStale(reason)) error.value = errorMessage(reason)
  } finally { loading.value = false }
}

async function detail(id: string) {
  try {
    const [project, taskPage, milestonePage, entryPage] = await Promise.all([
      api<Project>(`/projects/${id}`),
      api<Page<ProjectTask>>(`/projects/${id}/tasks?limit=20&offset=${tasks.value.offset}`),
      api<{ items: Milestone[] }>(`/projects/${id}/milestones?trash=${milestoneTrash.value}`),
      api<Page<Entry>>(`/projects/${id}/entries?limit=20&offset=${entries.value.offset}`),
    ])
    selected.value = project; tasks.value = taskPage; milestones.value = milestonePage.items; entries.value = entryPage
  } catch (reason) { error.value = errorMessage(reason) }
}

async function select(project: Project) {
  if (dirty.value && !(await confirmDiscard('未保存的项目修改'))) return
  resetDrafts()
  error.value = ''; selected.value = undefined; directoryState.value = ''; tab.value = 'tasks'
  tasks.value.offset = 0; entries.value.offset = 0; milestoneTrash.value = false
  await detail(project.id)
  try { await api(`/projects/${project.id}/opened`, { method: 'POST' }) } catch { /* Visit tracking is cosmetic. */ }
}

function resetDrafts() {
  editorDirty.value = false; ownDirty.value = false; contextDirty.value = false; launcherDirty.value = false
  writes.discard()
}

async function refreshAfter() {
  if (!selected.value) return
  await detail(selected.value.id); await load()
}

function onSaved(value: Record<string, unknown>) {
  editorOpen.value = false; resetDrafts()
  toast.push('项目已保存')
  void load().then(() => detail((value as Resource).id))
}

async function startEdit() {
  if (dirty.value && !(await confirmDiscard('未保存的项目修改'))) return
  resetDrafts(); editorOpen.value = true
}

/** A new project starts from nothing: it must not inherit (and then PUT over) the open project. */
async function startNew() {
  if (dirty.value && !(await confirmDiscard('未保存的项目修改'))) return
  resetDrafts(); selected.value = undefined; directoryState.value = ''; editorOpen.value = true
}

function backdrop() { selected.value = undefined; resetDrafts(); void load() }

/* --------------------------------------------------------------- project facts */
async function run(slot: string, path: string, body: Record<string, unknown>, method: 'POST' | 'PUT' | 'PATCH' = 'POST') {
  if (!selected.value) return false
  try {
    await writes.send(slot, path, body, method)
    ownDirty.value = false
    return true
  } catch (reason) {
    toast.error(reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
      ? '项目已变化，正在重新读取最新版本。'
      : errorMessage(reason))
    if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') await refreshAfter()
    return false
  }
}

async function lifecycle(project: Project) {
  if (dirty.value) return
  try {
    await writes.send('project-lifecycle', `/projects/${project.id}/lifecycle/${project.deleted_at ? 'restore' : 'trash'}`, { expected_version: project.version })
    toast.push(project.deleted_at ? '项目已恢复' : '项目已移入回收站')
    if (!project.deleted_at) selected.value = undefined
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function checkDirectory() {
  if (!selected.value) return
  try { directoryState.value = (await api<{ status: string }>(`/projects/${selected.value.id}/directory-check`, { method: 'POST' })).status }
  catch (reason) { toast.error(errorMessage(reason)) }
}

/* ------------------------------------------------------------------- tasks */
const taskTitle = ref('')
const taskDescription = ref('')
const taskDate = ref('')
const taskPriority = ref(2)
const taskMilestone = ref('')
const taskMode = ref<'new' | 'existing'>('new')
const taskQuery = ref('')
const taskResults = ref<Resource[]>([])
const existingTask = ref('')

function markTask() { ownDirty.value = true }

async function findResources(kind: 'task' | 'note', query: string) {
  try {
    const items = (await api<{ items: Resource[] }>(`/resources?kind=${kind}&q=${encodeURIComponent(query)}&limit=50`)).items
    if (kind === 'task') taskResults.value = items
    else noteResults.value = items
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function createTask() {
  if (!selected.value) return
  if (await run('task-create', `/projects/${selected.value.id}/tasks`, {
    expected_version: selected.value.version, title: taskTitle.value,
    description: taskDescription.value, priority: Number(taskPriority.value),
    planned_date: taskDate.value || null, milestone_id: taskMilestone.value || null,
  })) {
    taskTitle.value = ''; taskDescription.value = ''; taskDate.value = ''
    await refreshAfter()
  }
}
async function attachTask() {
  if (!selected.value || !existingTask.value) return
  if (await run('task-attach', `/projects/${selected.value.id}/tasks/attach`, {
    expected_version: selected.value.version, task_id: existingTask.value, milestone_id: taskMilestone.value || null,
  })) { existingTask.value = ''; await refreshAfter() }
}
async function reassign(task: ProjectTask, event: Event) {
  if (!selected.value) return
  const milestoneId = (event.target as HTMLSelectElement).value
  if (await run('task-milestone', `/projects/${selected.value.id}/tasks/attach`, {
    expected_version: selected.value.version, task_id: task.id, milestone_id: milestoneId || null,
  })) await refreshAfter()
}
async function complete(task: ProjectTask) {
  await run('task-status', `/tasks/${task.id}`, { expected_version: task.version, status: task.status === 'done' ? 'todo' : 'done' }, 'PATCH')
    .then(ok => { if (ok) void refreshAfter() })
}
async function unlink(id: string) {
  if (!selected.value) return
  if (await run('unlink', `/projects/${selected.value.id}/links/${id}/remove`, { expected_version: selected.value.version })) await refreshAfter()
}

/* -------------------------------------------------------------- milestones */
const milestoneEditing = ref(false)
const milestoneId = ref('')
const milestoneVersion = ref(0)
const milestoneForm = ref({ title: '', description: '', target_date: '', status: 'todo', position: 0 })

async function editMilestone(milestone?: Milestone) {
  if (dirty.value && !(await confirmDiscard('未保存的项目修改'))) return
  resetDrafts()
  milestoneId.value = milestone?.id || ''
  milestoneVersion.value = milestone?.version || 0
  milestoneForm.value = {
    title: milestone?.title || '', description: milestone?.description || '',
    target_date: milestone?.target_date || '', status: milestone?.status || 'todo', position: milestone?.position || 0,
  }
  milestoneEditing.value = true
}
async function cancelMilestone() {
  if (ownDirty.value && !(await confirmDiscard('里程碑表单里未保存的内容'))) return
  milestoneEditing.value = false; ownDirty.value = false
}
async function saveMilestone() {
  if (!selected.value) return
  const ok = await run('milestone', `/projects/${selected.value.id}/milestones${milestoneId.value ? '/' + milestoneId.value : ''}`, {
    expected_version: selected.value.version,
    expected_milestone_version: milestoneVersion.value,
    ...milestoneForm.value,
    target_date: milestoneForm.value.target_date || null,
    position: Number(milestoneForm.value.position),
  }, milestoneId.value ? 'PUT' : 'POST')
  if (ok) { milestoneEditing.value = false; await refreshAfter() }
}
async function toggleMilestone(milestone: Milestone) {
  if (!selected.value) return
  const ok = await run('milestone-life', `/projects/${selected.value.id}/milestones/${milestone.id}/${milestone.deleted_at ? 'restore' : 'trash'}`, {
    expected_version: selected.value.version, expected_milestone_version: milestone.version,
  })
  if (ok) await refreshAfter()
}

/* ----------------------------------------------------------------- entries */
const entryKind = ref('log')
const entryTitle = ref('')
const entryBody = ref('')
const entryMode = ref<'new' | 'existing'>('new')
const entryQuery = ref('')
const noteResults = ref<Resource[]>([])
const existingNote = ref('')

async function createEntry() {
  if (!selected.value) return
  if (await run('entry-create', `/projects/${selected.value.id}/entries`, {
    expected_version: selected.value.version, title: entryTitle.value, body: entryBody.value, entry_kind: entryKind.value,
  })) { entryTitle.value = ''; entryBody.value = ''; await refreshAfter() }
}
async function attachNote() {
  if (!selected.value || !existingNote.value) return
  if (await run('entry-attach', `/projects/${selected.value.id}/entries/attach`, {
    expected_version: selected.value.version, note_id: existingNote.value, entry_kind: entryKind.value,
  })) { existingNote.value = ''; await refreshAfter() }
}

/* ------------------------------------------------------------------- init */
onMounted(async () => {
  await load()
  if (props.initialId) { await detail(props.initialId); try { await api(`/projects/${props.initialId}/opened`, { method: 'POST' }) } catch { /* ignore */ } }
})
watch(() => [props.enabled, status.value, tag.value, trash.value], () => { if (props.enabled) void load(0) })
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="filter-row" style="padding: 0">
        <select v-model="status" aria-label="项目状态"><option value="">全部状态</option><option v-for="s in states" :key="s.value" :value="s.value">{{ s.label }}</option></select>
        <TagFilter v-model="tag" />
        <button class="chip-button" :aria-pressed="!trash" @click="trash = false">项目</button>
        <button class="chip-button" :aria-pressed="trash" @click="trash = true">回收站</button>
      </div>
      <div class="panel-actions">
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="startNew">＋ 新建项目</HubButton>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <template v-if="!selected">
      <p v-if="loading && !page.items.length" class="muted fine-print" role="status">读取中…</p>
      <p v-else-if="!page.items.length" class="empty-state">还没有项目。先建立一个你正在推进的项目。</p>
      <ul v-else class="task-list">
        <li v-for="project in page.items" :key="project.id">
          <div class="task-main">
            <button class="task-title" :disabled="!!project.deleted_at" @click="select(project)">{{ project.title }}</button>
            <div class="task-meta">
              <span>{{ states.find(s => s.value === project.status)?.label }}</span>
              <span>{{ project.progress.done }} / {{ project.progress.total }} 项任务完成</span>
              <span v-if="project.target_date">目标 {{ project.target_date }}</span>
            </div>
          </div>
          <div class="task-actions">
            <button class="plain-action" @click="select(project)">打开</button>
            <button class="plain-action muted" @click="lifecycle(project)">{{ project.deleted_at ? '恢复' : '删除' }}</button>
          </div>
        </li>
      </ul>
      <div v-if="page.total > limit" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!page.offset || loading" @click="load(page.offset - limit)">上一页</HubButton>
        <span class="muted">{{ Math.floor(page.offset / limit) + 1 }} / {{ Math.ceil(page.total / limit) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="page.offset + limit >= page.total || loading" @click="load(page.offset + limit)">下一页</HubButton>
      </div>
    </template>

    <template v-else>
      <div class="panel-toolbar">
        <HubButton variant="quiet" size="sm" @click="backdrop">← 全部项目</HubButton>
        <HubButton variant="secondary" size="sm" @click="startEdit">编辑项目</HubButton>
      </div>

      <h2>{{ selected.title }}</h2>
      <p v-if="selected.description">{{ selected.description }}</p>
      <p v-if="selected.objective" class="fine-print"><strong>完成标准：</strong>{{ selected.objective }}</p>
      <div class="progress-line">
        <span>{{ selected.progress.percent === null ? '暂无任务' : selected.progress.percent + '%' }}</span>
        <span class="muted">{{ selected.progress.done }} / {{ selected.progress.total }} 项 · {{ states.find(s => s.value === selected?.status)?.label }}</span>
      </div>
      <p v-if="archived" class="fine-print">项目已归档，任务与资料保留；调整关联前请先改为进行中。</p>
      <p v-if="selected.repository_url"><a :href="selected.repository_url" target="_blank" rel="noopener noreferrer" class="text-link">打开仓库 ↗</a></p>
      <p class="fine-print">
        本地目录：<code>{{ selected.local_directory || '尚未登记' }}</code>
        <button class="plain-action" @click="checkDirectory">检查</button>
        <span v-if="directoryState" class="muted"> · {{ directoryLabels[directoryState] }}</span>
      </p>

      <div class="chip-row" role="tablist" aria-label="项目视图">
        <button v-for="t in tabs" :key="t.id" role="tab" :aria-selected="tab === t.id" :tabindex="tab === t.id ? 0 : -1" @click="tab = t.id" @keydown="tabKey($event, tabs.map(x => x.id), tab, v => { tab = v as typeof tab })">{{ t.label }}</button>
      </div>

      <template v-if="tab === 'tasks'">
        <p v-if="!tasks.items.length" class="empty-state">还没有项目任务。</p>
        <ul v-else class="task-list">
          <li v-for="task in tasks.items" :key="task.id">
            <button class="task-checkbox" :class="{ checked: task.status === 'done' }" :aria-label="'切换任务状态'" @click="complete(task)">{{ task.status === 'done' ? '✓' : '' }}</button>
            <div class="task-main">
              <router-link :to="'/resource/' + task.id" class="task-title">{{ task.title }}</router-link>
              <div class="task-meta"><span>{{ taskStatus[task.status] }}</span><span>{{ task.planned_date || '未排日期' }}</span></div>
            </div>
            <div class="task-actions">
              <select :value="task.milestone_id || ''" aria-label="里程碑" :disabled="archived" @change="reassign(task, $event)">
                <option value="">无里程碑</option>
                <option v-for="m in activeMilestones" :key="m.id" :value="m.id">{{ m.title }}</option>
              </select>
              <button class="plain-action muted" @click="unlink(task.link_id)">移出项目</button>
            </div>
          </li>
        </ul>
        <div v-if="tasks.total > limit" class="pager">
          <HubButton variant="quiet" size="sm" :disabled="!tasks.offset" @click="tasks.offset -= limit; refreshAfter()">上一页</HubButton>
          <HubButton variant="quiet" size="sm" :disabled="tasks.offset + limit >= tasks.total" @click="tasks.offset += limit; refreshAfter()">下一页</HubButton>
        </div>
        <details v-if="!archived">
          <summary>添加任务</summary>
          <div class="chip-row"><button :aria-selected="taskMode === 'new'" @click="taskMode = 'new'">新建</button><button :aria-selected="taskMode === 'existing'" @click="taskMode = 'existing'">关联已有</button></div>
          <label class="form-field"><span>里程碑</span>
            <select v-model="taskMilestone"><option value="">不分配</option><option v-for="m in activeMilestones" :key="m.id" :value="m.id">{{ m.title }}</option></select>
          </label>
          <form v-if="taskMode === 'new'" @submit.prevent="createTask">
            <label class="form-field"><span>任务名称</span><input v-model="taskTitle" required maxlength="500" @input="markTask" /></label>
            <label class="form-field"><span>要求</span><textarea v-model="taskDescription" maxlength="20000" rows="2" @input="markTask" /></label>
            <div class="form-grid">
              <label class="form-field"><span>计划日</span><input v-model="taskDate" type="date" @input="markTask" /></label>
              <label class="form-field"><span>优先级</span>
                <select v-model="taskPriority" @change="markTask"><option :value="0">无</option><option :value="1">低</option><option :value="2">中</option><option :value="3">高</option></select>
              </label>
            </div>
            <HubButton variant="primary" size="sm" type="submit" :loading="writes.busy.value">创建任务</HubButton>
          </form>
          <div v-else>
            <div class="filter-row">
              <input v-model="taskQuery" placeholder="查找已有任务" maxlength="256" @keydown.enter.prevent="findResources('task', taskQuery)" />
              <HubButton variant="quiet" size="sm" @click="findResources('task', taskQuery)">查找</HubButton>
            </div>
            <select v-model="existingTask" aria-label="已有任务">
              <option value="">选择任务</option>
              <option v-for="t in taskResults" :key="t.id" :value="t.id">{{ t.title }}</option>
            </select>
            <HubButton variant="secondary" size="sm" :disabled="!existingTask" @click="attachTask">加入项目</HubButton>
          </div>
          <p class="fine-print">一个任务最多属于一个项目。</p>
        </details>
      </template>

      <template v-else-if="tab === 'milestones'">
        <div class="panel-actions" style="margin: 12px 0">
          <HubButton variant="primary" size="sm" :disabled="archived" @click="editMilestone()">＋ 里程碑</HubButton>
          <label class="checkbox-field"><input v-model="milestoneTrash" type="checkbox" @change="refreshAfter()" /> 回收站</label>
        </div>
        <p v-if="!milestones.length" class="empty-state">暂无里程碑。验收状态由你明确设置。</p>
        <ul v-else class="task-list">
          <li v-for="milestone in milestones" :key="milestone.id">
            <div class="task-main">
              <strong class="task-title">{{ milestone.title }}</strong>
              <div class="task-meta">
                <span>{{ milestoneStatus[milestone.status] }}</span>
                <span>{{ milestone.target_date || '无目标日' }}</span>
                <span>{{ milestone.progress.done }} / {{ milestone.progress.total }} 项任务</span>
              </div>
              <p v-if="milestone.description" class="task-description">{{ milestone.description }}</p>
            </div>
            <div class="task-actions">
              <button v-if="!milestone.deleted_at" class="plain-action" :disabled="archived" @click="editMilestone(milestone)">编辑</button>
              <button class="plain-action muted" :disabled="archived" @click="toggleMilestone(milestone)">{{ milestone.deleted_at ? '恢复' : '删除' }}</button>
            </div>
          </li>
        </ul>
        <form v-if="milestoneEditing" class="record-editor" @submit.prevent="saveMilestone">
          <fieldset :disabled="writes.busy.value">
            <label class="form-field"><span>里程碑名称</span><input v-model="milestoneForm.title" required maxlength="500" @input="markTask" /></label>
            <label class="form-field"><span>验收要求</span><textarea v-model="milestoneForm.description" maxlength="10000" rows="2" @input="markTask" /></label>
            <div class="form-grid">
              <label class="form-field"><span>目标日期</span><input v-model="milestoneForm.target_date" type="date" @input="markTask" /></label>
              <label class="form-field"><span>状态</span>
                <select v-model="milestoneForm.status" @change="markTask"><option value="todo">待办</option><option value="doing">进行中</option><option value="done">已验收</option></select>
              </label>
            </div>
            <div class="form-actions">
              <HubButton variant="quiet" @click="cancelMilestone">取消</HubButton>
              <HubButton variant="primary" type="submit" :loading="writes.busy.value">保存</HubButton>
            </div>
          </fieldset>
        </form>
      </template>

      <template v-else-if="tab === 'entries'">
        <p v-if="!entries.items.length" class="empty-state">日志、实验和阶段总结都使用现有 Markdown 笔记。</p>
        <ul v-else class="task-list">
          <li v-for="entry in entries.items" :key="entry.id">
            <div class="task-main">
              <router-link :to="'/resource/' + entry.note_id" class="task-title">{{ entry.title }}</router-link>
              <div class="task-meta"><span>{{ entryNames[entry.entry_kind] }}</span><span>{{ new Date(entry.created_at).toLocaleString('zh-CN') }}</span></div>
            </div>
            <div class="task-actions"><button class="plain-action muted" @click="unlink(entry.id)">移出项目</button></div>
          </li>
        </ul>
        <div v-if="entries.total > limit" class="pager">
          <HubButton variant="quiet" size="sm" :disabled="!entries.offset" @click="entries.offset -= limit; refreshAfter()">上一页</HubButton>
          <span class="muted">{{ Math.floor(entries.offset / limit) + 1 }} / {{ Math.ceil(entries.total / limit) }}</span>
          <HubButton variant="quiet" size="sm" :disabled="entries.offset + limit >= entries.total" @click="entries.offset += limit; refreshAfter()">下一页</HubButton>
        </div>
        <details v-if="!archived">
          <summary>添加记录</summary>
          <div class="chip-row"><button :aria-selected="entryMode === 'new'" @click="entryMode = 'new'">撰写</button><button :aria-selected="entryMode === 'existing'" @click="entryMode = 'existing'">关联笔记</button></div>
          <label class="form-field"><span>类型</span>
            <select v-model="entryKind" @change="markTask"><option v-for="(name, key) in entryNames" :key="key" :value="key">{{ name }}</option></select>
          </label>
          <form v-if="entryMode === 'new'" @submit.prevent="createEntry">
            <label class="form-field"><span>标题</span><input v-model="entryTitle" required maxlength="500" @input="markTask" /></label>
            <label class="form-field"><span>正文（Markdown）</span><textarea v-model="entryBody" rows="8" required maxlength="100000" @input="markTask" /></label>
            <HubButton variant="primary" size="sm" type="submit" :loading="writes.busy.value">保存记录</HubButton>
          </form>
          <div v-else>
            <div class="filter-row">
              <input v-model="entryQuery" placeholder="查找已有笔记" maxlength="256" @keydown.enter.prevent="findResources('note', entryQuery)" />
              <HubButton variant="quiet" size="sm" @click="findResources('note', entryQuery)">查找</HubButton>
            </div>
            <select v-model="existingNote" aria-label="已有笔记"><option value="">选择笔记</option><option v-for="n in noteResults" :key="n.id" :value="n.id">{{ n.title }}</option></select>
            <HubButton variant="secondary" size="sm" :disabled="!existingNote" @click="attachNote">关联</HubButton>
          </div>
        </details>
      </template>

      <ToolLauncher v-else-if="tab === 'tools'" :key="selected.id" :project="selected" @draft="launcherDirty = $event" />
      <ResourceContext v-else :key="selected.id" :resource="selected" :relations-read-only="archived" @draft="contextDirty = $event" @changed="refreshAfter" />
    </template>

    <RecordEditor v-if="editorOpen" :key="selected?.id || 'new'" endpoint="/projects" :fields="fields" :record="selected" title="项目基本信息" @draft="d => editorDirty = d" @saved="onSaved" @close="editorOpen = false" />
  </section>
</template>
