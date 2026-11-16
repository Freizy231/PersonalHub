<script setup lang="ts">
import { ref } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'

defineProps<{ modelValue: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [string] }>()

const choices = ref<{ id: string; name: string }[]>([])
const error = ref('')
let loaded = false

async function load() {
  if (loaded) return
  try { choices.value = (await api<{ items: typeof choices.value }>('/tags?limit=100')).items; loaded = true }
  catch (reason) { error.value = errorMessage(reason) }
}
</script>

<template>
  <select
    :value="modelValue"
    :disabled="disabled"
    aria-label="按标签筛选"
    @focus="load"
    @change="emit('update:modelValue', ($event.target as HTMLSelectElement).value)"
  >
    <option value="">全部标签</option>
    <option v-for="tag in choices" :key="tag.id" :value="tag.id">#{{ tag.name }}</option>
  </select>
</template>
