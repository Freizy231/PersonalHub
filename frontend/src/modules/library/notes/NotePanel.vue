<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import MarkdownView from '../../../shared/MarkdownView.vue'
import TagPanel from '../../tags/TagPanel.vue'
import { api, apiBlob, ApiError } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'
import { confirmAction, confirmDiscard } from '../../../shared/confirm'
import PurgeDialog from '../../../shared/PurgeDialog.vue'
import { localDate } from '../../life/tasks/types'
import type { Tag } from '../../tags/types'
import type { Page } from '../../life/events/types'

interface Note {
  id: string; title: string; note_kind: string; local_date: string | null; body_path: string; content_sha256: string
  tags?: Tag[]; body?: string; version: number; deleted_at: string | null; updated_at: string; storage_state?: string
}
interface Revision { id: string; version: number; title: string; note_kind: string; local_date: string | null; created_at: string; body?: string }

const props = withDefaults(defineProps<{ enabled: boolean; initialId?: string; autoCreate?: boolean; compact?: boolean }>(), { compact: false })
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const kinds = [
  { value: 'quick', label: '随手' },
  { value: 'journal', label: '日记' },
  { value: 'knowledge', label: '知识' },
  { value: 'experiment', label: '实验' },
]
const scope = ref('all')
const offset = ref(0)
const limit = 25
const page = ref<Page<Note>>({ items: [], total: 0, offset: 0, limit })
const loading = ref(false)
const error = ref('')
const reading = ref<Note>()
const tagFilter = ref('')
const tagChoices = ref<Tag[]>([])
let tagsLoaded = false

const readingDirty = computed(() => false)
useUnsaved(() => readingDirty.value)

function message(reason: unknown) { return reason instanceof Error ? reason.message : '操作未完成，请重试。' }

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const query = new URLSearchParams({ scope: scope.value, limit: String(limit), offset: String(offset.value) })
    if (tagFilter.value) query.set('tag_id', tagFilter.value)
    page.value = await latest.track(() => api<Page<Note>>(`/notes?${query}`))
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { loading.value = false }
}

async function read(row: Note) {
  try { reading.value = await latest.track(() => api<Note>(`/notes/${row.id}`)) }
  catch (reason) { if (!isStale(reason)) toast.error(message(reason)) }
}

async function loadTags() {
  if (tagsLoaded) return
  try { tagChoices.value = (await api<Page<Tag>>('/tags?limit=100')).items; tagsLoaded = true }
  catch { /* Tag filtering degrades to "全部". */ }
}

watch(() => [props.enabled, scope.value, tagFilter.value], () => { offset.value = 0; reading.value = undefined; void load() })
onMounted(() => {
  void load()
  if (props.initialId) void api<Note>(`/notes/${props.initialId}`).then(n => { reading.value = n }).catch(e => { error.value = message(e) })
  else if (props.autoCreate) newNote()
})

/* ------------------------------------------------------------------ editor */
const show = ref(false)
const editing = ref<Note>()
const form = ref({ title: '', body: '', note_kind: 'quick', local_date: '' })
const baseline = ref(JSON.stringify(form.value))
const formError = ref('')
const conflict = ref(false)
const dirty = computed(() => show.value && JSON.stringify(form.value) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

function newNote() {
  editing.value = undefined
  form.value = { title: '', body: '', note_kind: 'quick', local_date: localDate() }
  baseline.value = JSON.stringify(form.value)
  writes.discard(); formError.value = ''; conflict.value = false; show.value = true
}

async function edit(row: Note) {
  try {
    const full = await api<Note>(`/notes/${row.id}`)
    editing.value = full
    form.value = { title: full.title, body: full.body || '', note_kind: full.note_kind, local_date: full.local_date || '' }
    baseline.value = JSON.stringify(form.value)
    writes.discard(); formError.value = ''; conflict.value = false; show.value = true
  } catch (reason) { toast.error(message(reason)) }
}

async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('这篇笔记的未保存草稿'))) return
  writes.discard(); show.value = false
}

