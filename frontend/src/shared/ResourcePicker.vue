<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { api } from './api'
import { type Resource, errorMessage } from './resource'

const props = defineProps<{ modelValue?: string | null; kind: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const query = ref('')
const items = ref<Resource[]>([])
const total = ref(0)
const offset = ref(0)
const busy = ref(false)
const error = ref('')
const retained = ref<Resource>()
const pageSize = 50
let alive = true
let sequence = 0

async function load(next = 0) {
  const token = ++sequence
  busy.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ kind: props.kind, q: query.value, limit: String(pageSize), offset: String(next) })
    const page = await api<{ items: Resource[]; total?: number }>(`/resources?${params}`)
    if (!alive || token !== sequence) return
    items.value = page.items
    total.value = page.total ?? page.items.length
    offset.value = next
  } catch (reason) {
    if (alive && token === sequence) error.value = errorMessage(reason)
  } finally {
    if (alive && token === sequence) busy.value = false
  }
}

function choose(event: Event) {
  const id = (event.target as HTMLSelectElement).value
  retained.value = items.value.find(r => r.id === id) || retained.value
  emit('update:modelValue', id)
}

onMounted(() => {
  void load()
  if (!props.modelValue) return
  api<Resource>(`/resources/${props.modelValue}`)
    .then(r => { if (alive && r.kind === props.kind) retained.value = r })
    .catch(() => { /* Keep the stored id without disclosing an unavailable record. */ })
})
onBeforeUnmount(() => { alive = false; sequence++ })
</script>

<template>
  <div class="resource-picker">
    <div class="resource-picker-query">
      <input v-model="query" :disabled="disabled" aria-label="按标题查找" placeholder="按标题查找" maxlength="256" @keydown.enter.prevent="load(0)" />
      <button type="button" class="plain-action" :disabled="disabled || busy" @click="load(0)">查找</button>
    </div>
    <select :value="modelValue || ''" :disabled="disabled || busy" :aria-label="kind === 'task' ? '关联任务' : '关联资料'" @change="choose">
      <option value="">不关联</option>
      <option v-if="modelValue && !items.some(r => r.id === modelValue)" :value="modelValue">{{ retained?.id === modelValue ? retained.title : '保留当前关联' }}</option>
      <option v-for="r in items" :key="r.id" :value="r.id">{{ r.title }}</option>
    </select>
    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}，原有关联保持。</p>
    <p v-else-if="!busy && !items.length" class="fine-print">暂无匹配项。</p>
    <div v-if="total > pageSize" class="pager">
      <button type="button" class="plain-action" :disabled="disabled || busy || !offset" @click="load(offset - pageSize)">上一页</button>
      <span class="muted">{{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(total / pageSize) }}</span>
      <button type="button" class="plain-action" :disabled="disabled || busy || offset + pageSize >= total" @click="load(offset + pageSize)">下一页</button>
    </div>
  </div>
</template>
