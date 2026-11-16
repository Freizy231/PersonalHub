<script setup lang="ts">
import { onMounted, ref, watch } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import HubDialog from '../../shared/HubDialog.vue'
import RecordEditor from '../../shared/RecordEditor.vue'
import { confirmDiscard } from '../../shared/confirm'
import ResourceContext from '../knowledge/ResourceContext.vue'
import TagFilter from '../tags/TagFilter.vue'
import { api, ApiError } from '../../shared/api'
import { type Resource, type Page, catalogFields, label, errorMessage } from '../../shared/resource'
import { isStale, useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const props = defineProps<{ kind: 'book' | 'bookmark' | 'course'; enabled: boolean; initialId?: string; unifiedSearch?: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const names = { book: '书籍', bookmark: '网页收藏', course: '课程' } as const
const page = ref<Page<Resource>>({ items: [], total: 0, limit: 30, offset: 0 })
const query = ref('')
const category = ref('')
const tag = ref('')
const trash = ref(false)
const loading = ref(false)
const error = ref('')
const selected = ref<Resource>()
const editorOpen = ref(false)
const editorDirty = ref(false)
async function closeEditor() {
  if (writes.busy.value) return
  if (editorDirty.value && !(await confirmDiscard('未保存的修改'))) return
  editorOpen.value = false; editorDirty.value = false
}
const limit = 30

useUnsaved(() => editorDirty.value || writes.hasPending(), emit)

async function load(next = page.value.offset) {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ q: query.value, trash: String(trash.value), offset: String(next), limit: String(limit) })
    if (tag.value) params.set('tag_id', tag.value)
    if (category.value) params.set('category', category.value)
    page.value = await latest.track(() => api<Page<Resource>>(`/catalog/${props.kind}?${params}`))
  } catch (reason) {
    if (!isStale(reason)) error.value = errorMessage(reason)
  } finally { loading.value = false }
}

function open(record?: Resource) { selected.value = record; editorOpen.value = true }
function saved(record: Record<string, any>) {
  editorOpen.value = false
  selected.value = record as Resource
  editorDirty.value = false
  toast.push('已保存')
  void load()
}
async function toggle(record: Resource) {
  try {
    await api(`/catalog/${props.kind}/${record.id}/${record.deleted_at ? 'restore' : 'trash'}`, {
      method: 'POST', body: JSON.stringify({ expected_version: record.version }),
    })
    if (selected.value?.id === record.id) selected.value = undefined
    page.value = { ...page.value, items: page.value.items.filter(r => r.id !== record.id), total: Math.max(0, page.value.total - 1) }
    toast.push(record.deleted_at ? '已恢复' : '已移入回收站')
  } catch (reason) { toast.error(errorMessage(reason)) }
}
async function refreshSelected() {
  if (!selected.value) return
  try { selected.value = await api<Resource>(`/catalog/${props.kind}/${selected.value.id}`); void load() }
  catch (reason) { toast.error(errorMessage(reason)) }
}

onMounted(async () => {
  await load()
  if (props.initialId) {
    try { selected.value = await api<Resource>(`/catalog/${props.kind}/${props.initialId}`) }
    catch (reason) { error.value = errorMessage(reason) }
  }
})
watch(() => props.kind, () => { selected.value = undefined; editorOpen.value = false; void load(0) })

const fields = catalogFields[props.kind] || []
function tagsOf(record: Resource): { id: string; name: string }[] {
  return (record.tags as { id: string; name: string }[] | undefined) || []
}
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="panel-actions">
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="open()">＋ 新建</HubButton>
      </div>
    </div>

    <template v-if="!selected">
      <form class="filter-row" @submit.prevent="load(0)">
        <input v-if="!unifiedSearch" v-model="query" :placeholder="'查找' + names[kind]" maxlength="256" />
        <TagFilter v-model="tag" />
        <input v-if="kind !== 'course'" v-model="category" placeholder="分类" maxlength="100" />
        <label class="checkbox-field"><input v-model="trash" type="checkbox" @change="load(0)" /> 回收站</label>
        <HubButton variant="secondary" size="sm" :disabled="loading">筛选</HubButton>
      </form>

      <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
      <p v-if="loading && !page.items.length" class="muted fine-print" role="status">读取中…</p>
      <p v-else-if="!page.items.length && !error" class="empty-state">{{ trash ? '回收站为空。' : '还没有' + names[kind] + '，点右上角新建。' }}</p>
      <ul v-else class="task-list">
        <li v-for="record in page.items" :key="record.id">
          <div class="task-main">
            <button class="task-title" @click="selected = record">{{ record.title }}</button>
            <div class="task-meta">
              <span>{{ label(record.read_state || record.status) }}</span>
              <span v-if="record.progress_percent != null">{{ record.progress_percent }}%</span>
              <span v-for="t in tagsOf(record)" :key="t.id">#{{ t.name }}</span>
            </div>
          </div>
          <div class="task-actions">
            <button class="plain-action" @click="open(record)">编辑</button>
            <button class="plain-action muted" @click="toggle(record)">{{ record.deleted_at ? '恢复' : '删除' }}</button>
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
        <HubButton variant="quiet" size="sm" @click="selected = undefined">← 返回</HubButton>
        <HubButton variant="secondary" size="sm" @click="open(selected)">编辑</HubButton>
      </div>
      <h2>{{ selected.title }}</h2>
      <dl class="status-list">
        <div v-for="field in fields" v-show="selected[field.key] != null && selected[field.key] !== ''" :key="field.key">
          <dt>{{ field.label }}</dt>
          <dd>
            <a v-if="field.key === 'url'" :href="String(selected[field.key])" target="_blank" rel="noopener noreferrer" class="text-link">{{ selected[field.key] }} ↗</a>
            <span v-else>{{ label(selected[field.key]) }}</span>
          </dd>
        </div>
      </dl>
      <ResourceContext :key="selected.id" :resource="selected" @draft="d => editorDirty = d" @changed="refreshSelected" />
    </template>

    <HubDialog :show="editorOpen" :title="selected ? '编辑' + names[kind] : '新建' + names[kind]" :busy="writes.busy.value" @update:show="v => { if (!v) void closeEditor() }">
      <RecordEditor
        :key="selected?.id || 'new'"
        :endpoint="'/catalog/' + kind"
        :fields="fields"
        :record="selected"
        @draft="d => editorDirty = d"
        @saved="saved"
        @close="editorOpen = false"
      />
    </HubDialog>
  </section>
</template>