async function save() {
  if (!form.value.title.trim()) { formError.value = '请填写笔记名称。'; return }
  formError.value = ''
  const fields = {
    title: form.value.title.trim(),
    body: form.value.body,
    note_kind: form.value.note_kind,
    local_date: form.value.local_date || null,
  }
  try {
    if (editing.value) await writes.send<Note>('note', `/notes/${editing.value.id}`, { ...fields, expected_version: editing.value.version, expected_hash: editing.value.content_sha256 }, 'PUT')
    else await writes.send<Note>('note', '/notes', fields, 'POST')
    show.value = false
    toast.push('笔记已保存')
    reading.value = undefined
    await load()
  } catch (reason) {
    formError.value = message(reason)
    conflict.value = reason instanceof ApiError && ['VERSION_CONFLICT', 'NOTE_HASH_CONFLICT', 'NOTE_FILE_CONFLICT', 'NOTE_FILE_MISSING'].includes(reason.code)
  }
}

function saveAsCopy() {
  editing.value = undefined
  writes.discard()
  conflict.value = false
  formError.value = '将以独立新笔记保存，原笔记与修订保持不变。'
}

async function remove(row: Note, action: 'trash' | 'restore') {
  try {
    const updated = await api<Note>(`/notes/${row.id}/${action}`, { method: 'POST', body: JSON.stringify({ expected_version: row.version }) })
    page.value = { ...page.value, items: page.value.items.filter(n => n.id !== row.id), total: Math.max(0, page.value.total - 1) }
    if (reading.value?.id === row.id) reading.value = undefined
    if (action === 'trash') {
      toast.push('笔记已移入回收站', async () => {
        await api(`/notes/${row.id}/restore`, { method: 'POST', body: JSON.stringify({ expected_version: updated.version }) })
        await load()
      })
    } else toast.push('笔记已恢复')
  } catch (reason) { toast.error(message(reason)) }
}

/* ---------------------------------------------------------------- tags */
const tagTarget = ref<Note>()
const tagDirty = ref(false)
const tagBusy = ref(false)
function openTags(row: Note) { tagTarget.value = row; tagDirty.value = false }
async function closeTags() {
  if (tagBusy.value) return
  if (tagDirty.value && !(await confirmDiscard('未保存的标签修改'))) return
  tagTarget.value = undefined; tagDirty.value = false
}
function tagsSaved() { void load(); tagsLoaded = false }

/* ------------------------------------------------------------- history */
const history = ref(false)
const revisions = ref<Page<Revision>>({ items: [], total: 0, offset: 0, limit: 20 })
const selectedRevision = ref<Revision>()
const historyBusy = ref(false)

async function openHistory() {
  if (!editing.value) return
  history.value = true
  await loadHistory()
}
async function loadHistory() {
  if (!editing.value) return
  historyBusy.value = true
  try { revisions.value = await api<Page<Revision>>(`/notes/${editing.value.id}/revisions?limit=20&offset=${revisions.value.offset}`) }
  catch (reason) { toast.error(message(reason)) }
  finally { historyBusy.value = false }
}
async function viewRevision(row: Revision) {
  if (!editing.value) return
  try { selectedRevision.value = await api<Revision>(`/notes/${editing.value.id}/revisions/${row.id}`) }
  catch (reason) { toast.error(message(reason)) }
}
async function useRevision() {
  if (!selectedRevision.value) return
  if (dirty.value && !(await confirmAction('当前草稿中尚未保存的内容将被所选修订替换。', { title: '用历史修订替换草稿？', confirmLabel: '替换草稿', cancelLabel: '保留当前草稿', danger: true }))) return
  form.value = { title: selectedRevision.value.title, body: selectedRevision.value.body || '', note_kind: selectedRevision.value.note_kind, local_date: selectedRevision.value.local_date || '' }
  history.value = false
  formError.value = '历史内容已放入草稿，保存后创建新修订。'
}

function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a'); link.href = url; link.download = filename; link.click()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
async function exportAll() {
  try { download(await apiBlob('/notes/export'), `personal-hub-notes-${localDate()}.zip`) }
  catch (reason) { toast.error(message(reason)) }
}
async function markdown() {
  if (!editing.value) return
  try { download(await apiBlob(`/notes/${editing.value.id}/markdown`), `note-${editing.value.id}.md`) }
  catch (reason) { toast.error(message(reason)) }
}

