<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, watch } from 'vue'
import HubIcon from './HubIcon.vue'
import { api } from './api'
import { resourceKinds, type Resource } from './resource'
import type { PaletteCommand } from './palette'

const props = defineProps<{ open: boolean; commands: PaletteCommand[] }>()
const emit = defineEmits<{ close: []; open: [id: string] }>()

type Row =
  | { type: 'command'; key: string; label: string; hint?: string; icon: string; run: () => void }
  | { type: 'resource'; key: string; label: string; hint: string; icon: string; id: string; snippet: string }

const query = ref('')
const hits = ref<(Resource & { snippet?: string })[]>([])
const busy = ref(false)
const failed = ref(false)
const active = ref(0)
const input = ref<HTMLInputElement>()
const panel = ref<HTMLElement>()
let sequence = 0
let timer: ReturnType<typeof setTimeout> | undefined
let previousFocus: HTMLElement | null = null

const matchedCommands = computed(() => {
  const q = query.value.trim().toLowerCase()
  const list = props.commands.filter(c => !q || `${c.label} ${c.keywords || ''}`.toLowerCase().includes(q))
  return q ? list.slice(0, 6) : list
})

const rows = computed<Row[]>(() => [
  ...matchedCommands.value.map<Row>(c => ({ type: 'command', key: 'c:' + c.id, label: c.label, hint: c.hint, icon: c.icon || 'arrow', run: c.run })),
  ...hits.value.map<Row>(r => ({ type: 'resource', key: 'r:' + r.id, label: r.title, hint: resourceKinds[r.kind] || '资料', icon: 'file', id: r.id, snippet: r.snippet || '' })),
])

function reset() { query.value = ''; hits.value = []; busy.value = false; failed.value = false; active.value = 0; sequence++; clearTimeout(timer) }

async function search() {
  const q = query.value.trim()
  const token = ++sequence
  if (q.length < 2) { hits.value = []; busy.value = false; failed.value = false; return }
  busy.value = true; failed.value = false
  try {
    const result = await api<{ items: (Resource & { snippet?: string })[] }>(`/search?q=${encodeURIComponent(q)}&limit=8`)
    if (token !== sequence) return
    hits.value = result.items
  } catch {
    if (token !== sequence) return
    hits.value = []; failed.value = true
  } finally { if (token === sequence) busy.value = false }
}

watch(query, () => {
  active.value = 0
  clearTimeout(timer)
  timer = setTimeout(() => { void search() }, 180)
})
watch(() => rows.value.length, n => { if (active.value >= n) active.value = Math.max(0, n - 1) })

watch(() => props.open, async open => {
  if (open) {
    previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
    reset()
    await nextTick()
    input.value?.focus()
  } else {
    reset()
    previousFocus?.focus?.()
    previousFocus = null
  }
})
onBeforeUnmount(() => { clearTimeout(timer); sequence++ })

function choose(row: Row | undefined) {
  if (!row) return
  emit('close')
  if (row.type === 'command') row.run()
  else emit('open', row.id)
}

function submitFullSearch() {
  const q = query.value.trim()
  if (!q) return
  emit('close')
  emit('open', '?q=' + encodeURIComponent(q))
}

function onKeydown(event: KeyboardEvent) {
  if (event.key === 'Escape') { event.preventDefault(); emit('close'); return }
  if (event.isComposing) return
  if (event.key === 'ArrowDown') { event.preventDefault(); if (rows.value.length) active.value = (active.value + 1) % rows.value.length }
  else if (event.key === 'ArrowUp') { event.preventDefault(); if (rows.value.length) active.value = (active.value - 1 + rows.value.length) % rows.value.length }
  else if (event.key === 'Enter') {
    event.preventDefault()
    if ((event.ctrlKey || event.metaKey) && query.value.trim()) submitFullSearch()
    else choose(rows.value[active.value])
  } else if (event.key === 'Tab') {
    // Single-control dialog: keep focus on the input rather than leaking to the page behind.
    event.preventDefault(); input.value?.focus()
  }
}

watch(active, async () => {
  await nextTick()
  panel.value?.querySelector<HTMLElement>('[aria-selected="true"]')?.scrollIntoView?.({ block: 'nearest' })
})
</script>

