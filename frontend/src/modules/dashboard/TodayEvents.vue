<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { localDate } from '../life/tasks/types'
import { browserZone, eventLabel, shiftDate, type HubEvent, type Page } from '../life/events/types'

const props = defineProps<{ enabled: boolean }>()
const items = ref<HubEvent[]>([])
const loading = ref(false)
const error = ref('')

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const day = localDate()
    const result = await api<Page<HubEvent>>(`/events?start_date=${day}&end_date=${shiftDate(day, 1)}&timezone=${encodeURIComponent(browserZone())}&limit=10`)
    items.value = result.items
  } catch (reason) { error.value = errorMessage(reason) }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <section class="today-events">
    <div class="task-heading"><h2>今日日程</h2><router-link to="/events" class="plain-action">全部 →</router-link></div>
    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="fine-print">读取中…</p>
    <p v-else-if="!items.length" class="fine-print">今天没有安排。</p>
    <ul v-else class="plain-list">
      <li v-for="row in items" :key="row.id">
        <router-link :to="'/resource/' + row.id" class="text-link">{{ row.title }}</router-link>
        <span class="muted fine-print">{{ eventLabel(row) }}</span>
      </li>
    </ul>
  </section>
</template>
