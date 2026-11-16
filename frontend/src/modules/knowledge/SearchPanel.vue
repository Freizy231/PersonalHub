<script setup lang="ts">
import { onMounted, ref } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import { api } from '../../shared/api'
import { type Resource, errorMessage, resourceKinds } from '../../shared/resource'
import { isStale, useLatest } from '../../shared/requests'
import { useToast } from '../../shared/toast'

const props = withDefaults(defineProps<{ initialQuery?: string; initialKind?: string; unifiedSearch?: boolean }>(), { initialQuery: '', initialKind: '', unifiedSearch: false })

const toast = useToast()
const latest = useLatest()

const query = ref(props.initialQuery)
const kind = ref(props.initialKind)
const result = ref<{ items: (Resource & { snippet: string })[]; total: number; offset: number; limit: number; truncated?: boolean }>({ items: [], total: 0, offset: 0, limit: 20 })
const busy = ref(false)
const error = ref('')
const searched = ref(false)
const input = ref<HTMLInputElement>()
const submitted = ref({ q: '', kind: '' })
const rebuilding = ref(false)

async function search(offset = 0) {
  const criteria = offset ? submitted.value : { q: query.value.trim(), kind: kind.value }
  if (!criteria.q) { error.value = '请输入关键词后搜索。'; return }
  busy.value = true; error.value = ''
  try {
    result.value = await latest.track(() => api<typeof result.value>(`/search?q=${encodeURIComponent(criteria.q)}&kind=${encodeURIComponent(criteria.kind)}&offset=${offset}`))
    submitted.value = { ...criteria }
    searched.value = true
  } catch (reason) {
    if (!isStale(reason)) error.value = errorMessage(reason)
  } finally { busy.value = false }
}

async function rebuild() {
  if (rebuilding.value) return
  rebuilding.value = true; error.value = ''
  try {
    const response = await api<{ indexed: number; skipped: number }>('/search/rebuild', { method: 'POST' })
    toast.push(`已索引 ${response.indexed} 项，跳过 ${response.skipped} 项`)
    if (query.value.trim()) await search()
  } catch (reason) { error.value = errorMessage(reason) }
  finally { rebuilding.value = false }
}

onMounted(() => {
  if (!props.unifiedSearch) input.value?.focus()
  if (query.value.trim()) void search()
})
</script>

<template>
  <section class="search-workspace" :aria-busy="busy">
    <div class="eyebrow">搜索</div>
    <h1>{{ unifiedSearch && initialQuery ? `「${initialQuery}」的结果` : '搜索个人资料' }}</h1>
    <p class="muted fine-print">笔记、文献、书籍与项目。私密库与账本不在搜索范围。</p>

    <form v-if="!unifiedSearch" class="filter-row" @submit.prevent="search()">
      <input ref="input" v-model="query" placeholder="输入关键词" maxlength="256" aria-label="搜索关键词" />
      <select v-model="kind" aria-label="资源类型">
        <option value="">全部类型</option>
        <option v-for="(name, id) in resourceKinds" :key="id" :value="id">{{ name }}</option>
      </select>
      <HubButton variant="secondary" size="sm" :disabled="busy">搜索</HubButton>
    </form>
    <p v-else-if="!initialQuery" class="fine-print">在右上角输入关键词，或按 <kbd>⌘K</kbd>。</p>

    <p v-if="error" class="panel-error" role="alert">
      {{ error }}
      <button class="plain-action" :disabled="busy" @click="search()">重试</button>
    </p>
    <p v-if="busy" class="fine-print" role="status">正在搜索…</p>
    <p v-if="searched" class="search-meta" role="status">
      共 {{ result.total }} 项{{ result.truncated ? '（已达候选上限，请缩小范围）' : '' }}
    </p>

    <ul v-if="result.items.length" class="search-results">
      <li v-for="item in result.items" :key="item.id">
        <router-link :to="'/resource/' + item.id" class="text-link">{{ item.title }}</router-link>
        <small class="muted">{{ resourceKinds[item.kind] || '资料' }}</small>
        <p>{{ item.snippet }}</p>
      </li>
    </ul>
    <p v-else-if="searched && !busy && !error" class="empty-state">没有匹配的资料。试试更短的关键词，或去掉类型筛选。</p>

    <div v-if="result.total > result.limit" class="pager">
      <HubButton variant="quiet" size="sm" :disabled="!result.offset || busy" @click="search(result.offset - result.limit)">上一页</HubButton>
      <span class="muted">{{ Math.floor(result.offset / result.limit) + 1 }} / {{ Math.ceil(result.total / result.limit) }}</span>
      <HubButton variant="quiet" size="sm" :disabled="result.offset + result.limit >= result.total || busy" @click="search(result.offset + result.limit)">下一页</HubButton>
    </div>

    <details class="search-maintenance">
      <summary>搜不到已有资料？</summary>
      <p class="fine-print">更新本机搜索索引不会更改原文。短关键词使用字面匹配，长词使用全文检索。</p>
      <HubButton variant="secondary" size="sm" :loading="rebuilding" @click="rebuild">更新索引</HubButton>
    </details>
  </section>
</template>
