<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import { api, ApiError } from '../../shared/api'
import { useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'
import { defaults, internalTargets, safeTarget as checkTarget, usable, widgetNames, type Preferences, type Shortcut, type WidgetId } from './types'
import TodayMeals from '../life/diet/TodayMeals.vue'
import StudyWidget from './StudyWidget.vue'
import IntelWidget from '../intelligence/IntelWidget.vue'
import TodayStrip from './TodayStrip.vue'

const greeting = computed(() => {
  const h = new Date().getHours()
  return h < 5 ? '夜深了' : h < 11 ? '早上好' : h < 14 ? '中午好' : h < 18 ? '下午好' : '晚上好'
})
const today = new Intl.DateTimeFormat('zh-CN', { year: 'numeric', month: 'long', day: 'numeric', weekday: 'long' }).format(new Date())

const captureText = ref('')
const captureBusy = ref(false)
const captureDirty = ref(false)

async function capture() {
  if (!captureText.value.trim()) return
  captureBusy.value = true
  try {
    await writes.send('capture', '/notes', {
      title: captureText.value.trim().slice(0, 60),
      body: captureText.value,
      note_kind: 'quick',
      local_date: new Date().toLocaleDateString('sv-SE'),
    })
    captureText.value = ''
    captureDirty.value = false
    toast.push('已保存为笔记')
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '保存失败') }
  finally { captureBusy.value = false }
}

const props = withDefaults(defineProps<{ enabled: boolean; mode?: 'home' | 'settings' }>(), { mode: 'home' })
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const writes = useIdempotent()

const saved = ref<Preferences>(defaults())
const draft = ref<Preferences>(defaults())
const loaded = ref(false)
const loading = ref(false)
const editing = ref(false)
const error = ref('')
const childDirty = ref<Record<string, boolean>>({})

const dirty = computed(() => JSON.stringify(draft.value) !== JSON.stringify(saved.value))
const contentDirty = computed(() => Object.values(childDirty.value).some(Boolean))
const guarded = computed(() => dirty.value || contentDirty.value || writes.hasPending())
useUnsaved(() => guarded.value || captureDirty.value, emit)

const visible = computed(() => saved.value.widgets.filter(w => w.enabled))

function setChildDraft(id: string, value: boolean) {
  if (childDirty.value[id] === value) return
  childDirty.value = { ...childDirty.value, [id]: value }
}

function clone(value: Preferences): Preferences { return JSON.parse(JSON.stringify(value)) }

async function load() {
  if (!props.enabled || loading.value) return
  if (contentDirty.value) { error.value = '请先保存或关闭进行中的草稿，再重新读取首页。'; return }
  loading.value = true; error.value = ''
  try {
    const result = await api<Preferences>('/dashboard/preferences')
    if (!usable(result)) throw new Error('首页配置格式不受支持。现有资料不受影响。')
    saved.value = result
    draft.value = clone(result)
    loaded.value = true
    if (result.state === 'invalid') error.value = '首页配置异常，正使用默认布局。原配置已保留，暂不允许覆盖。'
  } catch (reason) { error.value = reason instanceof Error ? reason.message : '首页设置暂不可用。' }
  finally { loading.value = false }
}

function edit() {
  if (!loaded.value || saved.value.state === 'invalid') return
  if (contentDirty.value) { toast.error('请先保存或关闭进行中的草稿。'); return }
  childDirty.value = {}
  draft.value = clone(saved.value)
  editing.value = true
  error.value = ''
}

function cancel() { draft.value = clone(saved.value); editing.value = false; writes.discard() }

async function save() {
  if (saved.value.state === 'invalid') { toast.error('原配置异常，暂不允许覆盖。'); return }
  try {
    await writes.send<Preferences>('dashboard', '/dashboard/preferences', { ...draft.value, expected_version: saved.value.version }, 'PUT')
    saved.value = clone(draft.value)
    editing.value = false
    toast.push('首页布局已保存')
  } catch (reason) {
    if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') { toast.error('首页设置已在别处修改，请重新读取。'); }
    else toast.error(reason instanceof Error ? reason.message : '保存失败')
  }
}

