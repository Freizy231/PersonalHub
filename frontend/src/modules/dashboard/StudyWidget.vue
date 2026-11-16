<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { type Resource, errorMessage } from '../../shared/resource'

const props = defineProps<{ mode: 'reading' | 'learning' | 'weekly' | 'projects'; enabled: boolean }>()
const error = ref('')
const records = ref<Resource[]>([])
const overview = ref<{ paths: { id: string; title: string; verified: number; total: number }[]; study_seconds: number; verified_assessments_this_week: number }>()

const titles = { reading: '正在阅读', learning: '学习计划', weekly: '本周成果', projects: '最近项目' }

async function load() {
  if (!props.enabled) return
  try {
    if (props.mode === 'reading') {
      const [documents, books] = await Promise.all([
        api<{ items: Resource[] }>('/documents?state=reading&limit=6'),
        api<{ items: Resource[] }>('/catalog/book?limit=30'),
      ])
      records.value = [...documents.items, ...books.items.filter(r => r.read_state === 'reading')].slice(0, 8)
    } else if (props.mode === 'projects') {
      records.value = (await api<{ items: Resource[] }>('/projects?recent=true&limit=6')).items
    } else {
      overview.value = await api('/learning/overview')
    }
  } catch (reason) { error.value = errorMessage(reason) }
}

onMounted(load)
</script>

<template>
  <section class="study-widget">
    <h2>{{ titles[mode] }}</h2>
    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}</p>
    <template v-else-if="mode === 'reading' || mode === 'projects'">
      <ul v-if="records.length">
        <li v-for="record in records" :key="record.id">
          <router-link :to="'/resource/' + record.id" class="text-link">{{ record.title }}</router-link>
          <span v-if="mode === 'reading'" class="muted fine-print"> · {{ record.current_page || 0 }} / {{ record.page_count || record.total_pages || '?' }} 页</span>
        </li>
      </ul>
      <p v-else class="fine-print">{{ mode === 'reading' ? '将一份资料设为"在读"后会出现在这里。' : '打开过的项目会出现在这里。' }}</p>
    </template>
    <template v-else-if="mode === 'learning'">
      <ul v-if="overview?.paths.length">
        <li v-for="path in overview.paths" :key="path.id">
          <router-link :to="'/resource/' + path.id" class="text-link">{{ path.title }}</router-link>
          <span class="muted fine-print"> · {{ path.verified }} / {{ path.total }} 已验收</span>
        </li>
      </ul>
      <p v-else class="fine-print">还没有学习路线。</p>
      <router-link to="/learning" class="plain-action">管理学习计划 →</router-link>
    </template>
    <p v-else class="fine-print">
      本周记录 {{ Math.round((overview?.study_seconds || 0) / 60) }} 分钟，
      {{ overview?.verified_assessments_this_week || 0 }} 次有证据的验收。
    </p>
  </section>
</template>
