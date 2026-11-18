<script setup lang="ts">
import { computed, defineAsyncComponent, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import HubIcon from './shared/HubIcon.vue'
import HubButton from './shared/HubButton.vue'
import HubToast from './shared/HubToast.vue'
import HubDialog from './shared/HubDialog.vue'
import ConfirmHost from './shared/ConfirmHost.vue'
import CommandPalette from './shared/CommandPalette.vue'
import type { PaletteCommand } from './shared/palette'
import { api, connectSession, type SystemState } from './shared/api'
import { confirmDiscard } from './shared/confirm'
import { applyTheme, resolveDark, saveTheme, storedTheme, type ThemeChoice } from './shared/theme'

const DashboardPanel = defineAsyncComponent(() => import('./modules/dashboard/DashboardPanel.vue'))
const TaskPanel = defineAsyncComponent(() => import('./modules/life/tasks/TaskPanel.vue'))
const EventPanel = defineAsyncComponent(() => import('./modules/life/events/EventPanel.vue'))
const NotePanel = defineAsyncComponent(() => import('./modules/library/notes/NotePanel.vue'))
const VaultPanel = defineAsyncComponent(() => import('./modules/library/notes/VaultPanel.vue'))
const DietPanel = defineAsyncComponent(() => import('./modules/life/diet/DietPanel.vue'))
const FinancePanel = defineAsyncComponent(() => import('./modules/life/finance/FinancePanel.vue'))
const ExercisePanel = defineAsyncComponent(() => import('./modules/life/exercise/ExercisePanel.vue'))
const LearningPanel = defineAsyncComponent(() => import('./modules/learning/LearningPanel.vue'))
const LibraryPanel = defineAsyncComponent(() => import('./modules/library/LibraryPanel.vue'))
const ProjectPanel = defineAsyncComponent(() => import('./modules/projects/ProjectPanel.vue'))
const IntelligencePanel = defineAsyncComponent(() => import('./modules/intelligence/IntelligencePanel.vue'))
const AssistantPanel = defineAsyncComponent(() => import('./modules/assistant/AssistantPanel.vue'))
const WorkspacePanel = defineAsyncComponent(() => import('./modules/assistant/WorkspacePanel.vue'))
const AiPermissions = defineAsyncComponent(() => import('./modules/assistant/AiPermissions.vue'))
const SearchPanel = defineAsyncComponent(() => import('./modules/knowledge/SearchPanel.vue'))
const ResourcePage = defineAsyncComponent(() => import('./modules/knowledge/ResourcePage.vue'))
const ToolSettings = defineAsyncComponent(() => import('./modules/projects/ToolSettings.vue'))
const MaintenancePanel = defineAsyncComponent(() => import('./modules/knowledge/MaintenancePanel.vue'))

const route = useRoute()
const router = useRouter()

/* ---------------------------------------------------------------- drafts */
const draftOwners = new Set<string>()
const draftCount = ref(0)
function setDraft(owner: string, value: boolean) {
  if (value) draftOwners.add(owner)
  else draftOwners.delete(owner)
  draftCount.value = draftOwners.size
}
const hasDraft = computed(() => draftCount.value > 0)
const removeGuard = router.beforeEach(async (to, from) => {
  if (to.fullPath === from.fullPath || !hasDraft.value) return true
  // Moving within the same page (query changes only) is not a reason to interrupt editing.
  if (to.path === from.path) return true
  return confirmDiscard('当前页面有未保存的修改，离开后')
})
onBeforeUnmount(removeGuard)

/* -------------------------------------------------------------- shortcuts */
const searchInput = ref<HTMLInputElement>()
const paletteOpen = ref(false)
const helpOpen = ref(false)
const typing = (event: KeyboardEvent) => !!(event.target as HTMLElement | null)?.closest?.('input, textarea, select, [contenteditable]')
function shortcut(event: KeyboardEvent) {
  if (event.isComposing) return
  const mod = event.ctrlKey || event.metaKey
  if (mod && event.key.toLowerCase() === 'k') {
    event.preventDefault(); helpOpen.value = false; paletteOpen.value = !paletteOpen.value
  } else if (mod && event.shiftKey && event.key.toLowerCase() === 'n') {
    event.preventDefault(); paletteOpen.value = false; void router.push('/notes?new=1')
  } else if (event.key === '/' && !mod && !typing(event) && !paletteOpen.value) {
    event.preventDefault(); searchInput.value?.focus(); searchInput.value?.select()
  } else if (event.key === '?' && !mod && !typing(event) && !paletteOpen.value) {
    event.preventDefault(); helpOpen.value = !helpOpen.value
  } else if (event.key === 'Escape' && helpOpen.value) {
    helpOpen.value = false
  }
}
function openFromPalette(target: string) {
  if (target.startsWith('?q=')) { void router.push('/search' + target); return }
  void router.push('/resource/' + target)
}

/* ------------------------------------------------------------------ theme */
const theme = ref<ThemeChoice>(storedTheme())
const systemDark = window.matchMedia('(prefers-color-scheme: dark)')
const prefersDark = ref(systemDark.matches)
function systemThemeChanged(event: MediaQueryListEvent) { prefersDark.value = event.matches }
const isDark = computed(() => resolveDark(theme.value, prefersDark.value))
function setTheme(value: ThemeChoice) { theme.value = value; saveTheme(value) }
watch(isDark, applyTheme, { immediate: true })

/* ----------------------------------------------------------------- search */
const searchQuery = ref(typeof route.query.q === 'string' ? route.query.q : '')
const searchKind = ref(typeof route.query.kind === 'string' ? route.query.kind : '')
watch(() => route.query.q, v => { searchQuery.value = typeof v === 'string' ? v : '' })
watch(() => route.query.kind, v => { searchKind.value = typeof v === 'string' ? v : '' })
function globalSearch() {
  void router.push({ path: '/search', query: { ...(searchQuery.value.trim() ? { q: searchQuery.value.trim() } : {}), ...(searchKind.value ? { kind: searchKind.value } : {}) } })
}

/* ------------------------------------------------------------------ clock */
const now = ref(new Date())
let clock: ReturnType<typeof setInterval> | undefined
const dateLabel = computed(() => new Intl.DateTimeFormat('zh-CN', { month: 'long', day: 'numeric', weekday: 'long' }).format(now.value))

/* --------------------------------------------------------------- nav model */
type NavItem = { path: string; label: string; symbol: string; exact?: boolean }
const groups: { label: string; items: NavItem[] }[] = [
  { label: '今天', items: [
    { path: '/', label: '首页', symbol: 'home', exact: true },
    { path: '/tasks', label: '任务', symbol: 'check' },
    { path: '/events', label: '日程', symbol: 'calendar' },
  ] },
  { label: '生活', items: [
    { path: '/notes', label: '笔记', symbol: 'note' },
    { path: '/diet', label: '饮食', symbol: 'coffee' },
    { path: '/finance', label: '财务', symbol: 'wallet' },
    { path: '/exercise', label: '运动', symbol: 'run' },
    { path: '/vault', label: '私密库', symbol: 'lock' },
  ] },
  { label: '知识', items: [
    { path: '/learning', label: '学习', symbol: 'learning' },
    { path: '/library', label: '资料库', symbol: 'library' },
    { path: '/projects', label: '项目', symbol: 'projects' },
  ] },
  { label: '洞察', items: [
    { path: '/intelligence', label: '情报', symbol: 'intelligence' },
    { path: '/ai', label: 'AI 助手', symbol: 'ai' },
  ] },
]
const aiView = computed(() => (String(route.query.view || 'workspace') === 'ask' ? 'ask' : 'workspace'))
function isActive(item: NavItem) {
  if (item.exact) return route.path === '/'
  return route.path === item.path || route.path.startsWith(item.path + '/')
}
const currentLabel = computed(() => {
  if (route.path === '/search') return '搜索'
  if (route.path.startsWith('/resource/')) return '资料详情'
  if (route.path === '/settings') return '设置'
  for (const group of groups) for (const item of group.items) if (isActive(item)) return item.label
  return '我的空间'
})

const headings: Record<string, { title: string; hint: string }> = {
  '/tasks': { title: '任务', hint: '安排今天，也给之后留出位置。' },
  '/events': { title: '日程', hint: '时间看得见，安排才落得下。' },
  '/notes': { title: '笔记', hint: '想法留在本机，随时能翻回来看。' },
  '/vault': { title: '私密库', hint: '独立加密，不进入搜索、AI 或普通导出。' },
  '/diet': { title: '饮食', hint: '食谱、三餐、采购与库存分开记录。' },
  '/finance': { title: '财务', hint: '只在本机记账，不进入搜索或 AI。' },
  '/exercise': { title: '运动', hint: '计划与实际分开记录。' },
  '/learning': { title: '学习', hint: '目标、路线与知识点，用证据确认掌握。' },
  // 资料库自己的页签已经说明分类，页头不再重复一遍。
  '/library': { title: '资料库', hint: '' },
  '/projects': { title: '项目', hint: '任务、里程碑与日志放在同一处。' },
  '/intelligence': { title: '情报', hint: '来源、关键词与趋势，只在你点击时采集。' },
  '/ai': { title: 'AI 助手', hint: '可选；每次发送前先预览并确认。' },
}
const pageHeading = computed(() => headings[route.path])

const commands = computed<PaletteCommand[]>(() => [
  ...groups.flatMap(g => g.items.map<PaletteCommand>(item => ({
    id: 'go:' + item.path, label: item.label, hint: g.label, icon: item.symbol, keywords: item.path.replace('/', ''),
    run: () => { void router.push(item.path) },
  }))),
  { id: 'new-note', label: '记录想法', hint: 'Ctrl ⇧ N', icon: 'plus', keywords: 'new note 新建 笔记', run: () => { void router.push('/notes?new=1') } },
  { id: 'new-task', label: '新建任务', hint: '任务', icon: 'plus', keywords: 'new task 添加 待办', run: () => { void router.push('/tasks?new=1') } },
  { id: 'settings', label: '设置', icon: 'settings', keywords: 'settings 主题 备份', run: () => { void router.push('/settings') } },
  { id: 'theme', label: isDark.value ? '切换到亮色主题' : '切换到暗色主题', icon: isDark.value ? 'sun' : 'moon', keywords: 'theme dark light 主题', run: () => setTheme(isDark.value ? 'light' : 'dark') },
  { id: 'help', label: '键盘快捷键', hint: '?', icon: 'terminal', keywords: 'shortcuts help 帮助', run: () => { helpOpen.value = true } },
])

/* ------------------------------------------------------------------ state */
const state = ref<SystemState>()
const loading = ref(true)
const error = ref('')
const connected = ref(false)
const launchFilename = `launch-${window.location.port || '8765'}.url`
const databaseLabel = computed(() => ({
  ready: '本地资料空间已准备好',
  upgrade_required: '资料库需要升级到当前版本',
  not_initialized: '尚未初始化个人资料空间',
  version_mismatch: '数据版本不匹配，已保持只读',
  unavailable: '资料空间暂不可用',
} as Record<string, string>)[state.value?.database || 'not_initialized'])
const enabled = computed(() => state.value?.database === 'ready')

async function refresh() {
  loading.value = true; error.value = ''
  try { state.value = await api<SystemState>('/system') }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '暂时无法读取本机状态。' }
  finally { loading.value = false }
}

