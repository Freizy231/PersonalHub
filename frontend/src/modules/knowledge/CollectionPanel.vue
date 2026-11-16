<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import HubDialog from '../../shared/HubDialog.vue'
import { api, ApiError } from '../../shared/api'
import { type Resource, errorMessage, resourceKinds } from '../../shared/resource'
import { useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const emit = defineEmits<{ draft: [boolean] }>()
const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

interface Collection extends Resource { description: string; members: Resource[] }

const list = ref<Resource[]>([])
const trash = ref(false)
const loading = ref(false)
const error = ref('')
const editing = ref(false)
const selectedId = ref('')
const title = ref('')
const description = ref('')
const members = ref<Resource[]>([])
const baseline = ref('')
const pickerQuery = ref('')
const pickerResults = ref<Resource[]>([])

const dirty = ref(false)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

function mark() { dirty.value = true }

async function load() {
  loading.value = true; error.value = ''
  try { list.value = (await latest.track(() => api<{ items: Resource[] }>(`/collections?trash=${trash.value}`))).items }
  catch (reason) { error.value = errorMessage(reason) }
  finally { loading.value = false }
}

async function edit(record?: Resource) {
  try {
    error.value = ''
    if (record) {
      const found = await api<Collection>(`/collections/${record.id}`)
      selectedId.value = found.id; title.value = found.title; description.value = found.description || ''
      members.value = [...found.members]
    } else {
      selectedId.value = ''; title.value = ''; description.value = ''; members.value = []
    }
    baseline.value = JSON.stringify([title.value, description.value, members.value.map(m => m.id)])
    dirty.value = false
    writes.discard()
    editing.value = true
  } catch (reason) { error.value = errorMessage(reason) }
}

async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的集合修改'))) return
  editing.value = false; dirty.value = false; writes.discard()
}

async function find() {
  try {
    pickerResults.value = (await api<{ items: Resource[] }>(`/resources?q=${encodeURIComponent(pickerQuery.value)}&limit=50`)).items
      .filter(r => r.kind !== 'collection' && !members.value.some(m => m.id === r.id))
  } catch (reason) { toast.error(errorMessage(reason)) }
}
function addMember(record: Resource) {
  if (members.value.some(m => m.id === record.id)) return
  members.value.push(record); mark()
}
function move(index: number, delta: number) {
  const target = index + delta
  if (target < 0 || target >= members.value.length) return
  const items = [...members.value]
  ;[items[index], items[target]] = [items[target]!, items[index]!]
  members.value = items; mark()
}

async function save() {
  try {
    const result = await writes.send<Collection>('collection', '/collections' + (selectedId.value ? '/' + selectedId.value : ''), {
      title: title.value, description: description.value,
      members: members.value.map(m => m.id),
      expected_version: selectedId.value ? (list.value.find(r => r.id === selectedId.value)?.version || 0) : 0,
    }, selectedId.value ? 'PUT' : 'POST')
    editing.value = false; dirty.value = false
    toast.push('集合已保存')
    await load()
    void result
  } catch (reason) { toast.error(reason instanceof ApiError && reason.status < 500 ? errorMessage(reason) + ' 草稿已保留。' : errorMessage(reason)) }
}

async function toggle(record: Resource) {
  try {
    await api(`/collections/${record.id}/${record.deleted_at ? 'restore' : 'trash'}`, { method: 'POST', body: JSON.stringify({ expected_version: record.version }) })
    toast.push(record.deleted_at ? '集合已恢复' : '集合已移入回收站')
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

onMounted(load)
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="filter-row" style="padding: 0">
        <button class="chip-button" :aria-pressed="!trash" @click="trash = false; load()">集合</button>
        <button class="chip-button" :aria-pressed="trash" @click="trash = true; load()">回收站</button>
      </div>
      <div class="panel-actions">
        <HubButton variant="primary" size="sm" @click="edit()">＋ 新建集合</HubButton>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted fine-print" role="status">读取中…</p>
    <p v-else-if="!list.length" class="empty-state">把相关资料放进一个有序集合。</p>
    <ul v-else class="task-list">
      <li v-for="record in list" :key="record.id">
        <div class="task-main">
          <button class="task-title" :disabled="!!record.deleted_at" @click="edit(record)">{{ record.title }}</button>
        </div>
        <div class="task-actions">
          <button class="plain-action muted" @click="toggle(record)">{{ record.deleted_at ? '恢复' : '删除' }}</button>
        </div>
      </li>
    </ul>

    <HubDialog :show="editing" :title="selectedId ? '编辑集合' : '新建集合'" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="collection-form" @submit.prevent="save">
        <label class="form-field"><span>集合名称</span><input v-model="title" maxlength="300" required autofocus @input="mark" /></label>
        <label class="form-field"><span>说明</span><textarea v-model="description" maxlength="10000" rows="3" @input="mark" /></label>
        <h4>成员顺序</h4>
        <p v-if="!members.length" class="muted fine-print">还没有成员。</p>
        <ol class="member-list">
          <li v-for="(member, index) in members" :key="member.id">
            <span>{{ member.title }}</span>
            <button type="button" class="plain-action" :disabled="index === 0" @click="move(index, -1)">上移</button>
            <button type="button" class="plain-action" :disabled="index === members.length - 1" @click="move(index, 1)">下移</button>
            <button type="button" class="plain-action muted" @click="members.splice(index, 1); mark()">移除</button>
          </li>
        </ol>
        <div class="filter-row">
          <input v-model="pickerQuery" placeholder="搜索要加入的资料" maxlength="256" @keydown.enter.prevent="find" />
          <HubButton variant="quiet" size="sm" @click="find">查找</HubButton>
        </div>
        <ul class="plain-list">
          <li v-for="record in pickerResults" :key="record.id">
            <span>{{ record.title }} <small class="muted">{{ resourceKinds[record.kind] || '资料' }}</small></span>
            <button type="button" class="plain-action" @click="addMember(record)">加入</button>
          </li>
        </ul>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" @click="save">保存</HubButton>
      </template>
    </HubDialog>
  </section>
</template>
