<script setup lang="ts">
import { confirmDiscard } from '../../shared/confirm'
import DocumentPanel from './documents/DocumentPanel.vue'
import CatalogPanel from './CatalogPanel.vue'
import NotePanel from './notes/NotePanel.vue'
import CollectionPanel from '../knowledge/CollectionPanel.vue'

const props = withDefaults(defineProps<{ enabled: boolean; initialTab?: string }>(), { initialTab: 'document' })
const emit = defineEmits<{ draft: [boolean]; tabChange: [string] }>()

const tabs = [
  { id: 'document', name: '文献' },
  { id: 'book', name: '书籍' },
  { id: 'bookmark', name: '网页' },
  { id: 'course', name: '课程' },
  { id: 'note', name: '笔记' },
  { id: 'collection', name: '集合' },
] as const

const tab = ref(tabs.some(t => t.id === props.initialTab) ? props.initialTab : 'document')
const dirty = ref(false)

function draft(value: boolean) { dirty.value = value; emit('draft', value) }
async function change(next: string) {
  if (next === tab.value) return
  if (dirty.value && !(await confirmDiscard('当前资料的未保存修改'))) return
  tab.value = next; draft(false); emit('tabChange', next)
}

/* WAI-ARIA tabs: arrow keys move between tabs, only the selected tab is in the Tab order. */
const tabButtons = ref<HTMLButtonElement[]>([])
function arrow(event: KeyboardEvent, index: number) {
  const last = tabs.length - 1
  const target = event.key === 'ArrowRight' ? (index + 1) % tabs.length
    : event.key === 'ArrowLeft' ? (index - 1 + tabs.length) % tabs.length
    : event.key === 'Home' ? 0 : event.key === 'End' ? last : -1
  if (target < 0) return
  event.preventDefault()
  tabButtons.value[target]?.focus()
  void change(tabs[target]!.id)
}
</script>

<script lang="ts">
import { ref, watch } from 'vue'
export default {}
</script>

<template>
  <div class="chip-row" role="tablist" aria-label="资料类型">
    <button
      v-for="(t, index) in tabs"
      :key="t.id"
      :ref="el => { if (el) tabButtons[index] = el as HTMLButtonElement }"
      role="tab"
      :aria-selected="tab === t.id"
      :tabindex="tab === t.id ? 0 : -1"
      @click="change(t.id)"
      @keydown="arrow($event, index)"
    >{{ t.name }}</button>
  </div>

  <DocumentPanel v-if="tab === 'document'" :enabled="enabled" unified-search @draft="draft" />
  <NotePanel v-else-if="tab === 'note'" :enabled="enabled" @draft="draft" />
  <CollectionPanel v-else-if="tab === 'collection'" @draft="draft" />
  <CatalogPanel v-else :key="tab" :kind="tab as 'book' | 'bookmark' | 'course'" :enabled="enabled" unified-search @draft="draft" />
</template>
