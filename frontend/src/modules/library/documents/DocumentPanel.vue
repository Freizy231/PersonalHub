<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { confirmAction, confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import RecordEditor from '../../../shared/RecordEditor.vue'
import ResourceContext from '../../knowledge/ResourceContext.vue'
import TagFilter from '../../tags/TagFilter.vue'
import { api, apiBlob } from '../../../shared/api'
import { type Resource, type Page, catalogFields, label, errorMessage } from '../../../shared/resource'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'

const props = defineProps<{ enabled: boolean; initialId?: string; unifiedSearch?: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

interface ImportItem { file: File; key: string; status: string; error: string; document?: Resource }
interface Profile { id: string; name: string; enabled: number }
interface Preview { id: string; arguments: string[]; title: string; warning: string }

const page = ref<Page<Resource>>({ items: [], total: 0, limit: 30, offset: 0 })
const selected = ref<Resource>()
const editing = ref(false)
const trash = ref(false)
const query = ref('')
const readState = ref('')
const tag = ref('')
const category = ref('')
const busy = ref(false)
const error = ref('')
const jobs = ref<{ id: string; document_id: string; state: string; title: string; error_code: string }[]>([])
const imports = ref<ImportItem[]>([])
const uploading = ref(false)
const importing = ref(false)
const editorDirty = ref(false)
const limit = 30

const profiles = ref<Profile[]>([])
const profileId = ref('')
const preview = ref<Preview>()
const launchNotice = ref('')

useUnsaved(() => editorDirty.value || uploading.value || importing.value, emit)

function message(reason: unknown) { return errorMessage(reason) }

async function load(next = page.value.offset) {
  if (!props.enabled) return
  busy.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ trash: String(trash.value), q: query.value, state: readState.value, offset: String(next) })
    if (tag.value) params.set('tag_id', tag.value)
    if (category.value) params.set('category', category.value)
    const result = await latest.track(() => api<Page<Resource>>(`/documents?${params}`))
    page.value = result
    if (result.total > 0 && result.items.length === 0 && next > 0) { await load(Math.max(0, next - limit)); return }
    const jobResult = await api<{ items: typeof jobs.value }>('/documents/jobs')
    jobs.value = jobResult.items
    if (selected.value && !editorDirty.value) {
      const fresh = page.value.items.find(r => r.id === selected.value?.id)
      if (fresh) selected.value = fresh
    }
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { busy.value = false }
}

function choose(event: Event) {
  const files = Array.from((event.target as HTMLInputElement).files || [])
  if (files.length > 20) { error.value = '每批最多 20 个文件。'; return }
  importing.value = false
  imports.value = files.map(file => ({ file, key: crypto.randomUUID(), status: '待导入', error: '' }))
}

async function upload() {
  if (uploading.value) return
  uploading.value = true; importing.value = true
  let stopped = false
  let controller: AbortController | undefined
  const cancel = () => { stopped = true; controller?.abort() }
  ;(window as unknown as { __hubCancelUpload?: () => void }).__hubCancelUpload = cancel
  try {
    for (const item of imports.value) {
      if (stopped) break
      if (item.document) continue
      if (item.file.size > 100 * 1024 * 1024) { item.status = '失败'; item.error = '超过每文件 100 MiB 上限'; continue }
      item.status = '上传中'; item.error = ''
      controller = new AbortController()
      try {
        const result = await api<{ document: Resource; duplicate: boolean }>(`/documents/import?name=${encodeURIComponent(item.file.name)}`, {
          method: 'POST', body: item.file, headers: { 'Content-Type': contentTypeFor(item.file.name), 'Idempotency-Key': item.key }, signal: controller.signal,
        })
        item.document = result.document
        item.status = result.duplicate ? '已复用原文献' : '已导入，等待提取'
      } catch (reason) { item.status = '失败，结果待核对'; item.error = message(reason) }
    }
    controller = undefined
    importing.value = imports.value.some(item => !item.document)
    await load()
  } finally {
    uploading.value = false
    delete (window as unknown as { __hubCancelUpload?: () => void }).__hubCancelUpload
  }
}