function toggleWidget(id: WidgetId, value: boolean) {
  draft.value.widgets = draft.value.widgets.map(w => w.id === id ? { ...w, enabled: value } : w)
}
function move(id: WidgetId, delta: number) {
  const index = draft.value.widgets.findIndex(w => w.id === id)
  const target = index + delta
  if (index < 0 || target < 0 || target >= draft.value.widgets.length) return
  const items = [...draft.value.widgets]
  ;[items[index], items[target]] = [items[target]!, items[index]!]
  draft.value.widgets = items
}
function reset() { draft.value = { ...defaults(), version: draft.value.version } }

function addShortcut() {
  if (draft.value.shortcuts.length >= 12) return
  draft.value.shortcuts = [...draft.value.shortcuts, { id: crypto.randomUUID(), label: '', kind: 'internal', target: '/tasks' }]
}
function removeShortcut(id: string) { draft.value.shortcuts = draft.value.shortcuts.filter(s => s.id !== id) }
function updateShortcut(id: string, patch: Partial<Shortcut>) {
  draft.value.shortcuts = draft.value.shortcuts.map(s => s.id === id ? { ...s, ...patch } : s)
}

onMounted(load)
watch(() => props.enabled, value => { if (value) void load() })
</script>

<template>
  <section class="panel">
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <!-- home view: pure projection, no configuration controls -->
    <template v-if="mode === 'home'">
      <header class="home-head">
        <h1>{{ greeting }}</h1>
        <p class="muted">{{ today }}</p>
      </header>
      <TodayStrip :enabled="enabled" />
      <div class="dashboard-grid">
        <div v-for="widget in visible" :key="widget.id" class="dashboard-widget" :data-widget="widget.id">
          <template v-if="widget.id === 'capture'">
            <h2>快速记录</h2>
            <form @submit.prevent="capture">
              <label class="form-field"><span>记下一点想法</span>
                <textarea v-model="captureText" rows="3" maxlength="20000" placeholder="随手写，保存为普通笔记" @input="captureDirty = !!captureText.trim()" />
              </label>
              <div class="form-actions">
                <HubButton variant="primary" size="sm" type="submit" :loading="captureBusy" :disabled="!captureText.trim()">保存笔记</HubButton>
                <router-link to="/notes" class="quiet-link">全部笔记 →</router-link>
              </div>
            </form>
          </template>
          <template v-else-if="widget.id === 'tasks'">
            <TodayTasks :enabled="enabled" compact @draft="v => setChildDraft('tasks', v)" />
          </template>
          <template v-else-if="widget.id === 'events'">
            <TodayEvents :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'reminders'">
            <ReminderInbox :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'shortcuts'">
            <h2>快捷入口</h2>
            <ul class="plain-list" v-if="saved.shortcuts.length">
              <li v-for="shortcut in saved.shortcuts" :key="shortcut.id">
                <router-link v-if="shortcut.kind === 'internal'" :to="shortcut.target" class="text-link">{{ shortcut.label }}</router-link>
                <a v-else-if="checkTarget(shortcut.kind, shortcut.target)" :href="checkTarget(shortcut.kind, shortcut.target)!" target="_blank" rel="noopener noreferrer" class="text-link">{{ shortcut.label }} ↗</a>
                <span v-else class="muted">{{ shortcut.label }}</span>
              </li>
            </ul>
            <p v-else class="muted fine-print">还没有快捷入口。可在设置中添加。</p>
          </template>
          <template v-else-if="widget.id === 'meals'">
            <TodayMeals :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'reading'">
            <StudyWidget mode="reading" :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'learning'">
            <StudyWidget mode="learning" :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'weekly'">
            <StudyWidget mode="weekly" :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'projects'">
            <StudyWidget mode="projects" :enabled="enabled" />
          </template>
          <template v-else-if="widget.id === 'intelligence'">
            <IntelWidget :enabled="enabled" />
          </template>
          <template v-else>
            <h2>{{ (widgetNames as Record<string,string>)[widget.id] }}</h2>
            <p class="muted fine-print">此组件暂无内容。</p>
          </template>
        </div>
      </div>
      <p v-if="!visible.length" class="empty-state">首页暂未启用组件。可在设置中开启。</p>
    </template>

    <!-- settings view -->
    <template v-else>
      <div class="panel-toolbar">
        <h2>首页组件</h2>
        <div class="panel-actions">
          <HubButton v-if="!editing" variant="secondary" size="sm" :disabled="!loaded" @click="edit">调整布局</HubButton>
          <HubButton v-if="editing" variant="quiet" size="sm" @click="reset">恢复默认</HubButton>
          <HubButton v-if="editing" variant="quiet" size="sm" @click="cancel">取消</HubButton>
          <HubButton v-if="editing" variant="primary" size="sm" :loading="writes.busy.value" :disabled="!dirty" @click="save">保存布局</HubButton>
        </div>
      </div>

      <ul class="widget-settings">
        <li v-for="(widget, index) in (editing ? draft : saved).widgets" :key="widget.id">
          <label class="checkbox-field">
            <input type="checkbox" :checked="widget.enabled" :disabled="!editing" @change="toggleWidget(widget.id, ($event.target as HTMLInputElement).checked)" />
            {{ widgetNames[widget.id] }}
          </label>
          <div v-if="editing" class="widget-order">
            <button class="plain-action" :disabled="index === 0" @click="move(widget.id, -1)">↑</button>
            <button class="plain-action" :disabled="index === draft.widgets.length - 1" @click="move(widget.id, 1)">↓</button>
          </div>
        </li>
      </ul>

      <h3>快捷入口</h3>
      <p v-if="!(editing ? draft : saved).shortcuts.length" class="muted fine-print">还没有快捷入口。</p>
      <ul class="plain-list">
        <li v-for="shortcut in (editing ? draft : saved).shortcuts" :key="shortcut.id">
          <template v-if="editing">
            <input :value="shortcut.label" placeholder="名称" maxlength="40" @input="updateShortcut(shortcut.id, { label: ($event.target as HTMLInputElement).value })" />
            <select :value="shortcut.kind" @change="updateShortcut(shortcut.id, { kind: ($event.target as HTMLSelectElement).value as 'internal' | 'web', target: ($event.target as HTMLSelectElement).value === 'internal' ? '/tasks' : 'https://' })">
              <option value="internal">站内</option>
              <option value="web">外部链接</option>
            </select>
            <select v-if="shortcut.kind === 'internal'" :value="shortcut.target" @change="updateShortcut(shortcut.id, { target: ($event.target as HTMLSelectElement).value })">
              <option v-for="target in internalTargets" :key="target.target" :value="target.target">{{ target.label }}</option>
            </select>
            <input v-else :value="shortcut.target" placeholder="https://" maxlength="2048" @input="updateShortcut(shortcut.id, { target: ($event.target as HTMLInputElement).value })" />
            <button class="plain-action muted" @click="removeShortcut(shortcut.id)">移除</button>
          </template>
          <template v-else>
            <router-link v-if="shortcut.kind === 'internal'" :to="shortcut.target" class="text-link">{{ shortcut.label || '未命名' }}</router-link>
            <a v-else-if="checkTarget(shortcut.kind, shortcut.target)" :href="checkTarget(shortcut.kind, shortcut.target)!" target="_blank" rel="noopener noreferrer" class="text-link">{{ shortcut.label || shortcut.target }}</a>
            <span v-else class="muted">{{ shortcut.label || '无效链接' }}</span>
          </template>
        </li>
      </ul>
      <HubButton v-if="editing" variant="secondary" size="sm" :disabled="draft.shortcuts.length >= 12" @click="addShortcut">＋ 快捷入口</HubButton>
    </template>
  </section>
</template>

<script lang="ts">
import { defineAsyncComponent } from 'vue'
const TodayTasks = defineAsyncComponent(() => import('../life/tasks/TaskPanel.vue'))
const TodayEvents = defineAsyncComponent(() => import('./TodayEvents.vue'))
const ReminderInbox = defineAsyncComponent(() => import('../life/reminders/ReminderPanel.vue'))
export default {}
</script>

<style scoped>
.widget-settings { list-style: none; padding: 0; margin: 0 0 var(--space-4); }
.widget-settings li { display: flex; align-items: center; justify-content: space-between; gap: var(--space-3); padding: var(--space-2) 0; border-bottom: 1px solid var(--border); }
.widget-settings .checkbox-field { margin: 0; }
.widget-order { display: flex; gap: var(--space-1); }
.widget-order .plain-action { padding: 0 6px; font-size: var(--step-1); }
</style>