<template>
  <Teleport to="body">
    <div v-if="open" class="palette-overlay" @mousedown.self="emit('close')">
      <div class="palette" role="dialog" aria-modal="true" aria-label="快速跳转与搜索" @keydown="onKeydown">
        <div class="palette-input">
          <HubIcon name="search" :size="16" />
          <input
            ref="input"
            v-model="query"
            type="text"
            role="combobox"
            aria-expanded="true"
            aria-controls="palette-list"
            :aria-activedescendant="rows[active] ? 'palette-row-' + active : undefined"
            aria-label="输入页面名称或搜索关键词"
            placeholder="跳转到页面，或搜索笔记、文献、任务…"
            maxlength="256"
            autocomplete="off"
            spellcheck="false"
          />
          <kbd>Esc</kbd>
        </div>
        <ul id="palette-list" ref="panel" class="palette-list" role="listbox">
          <li
            v-for="(row, index) in rows"
            :id="'palette-row-' + index"
            :key="row.key"
            role="option"
            :aria-selected="index === active"
            :class="{ active: index === active }"
            @mousemove="active = index"
            @click="choose(row)"
          >
            <HubIcon :name="row.icon" :size="15" />
            <span class="palette-main">
              <span class="palette-label">{{ row.label }}</span>
              <span v-if="row.type === 'resource' && row.snippet" class="palette-snippet">{{ row.snippet }}</span>
            </span>
            <span v-if="row.hint" class="palette-hint">{{ row.hint }}</span>
          </li>
          <li v-if="!rows.length && !busy" class="palette-empty" role="presentation">
            {{ failed ? '搜索暂时不可用，请稍后重试。' : query.trim().length < 2 ? '没有匹配的页面。再输入一个字即可搜索资料。' : '没有找到相关资料。' }}
          </li>
        </ul>
        <div class="palette-foot" aria-hidden="true">
          <span><kbd>↑</kbd><kbd>↓</kbd> 选择</span>
          <span><kbd>Enter</kbd> 打开</span>
          <span><kbd>Ctrl</kbd>+<kbd>Enter</kbd> 完整搜索</span>
          <span v-if="busy" class="palette-busy">搜索中…</span>
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.palette-overlay {
  position: fixed;
  inset: 0;
  z-index: 70;
  display: flex;
  align-items: flex-start;
  justify-content: center;
  padding: 12vh var(--space-4) var(--space-4);
  background: var(--overlay);
  animation: palette-fade .12s ease-out;
}
.palette {
  width: min(620px, 100%);
  max-height: 70vh;
  display: flex;
  flex-direction: column;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow);
  overflow: hidden;
}
.palette-input {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: var(--space-3) var(--space-4);
  border-bottom: 1px solid var(--border);
  color: var(--muted);
}
.palette-input input {
  flex: 1;
  min-width: 0;
  padding: 4px 0;
  border: 0;
  border-radius: 0;
  background: none;
  box-shadow: none;
  color: var(--text);
  font-size: var(--step-1);
}
.palette-input input:focus { box-shadow: none; }
.palette-list { list-style: none; margin: 0; padding: var(--space-2); overflow-y: auto; }
.palette-list li[role="option"] {
  display: flex;
  align-items: center;
  gap: var(--space-3);
  padding: 8px var(--space-3);
  border-radius: var(--radius-sm);
  color: var(--text-soft);
  cursor: pointer;
}
.palette-list li.active { background: var(--accent-soft); color: var(--text); }
.palette-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.palette-label { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--step-0); color: var(--text); }
.palette-snippet { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--step--1); color: var(--muted); }
.palette-hint { font-size: var(--step--1); color: var(--muted); white-space: nowrap; }
.palette-empty { padding: var(--space-4); text-align: center; color: var(--muted); font-size: var(--step-0); }
.palette-foot {
  display: flex;
  gap: var(--space-4);
  flex-wrap: wrap;
  padding: var(--space-2) var(--space-4);
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: var(--step--1);
}
.palette-foot kbd { margin-right: 2px; }
.palette-busy { margin-left: auto; }
@keyframes palette-fade { from { opacity: 0; } }
@media (prefers-reduced-motion: reduce) { .palette-overlay { animation: none; } }
</style>