function contentTypeFor(name: string) {
  return name.toLowerCase().endsWith('.epub') ? 'application/epub+zip' : 'application/pdf'
}
function formatLabel(record: { format?: string }) {
  return record.format === 'epub' ? 'EPUB' : 'PDF'
}

async function back() {
  if (editorDirty.value && !(await confirmDiscard('元数据表单里未保存的内容'))) return
  selected.value = undefined; editing.value = false; preview.value = undefined; editorDirty.value = false
}
async function toggleEditing() {
  if (editing.value && editorDirty.value && !(await confirmDiscard('元数据表单里未保存的内容'))) return
  editing.value = !editing.value; editorDirty.value = false
}
function select(record: Resource) { selected.value = record; editing.value = false; preview.value = undefined }
async function onSaved(record: Record<string, unknown>) {
  selected.value = record as Resource; editing.value = false; editorDirty.value = false
  toast.push('已保存')
  await load()
}
async function refreshSelected() {
  if (!selected.value) return
  try { selected.value = await api<Resource>(`/documents/${selected.value.id}`); await load() }
  catch (reason) { toast.error(message(reason)) }
}
async function action(record: Resource, next: string) {
  try {
    await api(`/documents/${record.id}/${next}`, { method: 'POST', body: JSON.stringify({ expected_version: record.version }) })
    if (next === 'trash') selected.value = undefined
    toast.push(next === 'trash' ? '已移入回收站' : '操作已提交')
    await load()
    if (selected.value) await refreshSelected()
  } catch (reason) { toast.error(message(reason)) }
}
async function bookmark(remove = false, pageValue = bookmarkPage.value) {
  if (!selected.value) return
  try {
    selected.value = await api<Resource>(`/documents/${selected.value.id}/bookmark`, {
      method: 'PUT', body: JSON.stringify({ expected_version: selected.value.version, page: Number(pageValue), label: bookmarkLabel.value, remove }),
    })
    bookmarkLabel.value = ''
  } catch (reason) { toast.error(message(reason)) }
}
async function download() {
  if (!selected.value) return
  try {
    const blob = await apiBlob(`/documents/${selected.value.id}/original`)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = String(selected.value.original_name); link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { toast.error(message(reason)) }
}
async function readerPreview() {
  if (!selected.value || !profileId.value) return
  try { preview.value = await api<Preview>('/readers/preview', { method: 'POST', body: JSON.stringify({ document_id: selected.value.id, profile_id: profileId.value }) }) }
  catch (reason) { toast.error(message(reason)) }
}
async function launch() {
  if (!preview.value) return
  const isEpub = selected.value?.format === 'epub'
  if (!(await confirmAction(`将用预览中列出的本机应用打开这个 ${isEpub ? 'EPUB' : 'PDF'}。此操作只启动应用，不会自动标记阅读进度。`, { title: '打开外部阅读器？', confirmLabel: '打开' }))) return
  try {
    const result = await api<{ status: string }>(`/readers/confirm/${preview.value.id}`, { method: 'POST', body: JSON.stringify({ confirmed: true }) })
    launchNotice.value = result.status === 'started' ? '已发起启动，进度请手动记录。' : '启动状态：' + result.status
    preview.value = undefined
  } catch (reason) { toast.error(message(reason)) }
}

let pollTimer: ReturnType<typeof setInterval> | undefined
const bookmarkPage = ref(1)
const bookmarkLabel = ref('')

onMounted(async () => {
  await load()
  try {
    profiles.value = (await api<{ items: Profile[] }>('/readers')).items
    if (props.initialId) selected.value = await api<Resource>(`/documents/${props.initialId}`)
  } catch (reason) { error.value = message(reason) }
  pollTimer = setInterval(() => {
    if (!document.hidden && !editorDirty.value && !editing.value && !uploading.value && jobs.value.some(j => ['queued', 'running'].includes(j.state))) void load()
  }, 5000)
})
onBeforeUnmount(() => { if (pollTimer) clearInterval(pollTimer) })
watch(() => props.enabled, value => { if (value) void load() })
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="panel-actions">
        <label class="upload-trigger">
          <input type="file" accept="application/pdf,.pdf,application/epub+zip,.epub" multiple :disabled="uploading" @change="choose" />
          <span class="hub-upload">选择 PDF / EPUB</span>
        </label>
        <HubButton variant="primary" size="sm" :disabled="uploading || !imports.length || !enabled" :loading="uploading" @click="upload">导入</HubButton>
      </div>
    </div>

    <p class="fine-print">支持 PDF 与 EPUB。原件只保存在本机，每份 100 MiB，单批最多 20 个文件。</p>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <ul v-if="imports.length" class="import-list">
      <li v-for="item in imports" :key="item.key">
        <span>{{ item.file.name }}</span>
        <span class="muted">{{ item.status }}</span>
        <span v-if="item.error" class="error-text">{{ item.error }}</span>
      </li>
    </ul>

    <template v-if="!selected">
      <form class="filter-row" @submit.prevent="load(0)">
        <input v-if="!unifiedSearch" v-model="query" placeholder="按标题查找" maxlength="256" />
        <select v-model="readState" aria-label="阅读状态">
          <option value="">全部状态</option>
          <option v-for="s in catalogFields.document.find(f => f.key === 'read_state')?.options" :key="s.value" :value="s.value">{{ s.label }}</option>
        </select>
        <TagFilter v-model="tag" />
        <label class="checkbox-field"><input v-model="trash" type="checkbox" @change="load(0)" /> 回收站</label>
        <HubButton variant="secondary" size="sm" :disabled="busy">筛选</HubButton>
      </form>

      <p v-if="!page.items.length && !busy && !error" class="empty-state">{{ trash ? '回收站为空。' : '还没有文献，点右上角选择 PDF 或 EPUB 导入。' }}</p>
      <ul v-else class="task-list">
        <li v-for="record in page.items" :key="record.id">
          <div class="task-main">
            <button class="task-title" :disabled="!!record.deleted_at" :title="record.deleted_at ? '已在回收站，请先恢复' : undefined" @click="select(record)">{{ record.title }}</button>
            <div class="task-meta">
              <span class="format-badge">{{ formatLabel(record) }}</span>
              <span>{{ label(record.read_state) }}</span>
              <span>{{ label(record.extraction_status) }}</span>
              <span>{{ record.current_page }} / {{ record.page_count || '?' }} {{ record.format === 'epub' ? '章' : '页' }}</span>
            </div>
          </div>
          <div class="task-actions">
            <button class="plain-action muted" @click="action(record, record.deleted_at ? 'restore' : 'trash')">{{ record.deleted_at ? '恢复' : '删除' }}</button>
          </div>
        </li>
      </ul>
      <div v-if="page.total > limit" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!page.offset || busy" @click="load(page.offset - limit)">上一页</HubButton>
        <span class="muted">{{ Math.floor(page.offset / limit) + 1 }} / {{ Math.ceil(page.total / limit) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="page.offset + limit >= page.total || busy" @click="load(page.offset + limit)">下一页</HubButton>
      </div>
    </template>

    <section v-else-if="!selected.deleted_at" class="record-details">
      <div class="panel-toolbar">
        <HubButton variant="quiet" size="sm" @click="back">← 返回</HubButton>
        <div class="panel-actions">
          <button class="plain-action" @click="download">下载原件</button>
          <HubButton variant="secondary" size="sm" @click="toggleEditing">{{ editing ? '收起编辑' : '编辑' }}</HubButton>
        </div>
      </div>
      <h2>{{ selected.title }}</h2>
      <p class="fine-print">
        {{ formatLabel(selected) }} · {{ label(selected.extraction_status) }} ·
        {{ selected.page_count || '未知' }} {{ selected.format === 'epub' ? '章' : '页' }} ·
        {{ Math.ceil(Number(selected.size) / 1024) }} KiB
      </p>
      <p class="fine-print">原始元数据：{{ selected.metadata_title || '无标题' }} / {{ selected.metadata_author || '无作者' }}</p>
      <p v-if="selected.abstract">{{ selected.abstract }}</p>

      <RecordEditor
        v-if="editing"
        :endpoint="'/documents'"
        :record="selected"
        :fields="catalogFields.document || []"
        title="编辑元数据与进度"
        @draft="d => editorDirty = d"
        @saved="onSaved"
        @close="editing = false"
      />

      <template v-else>
        <div class="panel-actions" style="margin: 12px 0">
          <HubButton variant="secondary" size="sm" @click="action(selected, 'retry')">重试提取</HubButton>
          <HubButton variant="quiet" size="sm" @click="action(selected, 'cancel')">取消提取</HubButton>
        </div>

        <details>
          <summary>书签 {{ (selected.bookmarks as unknown[])?.length ? `(${(selected.bookmarks as unknown[]).length})` : '' }}</summary>
          <ul class="plain-list">
            <li v-for="b in (selected.bookmarks as { id: string; page: number; label: string }[])" :key="b.id">
              <span>第 {{ b.page }} {{ selected.format === 'epub' ? '章' : '页' }} · {{ b.label }}</span>
              <button class="plain-action" @click="bookmark(true, b.page)">解除</button>
            </li>
          </ul>
          <form class="inline-form" @submit.prevent="bookmark()">
            <input v-model="bookmarkPage" type="number" min="1" :max="Number(selected.page_count) || undefined" required aria-label="书签页码" />
            <input v-model="bookmarkLabel" maxlength="500" placeholder="书签备注" />
            <HubButton variant="secondary" size="sm" :disabled="!selected.page_count">保存</HubButton>
          </form>
        </details>

        <details>
          <summary>用本机阅读器打开</summary>
          <p v-if="selected.format === 'epub'" class="fine-print">EPUB 可用本机阅读器打开，但进度仍需手动记录。</p>
          <p v-if="!profiles.length" class="fine-print">先在设置中登记已安装的阅读器。</p>
          <div class="filter-row">
            <select v-model="profileId" aria-label="阅读器">
              <option value="">选择阅读器</option>
              <option v-for="p in profiles.filter(p => p.enabled)" :key="p.id" :value="p.id">{{ p.name }}</option>
            </select>
            <HubButton variant="secondary" size="sm" :disabled="!profileId" @click="readerPreview">预览启动</HubButton>
          </div>
          <section v-if="preview" class="preview-box">
            <p>{{ preview.warning }}</p>
            <pre>{{ preview.arguments.join('\n') }}</pre>
            <div class="form-actions">
              <HubButton variant="quiet" @click="preview = undefined">取消</HubButton>
              <HubButton variant="primary" @click="launch">确认启动</HubButton>
            </div>
          </section>
          <p v-if="launchNotice" class="fine-print">{{ launchNotice }}</p>
        </details>

        <ResourceContext :key="selected.id" :resource="selected" @draft="d => editorDirty = d" @changed="refreshSelected" />
      </template>
    </section>

    <details v-if="jobs.length">
      <summary>最近提取任务</summary>
      <ul class="plain-list">
        <li v-for="job in jobs" :key="job.id">{{ job.title }} · {{ label(job.state) }} <span class="error-text">{{ job.error_code }}</span></li>
      </ul>
    </details>
  </section>
</template>

<style scoped>
.upload-trigger { display: inline-flex; }
.upload-trigger input { position: absolute; width: 1px; height: 1px; opacity: 0; }
.hub-upload {
  display: inline-flex;
  align-items: center;
  padding: 5px 10px;
  border: 1px solid var(--border-strong);
  border-radius: var(--radius-sm);
  background: var(--surface);
  color: var(--text);
  font-size: var(--step--1);
  font-weight: 500;
  cursor: pointer;
}
.hub-upload:hover { border-color: var(--accent); color: var(--accent); }
.import-list { list-style: none; padding: 0; margin: 0 0 var(--space-3); font-size: var(--step--1); }
.import-list li { display: flex; gap: var(--space-3); flex-wrap: wrap; padding: 3px 0; }
</style>
