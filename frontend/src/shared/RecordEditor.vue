<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { confirmDiscard } from './confirm'
import HubButton from './HubButton.vue'
import ResourcePicker from './ResourcePicker.vue'
import { ApiError } from './api'
import { useIdempotent } from './requests'
import { useUnsaved } from './useUnsaved'

export interface Field {
  key: string
  label: string
  type?: 'text' | 'textarea' | 'number' | 'date' | 'datetime-local' | 'select' | 'boolean'
  options?: { value: string; label: string }[]
  nullable?: boolean
  required?: boolean
  min?: number
  max?: number
  default?: unknown
  resourceKind?: string
}

const props = defineProps<{
  endpoint: string
  fields: Field[]
  record?: Record<string, any>
  title?: string
  /** Omit the generic "名称" field for resources that have no `title` (sources, keywords, items). */
  titleless?: boolean
}>()
const emit = defineEmits<{ saved: [Record<string, any>]; close: []; draft: [boolean] }>()

const allFields = computed<Field[]>(() => props.titleless ? props.fields : [{ key: 'title', label: '名称', required: true }, ...props.fields])

const writes = useIdempotent()
const error = ref('')

function initial(): Record<string, any> {
  const values: Record<string, any> = {}
  for (const field of allFields.value) {
    const stored = props.record?.[field.key] ?? field.default
    // Booleans live in a select as 'true'/'false' and go back to real booleans on save.
    values[field.key] = field.type === 'boolean' ? String(stored === true || stored === 1 || stored === 'true') : stored ?? ''
  }
  return values
}

const form = ref<Record<string, any>>(initial())
const baseline = ref(JSON.stringify(form.value))
const dirty = computed(() => JSON.stringify(form.value) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

// Reset when the editor is pointed at a different record or endpoint, or when the saved version moved
// and the person has typed nothing. A re-fetch of the same record (a task ticked elsewhere on the
// page, a list refresh) hands over a new object but must never wipe a draft in progress.
watch(() => [props.record?.id, props.record?.version, props.endpoint] as const, (now, before) => {
  const sameRecord = before && now[0] === before[0] && now[2] === before[2]
  if (sameRecord && dirty.value) return
  form.value = initial()
  baseline.value = JSON.stringify(form.value)
  writes.discard(); error.value = ''
}, { immediate: true })

function coerce(field: Field, raw: unknown) {
  if (field.type === 'boolean') return raw === true || raw === 'true'
  if (field.type === 'number') {
    if (raw === '' || raw === null || raw === undefined) return field.nullable ? null : 0
    return Number(raw)
  }
  if (field.nullable && raw === '') return null
  return raw
}

async function save() {
  if (writes.busy.value) return
  error.value = ''
  const body: Record<string, unknown> = { expected_version: props.record?.version || 0 }
  for (const field of allFields.value) body[field.key] = coerce(field, form.value[field.key])
  try {
    const result = await writes.send<Record<string, any>>('record', props.endpoint + (props.record ? '/' + props.record.id : ''), body, props.record ? 'PUT' : 'POST')
    emit('saved', result)
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.code === 'VERSION_CONFLICT'
      ? '此记录已在别处修改。你的内容仍保留，请关闭后重新读取最新版本。'
      : reason instanceof Error ? reason.message : '保存失败，请保留草稿重试。'
  }
}

function saveShortcut(event: KeyboardEvent) {
  if ((event.ctrlKey || event.metaKey) && !event.isComposing && (event.key.toLowerCase() === 's' || event.key === 'Enter')) {
    event.preventDefault()
    void save()
  }
}

async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的修改'))) return
  emit('close')
}
</script>

<template>
  <form class="record-editor" @submit.prevent="save" @keydown="saveShortcut">
    <h3>{{ title || (record ? '编辑资料' : '新建资料') }}</h3>
    <p class="fine-print">按普通本地资料保存，修改失败会保留草稿。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <fieldset :disabled="writes.busy.value">
      <label v-for="field in allFields" :key="field.key" class="form-field">
        <span>{{ field.label }}<em v-if="field.required"> *</em></span>
        <ResourcePicker v-if="field.resourceKind" v-model="form[field.key]" :kind="field.resourceKind" :disabled="writes.busy.value" />
        <select v-else-if="field.type === 'boolean'" v-model="form[field.key]">
          <option value="true">是</option>
          <option value="false">否</option>
        </select>
        <select v-else-if="field.type === 'select'" v-model="form[field.key]">
          <option v-if="field.nullable" value="">无</option>
          <option v-for="option in field.options" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
        <textarea v-else-if="field.type === 'textarea'" v-model="form[field.key]" rows="3" maxlength="20000" />
        <input
          v-else
          v-model="form[field.key]"
          :type="field.type || 'text'"
          :required="field.required"
          :min="field.min"
          :max="field.max"
          :maxlength="field.key === 'title' ? 500 : 2000"
        />
      </label>
    </fieldset>
    <div class="form-actions">
      <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
      <HubButton variant="primary" type="submit" :loading="writes.busy.value">保存</HubButton>
    </div>
  </form>
</template>