function protectPage(event: BeforeUnloadEvent) { if (hasDraft.value) { event.preventDefault(); event.returnValue = '' } }

onMounted(async () => {
  window.addEventListener('keydown', shortcut)
  window.addEventListener('beforeunload', protectPage)
  window.addEventListener('focus', refreshClock)
  systemDark.addEventListener('change', systemThemeChanged)
  clock = setInterval(refreshClock, 30000)
  const fragment = new URLSearchParams(window.location.hash.slice(1))
  const token = fragment.get('bootstrap') || undefined
  if (token) history.replaceState(null, '', window.location.pathname + window.location.search)
  try { await connectSession(token); connected.value = true; await refresh() }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '无法建立本机会话。'; loading.value = false }
})
function refreshClock() { now.value = new Date() }
onBeforeUnmount(() => {
  window.removeEventListener('keydown', shortcut)
  window.removeEventListener('beforeunload', protectPage)
  window.removeEventListener('focus', refreshClock)
  systemDark.removeEventListener('change', systemThemeChanged)
  if (clock) clearInterval(clock)
})
</script>

<template>
  <div class="hub" :class="{ dark: isDark }">
    <a class="skip-link" href="#main-content">跳到主要内容</a>

    <aside class="sidebar">
      <router-link to="/" class="brand">
        <span class="brand-mark" aria-hidden="true">P</span>
        <span>Personal<span class="brand-word"> Hub</span><small>YOUR SPACE, YOUR PACE.</small></span>
      </router-link>

      <nav aria-label="主导航">
        <div v-for="group in groups" :key="group.label" class="nav-group">
          <div class="nav-label">{{ group.label }}</div>
          <router-link
            v-for="item in group.items"
            :key="item.path"
            :to="item.path"
            :class="{ active: isActive(item) }"
            :aria-current="isActive(item) ? 'page' : undefined"
            :title="item.label"
          >
            <HubIcon :name="item.symbol" :size="16" />
            <span class="nav-name">{{ item.label }}</span>
            <span v-if="isActive(item)" class="nav-active-dot" aria-hidden="true" />
          </router-link>
        </div>
      </nav>

      <router-link to="/notes?new=1" class="sidebar-capture">
        <HubIcon name="plus" :size="15" /> 记录想法 <kbd>Ctrl ⇧ N</kbd>
      </router-link>

      <div class="sidebar-foot">
        <div class="local-indicator"><span class="status-dot" /> 本地优先</div>
        <router-link to="/settings" class="nav-settings" :class="{ active: route.path === '/settings' }">
          <HubIcon name="settings" :size="15" /> 设置
        </router-link>
      </div>
    </aside>

    <div class="workspace">
      <header class="topbar">
        <div class="breadcrumb"><strong>{{ currentLabel }}</strong></div>
        <div class="topbar-right">
          <form class="global-search" role="search" @submit.prevent="globalSearch">
            <HubIcon name="search" :size="15" />
            <input ref="searchInput" v-model="searchQuery" type="search" aria-label="查找个人资料" placeholder="搜索资料…" maxlength="256" />
            <select v-model="searchKind" aria-label="查找范围">
              <option value="">全部</option>
              <option value="note">笔记</option>
              <option value="document">文献</option>
              <option value="book">书籍</option>
              <option value="bookmark">网页</option>
              <option value="course">课程</option>
              <option value="task">任务</option>
              <option value="project">项目</option>
              <option value="knowledge_node">知识点</option>
              <option value="recipe">食谱</option>
            </select>
            <kbd class="search-hotkey">⌘K</kbd>
          </form>
          <span class="topbar-date">{{ dateLabel }}</span>
          <button class="theme-button" :aria-label="isDark ? '切换到亮色主题' : '切换到暗色主题'" @click="setTheme(isDark ? 'light' : 'dark')">
            <HubIcon :name="isDark ? 'sun' : 'moon'" :size="16" />
          </button>
        </div>
      </header>

      <main id="main-content" tabindex="-1">
        <div v-if="error" class="error-banner" role="alert">
          {{ error }}
          <button v-if="connected" type="button" class="plain-action" @click="refresh">重试</button>
        </div>

        <section v-if="!connected && !loading" class="access-panel">
          <div class="eyebrow">仅限本机访问</div>
          <h1>打开你的个人空间</h1>
          <p>请从本机启动入口打开 Personal Hub。入口只用于建立本机会话，不会把资料发送到其他设备。</p>
          <p class="muted">启动服务后，在数据目录的 config 文件夹中打开 {{ launchFilename }} 内的地址。入口失效时重新启动服务即可。</p>
        </section>

        <template v-else-if="connected">
          <!-- The top bar already says which page this is; a second h1 would only repeat it.
               A page keeps a heading only when it has something to add (an action or a hint). -->
          <div v-if="pageHeading && pageHeading.hint" class="page-heading"><div><h1>{{ pageHeading.title }}</h1><p class="muted">{{ pageHeading.hint }}</p></div></div>
          <DashboardPanel v-if="route.path === '/'" :enabled="enabled" @draft="setDraft('dashboard', $event)" />

          <template v-else-if="route.path === '/tasks'">
            <TaskPanel :key="route.query.new === '1' ? 'new' : 'list'" :enabled="enabled" :auto-create="route.query.new === '1'" @draft="setDraft('tasks', $event)" />
          </template>

          <template v-else-if="route.path === '/events'">
            <EventPanel :enabled="enabled" @draft="setDraft('events', $event)" />
          </template>

          <template v-else-if="route.path === '/notes'">
            <NotePanel :enabled="enabled" :auto-create="route.query.new === '1'" @draft="setDraft('notes', $event)" />
          </template>

          <template v-else-if="route.path === '/vault'">
            <VaultPanel :enabled="enabled" @draft="setDraft('vault', $event)" />
          </template>

          <template v-else-if="route.path === '/diet'">
            <DietPanel :enabled="enabled" @draft="setDraft('diet', $event)" />
          </template>

          <template v-else-if="route.path === '/finance'">
            <FinancePanel :enabled="enabled" @draft="setDraft('finance', $event)" />
          </template>

          <template v-else-if="route.path === '/exercise'">
            <ExercisePanel :enabled="enabled" @draft="setDraft('exercise', $event)" />
          </template>

          <template v-else-if="route.path === '/learning'">
            <LearningPanel :enabled="enabled" @draft="setDraft('learning', $event)" />
          </template>

          <template v-else-if="route.path === '/library'">
            <LibraryPanel :enabled="enabled" :initial-tab="String(route.query.view || 'document')" @tab-change="v => router.replace({ path: '/library', query: { view: v } })" @draft="setDraft('library', $event)" />
          </template>

          <template v-else-if="route.path === '/projects'">
            <ProjectPanel :enabled="enabled" @draft="setDraft('projects', $event)" />
          </template>

          <template v-else-if="route.path === '/intelligence'">
            <IntelligencePanel :enabled="enabled" @draft="setDraft('intelligence', $event)" />
          </template>

          <template v-else-if="route.path === '/ai'">
            <div class="chip-row" role="tablist" aria-label="AI 视图">
              <button class="chip-button" role="tab" :aria-selected="aiView === 'workspace'"
                :tabindex="aiView === 'workspace' ? 0 : -1"
                @click="router.replace({ path: '/ai', query: {} })">学习工作台</button>
              <button class="chip-button" role="tab" :aria-selected="aiView === 'ask'"
                :tabindex="aiView === 'ask' ? 0 : -1"
                @click="router.replace({ path: '/ai', query: { view: 'ask' } })">基于资料提问</button>
            </div>
            <WorkspacePanel v-if="aiView === 'workspace'" :enabled="enabled" @draft="setDraft('ai', $event)" />
            <AssistantPanel v-else :enabled="enabled" @draft="setDraft('ai', $event)" />
          </template>

          <template v-else-if="route.path === '/search'">
            <SearchPanel :key="route.fullPath" unified-search :initial-query="String(route.query.q || '')" :initial-kind="String(route.query.kind || '')" />
          </template>

          <template v-else-if="route.path.startsWith('/resource/')">
            <ResourcePage :key="route.path" :id="route.path.split('/')[2] || ''" :enabled="enabled" @draft="setDraft('resource', $event)" />
          </template>

          <template v-else-if="route.path === '/settings'">
            <div class="page-heading"><div><h1>设置</h1><p class="muted">本机状态、外观与维护。</p></div>
              <HubButton variant="secondary" :loading="loading" @click="refresh">刷新状态</HubButton>
            </div>
            <section v-if="state?.database === 'upgrade_required'" class="upgrade-guide">
              <h2>启用当前版本需要升级资料库</h2>
              <p>先停止新服务，再在项目根目录执行以下命令。升级会先备份并在副本演练，不修改旧情报库。</p>
              <pre>backend/.venv/bin/python -B -m backend.manage upgrade --data-root runtime/personal-hub</pre>
              <p>完成后重新启动服务并使用新的本机入口。请勿重复初始化数据库。</p>
            </section>
            <section class="settings-section">
              <h2>外观</h2>
              <div class="setting-row">
                <div><strong>主题</strong><p>跟随系统，或固定为亮色 / 暗色。</p></div>
                <div class="theme-options">
                  <HubButton v-for="option in [{ v: 'system', n: '跟随系统' }, { v: 'light', n: '亮色' }, { v: 'dark', n: '暗色' }]" :key="option.v" :variant="theme === option.v ? 'primary' : 'secondary'" size="sm" @click="setTheme(option.v)">{{ option.n }}</HubButton>
                </div>
              </div>
            </section>
            <section class="settings-section"><DashboardPanel mode="settings" :enabled="enabled" @draft="setDraft('dashboard', $event)" /></section>
            <AssistantPanel v-if="enabled" mode="settings" :enabled="true" @draft="setDraft('ai', $event)" />
            <AiPermissions v-if="enabled" />
            <MaintenancePanel v-if="enabled" />
            <ToolSettings v-if="enabled" @draft="setDraft('tools', $event)" />
            <section class="settings-section">
              <h2>资料与隐私</h2>
              <dl class="status-list">
                <div><dt>个人资料空间</dt><dd>{{ databaseLabel }}</dd></div>
                <div><dt>技术情报</dt><dd>保留原数据库，独立运行</dd></div>
                <div><dt>私密库</dt><dd><router-link class="text-link" to="/vault">设置密码、解锁与锁定</router-link></dd></div>
                <div><dt>备份</dt><dd>支持数据库、笔记、文献与加密私密库的完整备份；需按运行手册执行</dd></div>
                <div><dt>AI 助手</dt><dd>可选 API，默认关闭；每次发送前需确认</dd></div>
              </dl>
            </section>
          </template>

          <section v-else class="planned-page">
            <h1>页面不存在</h1>
            <p class="muted">请通过左侧导航继续访问。</p>
            <router-link to="/" class="text-link">返回首页 →</router-link>
          </section>
        </template>

        <p v-else class="muted" role="status">正在连接本机空间…</p>
      </main>

      <footer>
        <span><span class="status-dot" /> 本地管理 · 按需连接</span>
        <span>Personal Hub {{ state?.version || '' }}</span>
      </footer>
    </div>

    <CommandPalette :open="paletteOpen" :commands="commands" @close="paletteOpen = false" @open="openFromPalette" />
    <HubDialog :show="helpOpen" title="键盘快捷键" @update:show="v => { if (!v) helpOpen = false }">
      <dl class="shortcut-help">
        <div><dt><kbd>Ctrl</kbd> <kbd>K</kbd></dt><dd>跳转页面或搜索资料</dd></div>
        <div><dt><kbd>/</kbd></dt><dd>聚焦顶部搜索框</dd></div>
        <div><dt><kbd>Ctrl</kbd> <kbd>⇧</kbd> <kbd>N</kbd></dt><dd>记录想法</dd></div>
        <div><dt><kbd>Ctrl</kbd> <kbd>S</kbd></dt><dd>在编辑器中保存</dd></div>
        <div><dt><kbd>Esc</kbd></dt><dd>关闭最上层窗口</dd></div>
        <div><dt><kbd>?</kbd></dt><dd>打开此帮助（非输入状态）</dd></div>
      </dl>
    </HubDialog>
    <ConfirmHost />
    <HubToast />
  </div>
</template>
