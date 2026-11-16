<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../shared/api'

const props = defineProps<{ enabled: boolean }>()
const items = ref<{ id: string; title: string; url: string; source_key: string }[]>([])
const error = ref('')

onMounted(async () => {
  if (!props.enabled) return
  try { items.value = (await api<{ items: typeof items.value }>('/intelligence/items?limit=5')).items }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '情报暂不可用。' }
})
</script>

<template>
  <section class="study-widget">
    <h2>技术情报</h2>
    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}</p>
    <p v-else-if="!items.length" class="fine-print">暂无已采集情报。</p>
    <ul v-else>
      <li v-for="item in items" :key="item.id">
        <a :href="item.url" target="_blank" rel="noopener noreferrer" class="text-link">{{ item.title }} ↗</a>
        <span class="muted fine-print"> · {{ item.source_key }}</span>
      </li>
    </ul>
    <router-link to="/intelligence" class="plain-action">打开情报中心 →</router-link>
  </section>
</template>