const purging = ref('')
function purged(id: string) {
  page.value = { ...page.value, items: page.value.items.filter(n => n.id !== id), total: Math.max(0, page.value.total - 1) }
  toast.push('已永久删除')
}

function turn(direction: number) { offset.value = Math.max(0, offset.value + direction * limit); reading.value = undefined; void load() }
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="chip-row" role="group" aria-label="笔记范围">
        <button :aria-pressed="scope === 'all'" @click="scope = 'all'">全部</button>
        <button v-for="kind in kinds" :key="kind.value" :aria-pressed="scope === kind.value" @click="scope = kind.value">{{ kind.label }}</button>
        <button :aria-pressed="scope === 'trash'" @click="scope = 'trash'">回收站</button>
      </div>
      <div class="panel-actions">
        <HubButton variant="quiet" size="sm" @click="exportAll">导出</HubButton>
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="newNote">＋ 写笔记</HubButton>
      </div>
    </div>

    <div v-if="scope !== 'trash'" class="filter-row">
      <select v-model="tagFilter" aria-label="按标签筛选" @focus="loadTags">
        <option value="">全部标签</option>
        <option v-for="t in tagChoices" :key="t.id" :value="t.id">#{{ t.name }}</option>
      </select>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <article v-if="reading" class="note-reading">
      <div class="note-reading-toolbar">
        <button class="plain-action" @click="reading = undefined">← 返回列表</button>
        <span>{{ kinds.find(k => k.value === reading?.note_kind)?.label }}</span>
        <HubButton variant="secondary" size="sm" @click="edit(reading)">编辑</HubButton>
      </div>
      <h2>{{ reading.title }}</h2>
      <div class="note-reading-meta">
        {{ new Date(reading.updated_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) }}
        · 版本 {{ reading.version }}
        <span v-for="tag in reading.tags || []" :key="tag.id"> · #{{ tag.name }}</span>
      </div>
      <MarkdownView :body="reading.body || ''" />
    </article>

    <template v-else>
      <div v-if="loading && !page.items.length" class="empty-state" role="status">正在读取本机笔记…</div>
      <div v-else-if="!page.items.length && !error" class="empty-state">
        <h3>{{ scope === 'trash' ? '回收站为空' : tagFilter ? '此标签下没有笔记' : '还没有笔记' }}</h3>
        <p>{{ scope === 'trash' ? '删除的笔记会保留，可以随时恢复。' : '记下一点想法，之后再慢慢整理。' }}</p>
      </div>
      <ul v-else class="task-list">
        <li v-for="row in page.items" :key="row.id">
          <div class="task-main">
            <button class="task-title" @click="scope === 'trash' ? null : read(row)">{{ row.title }}</button>
            <div class="task-meta">
              <span>{{ kinds.find(k => k.value === row.note_kind)?.label }}</span>
              <span v-if="row.local_date">{{ row.local_date }}</span>
              <span>{{ new Date(row.updated_at).toLocaleDateString('zh-CN') }}</span>
              <span v-for="tag in row.tags || []" :key="tag.id">#{{ tag.name }}</span>
              <span v-if="row.storage_state !== 'ready'" class="overdue">{{ row.storage_state === 'missing' ? '文件缺失，请核对备份' : '文件冲突，请先核对磁盘内容' }}</span>
            </div>
          </div>
          <div class="task-actions">
            <template v-if="scope === 'trash'">
              <button class="plain-action" @click="remove(row, 'restore')">恢复</button>
              <button class="plain-action muted" @click="purging = row.id">永久删除</button>
            </template>
            <template v-else>
              <button class="plain-action" @click="openTags(row)">标签</button>
              <button class="plain-action muted" @click="remove(row, 'trash')">删除</button>
            </template>
          </div>
        </li>
      </ul>
      <div v-if="page.total > limit" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!offset || loading" @click="turn(-1)">上一页</HubButton>
        <span class="muted">{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(page.total / limit) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="offset + limit >= page.total || loading" @click="turn(1)">下一页</HubButton>
      </div>
    </template>

    <HubDialog :show="show" wide :title="editing ? '编辑笔记' : '新建笔记'" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="note-form" @submit.prevent="save">
        <p v-if="formError" class="form-error" role="alert">
          {{ formError }}
          <button v-if="conflict" type="button" class="plain-action" @click="saveAsCopy">另存为新笔记</button>
        </p>
        <label class="form-field"><span>标题</span><input v-model="form.title" maxlength="500" autofocus /></label>
        <div class="form-grid">
          <label class="form-field"><span>类型</span>
            <select v-model="form.note_kind"><option v-for="k in kinds" :key="k.value" :value="k.value">{{ k.label }}</option></select>
          </label>
          <label class="form-field"><span>日期</span><input v-model="form.local_date" type="date" /></label>
        </div>
        <div class="note-editor-split">
          <label class="form-field"><span>正文（Markdown）· {{ form.body.length }} 字</span>
            <textarea v-model="form.body" rows="18" maxlength="200000" spellcheck="false" />
          </label>
          <div class="note-live-preview" aria-label="实时预览">
            <span class="note-live-label">预览</span>
            <MarkdownView v-if="form.body.trim()" :body="form.body" />
            <p v-else class="muted fine-print">输入正文后，这里会显示排版效果。</p>
          </div>
        </div>
        <p class="fine-print">按 <kbd>Ctrl</kbd> <kbd>S</kbd> 保存。保存前内容只在本页，关闭或刷新会提示。</p>
      </form>
      <template #actions>
        <HubButton v-if="editing" variant="quiet" size="sm" @click="markdown">下载 .md</HubButton>
        <HubButton v-if="editing" variant="quiet" size="sm" @click="openHistory">历史</HubButton>
        <span class="spacer" />
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">关闭</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" :disabled="conflict" @click="save">保存</HubButton>
      </template>
    </HubDialog>

    <HubDialog :show="history" title="历史修订" :busy="historyBusy" @update:show="v => { if (!v) history = false }">
      <p class="fine-print">选中旧修订可放入草稿，保存后成为新版本；原记录不回退、不删除。</p>
      <ul class="task-list">
        <li v-for="row in revisions.items" :key="row.id">
          <button class="task-title" @click="viewRevision(row)">v{{ row.version }} · {{ new Date(row.created_at).toLocaleString('zh-CN') }}</button>
        </li>
      </ul>
      <p v-if="!revisions.items.length" class="muted">暂无历史修订。</p>
      <template v-if="selectedRevision">
        <h3>{{ selectedRevision.title }}</h3>
        <pre class="note-history-body">{{ selectedRevision.body }}</pre>
        <HubButton variant="secondary" @click="useRevision">放入草稿</HubButton>
      </template>
    </HubDialog>

    <HubDialog :show="!!tagTarget" title="标签" :busy="tagBusy" @update:show="v => { if (!v) closeTags() }">
      <TagPanel v-if="tagTarget" :key="tagTarget.id" :resource-id="tagTarget.id" @draft="tagDirty = $event" @busy="tagBusy = $event" @saved="tagsSaved" />
    </HubDialog>
    <PurgeDialog :show="!!purging" :resource-id="purging" @update:show="v => { if (!v) purging = '' }" @purged="purged" />
  </section>
</template>

<style scoped>
.note-editor-split { display: grid; grid-template-columns: minmax(0, 1fr); gap: var(--space-4); }
.note-editor-split textarea { min-height: 46vh; line-height: 1.7; font-family: var(--font-mono); font-size: var(--step-0); }
.note-live-preview { display: none; min-width: 0; padding: var(--space-3) var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--bg); overflow: auto; max-height: 60vh; }
.note-live-preview :deep(.reading-prose) { font-size: var(--step-1); line-height: 1.75; }
.note-live-label { display: block; margin-bottom: var(--space-2); font-size: var(--step--1); color: var(--muted); }
@media (min-width: 900px) {
  .note-editor-split { grid-template-columns: minmax(0, 1fr) minmax(0, 1fr); }
  .note-live-preview { display: block; margin-bottom: var(--space-4); }
}
</style>
