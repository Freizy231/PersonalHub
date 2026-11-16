<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmAction, confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import HubDialog from '../../shared/HubDialog.vue'
import RecordEditor from '../../shared/RecordEditor.vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { isStale, useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { tabKey } from '../../shared/useTabKeys'
import { useToast } from '../../shared/toast'

defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Trend { stars: number[]; forks: number[]; score: number[]; series: number }
interface Item {
  id: string; title: string; url: string; source_key: string; domain: string | null
  read_state: string; favorite: number; summary: string; summary_zh?: string
  version: number; updated_at: string; trend?: Trend
}
interface Source { id: string; name: string; kind: string; endpoint: string; query: string; enabled: number; version: number }
interface Schedule { enabled: boolean; runs_per_day: number; local_hours: string[]; last_run_at: string | null; next_run_at: string | null; version: number }
interface Run { id: string; started_at: string; status: string; received: number; saved: number; error_code: string | null }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const readLabels: Record<string, string> = { new: '未读', later: '稍后', read: '已读' }
const runLabels: Record<string, string> = { success: '已完成', failed: '失败', partial: '部分完成', running: '采集中', starting: '启动中', unknown: '结果未知', interrupted: '已中断' }
const tabs = [{ id: 'items', label: '情报' }, { id: 'sources', label: '来源' }, { id: 'subscriptions', label: '订阅' }, { id: 'runs', label: '采集记录' }] as const

const tab = ref<string>('items')
const items = ref<{ items: Item[]; total: number }>({ items: [], total: 0 })
const sources = ref<{ items: Source[]; unconnected: { kind: string; reason: string }[]; github: string }>({ items: [], unconnected: [], github: '' })
const subscriptions = ref<{ id: string; keyword: string; enabled: number; version: number }[]>([])
const runs = ref<Run[]>([])
const query = ref('')
const sourceFilter = ref('')
const readState = ref('')
const favorite = ref(false)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const weekly = ref('')
const detail = ref<any>()
const limit = 30

const editorOpen = ref(false)
const editorArea = ref<'items' | 'sources' | 'subscriptions'>('items')
const editorRecord = ref<Record<string, any>>()
const editorDirty = ref(false)
async function closeEditor() {
  if (writes.busy.value) return
  if (editorDirty.value && !(await confirmDiscard('未保存的修改'))) return
  editorOpen.value = false; editorDirty.value = false
}

useUnsaved(() => editorDirty.value || writes.hasPending(), emit)

const editorFields = computed(() => {
  if (editorArea.value === 'sources') return [
    { key: 'name', label: '名称', required: true },
    { key: 'kind', label: '接口类型（创建后不可改）', type: 'select' as const, options: ['rss', 'hn', 'arxiv', 'huggingface'].map(value => ({ value, label: value })) },
    { key: 'endpoint', label: '地址（RSS/arXiv 需要 HTTPS）' },
    { key: 'query', label: '查询词（arXiv 填查询式，如 cat:cs.CR；HN 按这些词过滤标题；RSS 留空用上面的地址）' },
    { key: 'enabled', label: '启用（默认关闭，需要你显式开启）', type: 'boolean' as const, default: false },
  ]
  if (editorArea.value === 'subscriptions') return [
    { key: 'keyword', label: '关键词', required: true },
    { key: 'enabled', label: '启用', type: 'boolean' as const, default: true },
  ]
  return [
    { key: 'domain', label: '领域' },
    { key: 'read_state', label: '阅读状态', type: 'select' as const, options: [{ value: 'new', label: '未读' }, { value: 'later', label: '稍后' }, { value: 'read', label: '已读' }] },
    { key: 'favorite', label: '收藏', type: 'boolean' as const, default: false },
  ]
})

async function load() {
  loading.value = true; error.value = ''
  try {
    const params = new URLSearchParams({ q: query.value, source: sourceFilter.value, state: readState.value, favorite: String(favorite.value), limit: String(limit), offset: String(offset.value) })
    const [i, s, b, r] = await latest.track(() => Promise.all([
      api<{ items: Item[]; total: number }>(`/intelligence/items?${params}`),
      api<typeof sources.value>('/intelligence/sources'),
      api<{ items: typeof subscriptions.value }>('/intelligence/subscriptions'),
      api<{ items: Run[] }>('/intelligence/runs'),
    ]))
    items.value = i; sources.value = s; subscriptions.value = b.items; runs.value = r.items
  } catch (reason) {
    if (!isStale(reason)) error.value = errorMessage(reason)
  } finally { loading.value = false }
}

function openEditor(area: 'items' | 'sources' | 'subscriptions', record?: Record<string, any>) {
  editorArea.value = area
  editorRecord.value = record
  editorOpen.value = true
  editorDirty.value = false
}
function onSaved() { editorOpen.value = false; editorDirty.value = false; toast.push('已保存'); void load() }

/** A source card opens the item list already filtered to that source. */
function openFilter(source: Source) {
  sourceFilter.value = source.kind + ':' + source.id
  offset.value = 0
  tab.value = 'items'
  void load()
}

async function collect(source: Source) {
  try {
    const result = await writes.send<Run>('collect', `/intelligence/sources/${source.id}/collect`, {})
    toast.push(`采集完成：收到 ${result.received ?? 0}，保存 ${result.saved ?? 0}`)
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function importLegacy() {
  if (!(await confirmAction('只读取旧 GitHub 采集库的四张表并复制到 Hub，不会修改原数据库，也不会联网。', { title: '导入旧 GitHub 历史？', confirmLabel: '开始导入' }))) return
  try {
    const result = await api<{ status: string; counts: Record<string, number> }>('/intelligence/legacy/import', { method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: '{}' })
    toast.push(`旧历史导入：${result.status}`)
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function showItem(id: string) {
  try { detail.value = await api(`/intelligence/items/${id}`) }
  catch (reason) { toast.error(errorMessage(reason)) }
}

async function makeWeekly() {
  if (weekly.value && !(await confirmAction('重新生成会替换当前周报草稿，尚未复制到笔记的内容将丢失。', { title: '重新生成周报？', confirmLabel: '重新生成', danger: true }))) return
  try { weekly.value = (await api<{ markdown: string }>('/intelligence/weekly')).markdown }
  catch (reason) { toast.error(errorMessage(reason)) }
}

const schedule = ref<Schedule>({ enabled: false, runs_per_day: 2, local_hours: ['09:00', '21:00'], last_run_at: null, next_run_at: null, version: 0 })
const scheduleHours = ref('09:00,21:00')
const scheduleBusy = ref(false)
const collecting = ref('')

function scheduleFrom(value: Schedule) {
  // The schedule endpoint may be older than this build (or unreachable); never let a
  // missing list take the whole panel down with it.
  const hours = Array.isArray(value?.local_hours) ? value.local_hours : []
  schedule.value = { enabled: !!value?.enabled, runs_per_day: value?.runs_per_day || 2,
    local_hours: hours, last_run_at: value?.last_run_at ?? null, next_run_at: value?.next_run_at ?? null,
    version: value?.version || 0 }
  scheduleHours.value = hours.join(',')
}

async function loadSchedule() {
  try { scheduleFrom(await api<Schedule>('/intelligence/schedule')) }
  catch { /* The settings section simply stays at its default until it can be read. */ }
}

async function saveSchedule() {
  const hours = scheduleHours.value.split(/[,，\s]+/).map(v => v.trim()).filter(Boolean)
  scheduleBusy.value = true
  try {
    scheduleFrom(await api<Schedule>('/intelligence/schedule', {
      method: 'PUT',
      headers: { 'Idempotency-Key': crypto.randomUUID() },
      body: JSON.stringify({ enabled: schedule.value.enabled, runs_per_day: hours.length, local_hours: hours, expected_version: schedule.value.version }),
    }))
    toast.push(schedule.value.enabled ? '已开启定时采集' : '已关闭定时采集')
  } catch (reason) { toast.error(errorMessage(reason)) }
  finally { scheduleBusy.value = false }
}

async function collectNow(source: Source) {
  collecting.value = source.id
  try {
    const run = await api<{ status: string; received?: number; saved?: number; error_code?: string }>(`/intelligence/sources/${source.id}/collect`, {
      method: 'POST', headers: { 'Idempotency-Key': crypto.randomUUID() }, body: '{}',
    })
    toast.push(run.status === 'failed' ? `采集失败：${run.error_code || '未知原因'}` : `采集完成：收到 ${run.received ?? 0}，保存 ${run.saved ?? 0}`)
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
  finally { collecting.value = '' }
}

function turn(delta: number) { offset.value = Math.max(0, offset.value + delta); void load() }
onMounted(() => { void load(); void loadSchedule() })

/** A tiny inline chart: no axes, no library, just the shape of the series. */
function sparkline(values: number[]) {
  if (values.length < 2) return ''
  const min = Math.min(...values); const max = Math.max(...values); const span = max - min || 1
  return values.map((v, i) => `${(i / (values.length - 1)) * 100},${28 - ((v - min) / span) * 24}`).join(' ')
}
function delta(values: number[]) {
  if (values.length < 2) return ''
  const change = values[values.length - 1]! - values[0]!
  return change === 0 ? '持平' : (change > 0 ? '+' : '') + change
}
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="chip-row" role="tablist" aria-label="情报视图">
        <button v-for="t in tabs" :key="t.id" role="tab" :aria-selected="tab === t.id" :tabindex="tab === t.id ? 0 : -1" @click="tab = t.id" @keydown="tabKey($event, tabs.map(x => x.id), tab, v => { tab = v as typeof tab })">{{ t.label }}</button>
      </div>
      <div class="panel-actions">
        <HubButton v-if="tab === 'items'" variant="quiet" size="sm" @click="makeWeekly">生成周报</HubButton>
        <HubButton v-if="tab === 'sources'" variant="primary" size="sm" @click="openEditor('sources')">＋ 来源</HubButton>
        <HubButton v-if="tab === 'subscriptions'" variant="primary" size="sm" @click="openEditor('subscriptions')">＋ 关键词</HubButton>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <template v-if="tab === 'items'">
      <form class="filter-row" @submit.prevent="offset = 0; load()">
        <input v-model="query" placeholder="标题或摘要" maxlength="200" />
        <select v-model="sourceFilter" aria-label="来源">
          <option value="">全部来源</option>
          <option value="github:legacy">GitHub 历史</option>
          <option v-for="s in sources.items" :key="s.id" :value="s.kind + ':' + s.id">{{ s.name }}</option>
        </select>
        <select v-model="readState" aria-label="阅读状态">
          <option value="">全部状态</option>
          <option v-for="(label, key) in readLabels" :key="key" :value="key">{{ label }}</option>
        </select>
        <label class="checkbox-field"><input v-model="favorite" type="checkbox" @change="offset = 0; load()" /> 收藏</label>
        <HubButton variant="secondary" size="sm" :loading="loading">筛选</HubButton>
      </form>

      <p v-if="loading && !items.items.length" class="muted fine-print" role="status">读取中…</p>
      <p v-else-if="!items.items.length" class="empty-state">暂无情报。可导入旧 GitHub 历史，或登记并启用公开来源。</p>
      <ul v-else class="task-list">
        <li v-for="item in items.items" :key="item.id" class="intel-row">
          <div class="task-main">
            <div class="intel-headline">
              <a :href="item.url" target="_blank" rel="noopener noreferrer" class="task-title">{{ item.title }} ↗</a>
              <div v-if="item.trend && (item.trend.stars.length > 1 || item.trend.score.length > 1)" class="intel-spark" aria-label="历次采集的走势">
                <span v-if="item.trend.stars.length > 1" class="intel-metric">
                  <svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline :points="sparkline(item.trend.stars)" /></svg>
                  <span>★ {{ item.trend.stars.at(-1) }} <em :class="{ up: item.trend.stars.at(-1)! > item.trend.stars[0]!, down: item.trend.stars.at(-1)! < item.trend.stars[0]! }">{{ delta(item.trend.stars) }}</em></span>
                </span>
                <span v-if="item.trend.forks.length > 1" class="intel-metric">
                  <svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline :points="sparkline(item.trend.forks)" /></svg>
                  <span>⑂ {{ item.trend.forks.at(-1) }} <em :class="{ up: item.trend.forks.at(-1)! > item.trend.forks[0]!, down: item.trend.forks.at(-1)! < item.trend.forks[0]! }">{{ delta(item.trend.forks) }}</em></span>
                </span>
                <span v-if="item.trend.stars.length < 2 && item.trend.score.length > 1" class="intel-metric">
                  <svg viewBox="0 0 100 30" preserveAspectRatio="none" aria-hidden="true"><polyline :points="sparkline(item.trend.score)" /></svg>
                  <span>分 {{ item.trend.score.at(-1) }} <em :class="{ up: item.trend.score.at(-1)! > item.trend.score[0]!, down: item.trend.score.at(-1)! < item.trend.score[0]! }">{{ delta(item.trend.score) }}</em></span>
                </span>
                <span class="intel-series">{{ item.trend.series }} 次采集</span>
              </div>
            </div>
            <div class="task-meta">
              <span>{{ item.source_key }}</span>
              <span>{{ item.domain || '未分类' }}</span>
              <span>{{ readLabels[item.read_state] || '待整理' }}</span>
              <span v-if="item.favorite">★</span>
            </div>
            <p v-if="item.summary_zh" class="intel-summary-zh">{{ item.summary_zh }}</p>
            <p class="intel-summary-en" :class="{ 'is-muted': !!item.summary_zh }">{{ item.summary.slice(0, 240) }}</p>
          </div>
          <div class="task-actions">
            <button class="plain-action" @click="showItem(item.id)">详情</button>
            <button class="plain-action muted" @click="openEditor('items', item)">整理</button>
          </div>
        </li>
      </ul>
      <div v-if="items.total > limit" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!offset" @click="turn(-limit)">上一页</HubButton>
        <span class="muted">{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(items.total / limit) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="offset + limit >= items.total" @click="turn(limit)">下一页</HubButton>
      </div>

      <section v-if="detail" class="record-details">
        <div class="panel-toolbar">
          <h3>{{ detail.item.title }}</h3>
          <HubButton variant="quiet" size="sm" @click="detail = undefined">关闭</HubButton>
        </div>
        <h4>趋势</h4>
        <p class="fine-print">{{ detail.trend.note }}</p>
        <pre>{{ JSON.stringify(detail.trend, null, 2) }}</pre>
        <h4>同目标的其他来源 ({{ detail.same_target.length }})</h4>
        <ul class="plain-list">
          <li v-for="row in detail.same_target" :key="row.id">
            <button class="plain-action" @click="showItem(row.id)">{{ row.source_key }} · {{ row.title }}</button>
          </li>
        </ul>
        <details>
          <summary>历史快照 ({{ detail.snapshots.length }})</summary>
          <details v-for="snapshot in detail.snapshots" :key="snapshot.id">
            <summary>{{ snapshot.captured_at }} · {{ snapshot.metrics_json! || snapshot.metrics_json }}</summary>
            <pre>{{ snapshot.raw_json }}</pre>
          </details>
        </details>
      </section>

      <details v-if="weekly">
        <summary>周报草稿</summary>
        <textarea v-model="weekly" rows="14" aria-label="周报草稿" />
        <p class="fine-print">周报是页面草稿，复制到笔记保存；不会自动发布。</p>
      </details>
    </template>

    <template v-else-if="tab === 'sources'">
      <p class="fine-print">每个来源一张卡片。卡片里的查询词决定拉取什么：arXiv 填查询式，HN 按词过滤标题，RSS 用你保存的地址。只有点击「采集」或开启定时采集时才联网。</p>

      <ul class="source-cards">
        <li v-for="source in sources.items" :key="source.id" class="source-card">
          <div class="source-card-head">
            <strong>{{ source.name }}</strong>
            <span class="format-badge">{{ source.kind }}</span>
            <span class="muted fine-print">{{ source.enabled ? '已启用' : '已关闭' }}</span>
          </div>
          <p class="source-query">
            <span class="muted">查询词：</span>
            <code v-if="source.query">{{ source.query }}</code>
            <span v-else class="muted">（未设置，使用该来源的默认榜单）</span>
          </p>
          <div class="source-card-actions">
            <button class="plain-action" @click="openFilter(source)">查看这一来源 →</button>
            <button class="plain-action" @click="openEditor('sources', source)">配置</button>
            <HubButton variant="secondary" size="sm" :loading="collecting === source.id" :disabled="!source.enabled" @click="collectNow(source)">采集</HubButton>
          </div>
        </li>
      </ul>
      <p v-if="!sources.items.length" class="muted fine-print">还没有登记来源。</p>
      <p v-for="item in sources.unconnected" :key="item.kind" class="muted fine-print">{{ item.kind }}：未接入。</p>

      <section class="schedule-box">
        <h3>定时采集</h3>
        <p class="fine-print">默认关闭。开启后会在下面的时刻自动联网采集已启用的来源；同一个时刻只跑一次，服务重启不会补跑。</p>
        <label class="checkbox-field">
          <input v-model="schedule.enabled" type="checkbox" /> 开启定时采集
        </label>
        <label class="form-field">
          <span>每天的时刻（24 小时制，逗号分隔，个数即每天次数）</span>
          <input v-model="scheduleHours" placeholder="09:00,21:00" maxlength="120" />
        </label>
        <p class="muted fine-print">
          当前：{{ schedule.local_hours.length }} 次/天
          <span v-if="schedule.last_run_at"> · 上次 {{ new Date(schedule.last_run_at).toLocaleString('zh-CN') }}</span>
        </p>
        <div class="form-actions" style="justify-content: flex-start">
          <HubButton variant="primary" size="sm" :loading="scheduleBusy" @click="saveSchedule">保存定时设置</HubButton>
          <HubButton variant="secondary" size="sm" @click="importLegacy">导入旧 GitHub 历史</HubButton>
        </div>
      </section>
    </template>

    <template v-else-if="tab === 'subscriptions'">
      <p v-if="!subscriptions.length" class="empty-state">还没有关键词订阅。</p>
      <ul v-else class="plain-list">
        <li v-for="sub in subscriptions" :key="sub.id">
          <span>{{ sub.keyword }} · {{ sub.enabled ? '启用' : '关闭' }}</span>
          <button class="plain-action" @click="openEditor('subscriptions', sub)">编辑</button>
        </li>
      </ul>
    </template>

    <template v-else>
      <p v-if="!runs.length" class="muted fine-print">暂无采集记录。</p>
      <ul v-else class="plain-list">
        <li v-for="run in runs" :key="run.id">
          <span>{{ new Date(run.started_at).toLocaleString('zh-CN') }} · {{ runLabels[run.status] || run.status }}</span>
          <span class="muted fine-print">收到 {{ run.received }} / 保存 {{ run.saved }} <span v-if="run.error_code">{{ run.error_code }}</span></span>
        </li>
      </ul>
    </template>

    <HubDialog :show="editorOpen" title="编辑" :busy="writes.busy.value" @update:show="v => { if (!v) void closeEditor() }">
      <RecordEditor
        :key="editorArea + (editorRecord?.id || 'new')"
        :endpoint="'/intelligence/' + editorArea"
        :fields="editorFields"
        titleless
        :record="editorRecord"
        @draft="d => editorDirty = d"
        @saved="onSaved"
        @close="editorOpen = false"
      />
    </HubDialog>
  </section>
</template>

<style scoped>
.intel-headline { display: flex; align-items: flex-start; justify-content: space-between; gap: var(--space-3); flex-wrap: wrap; }
.intel-spark { display: flex; align-items: center; gap: var(--space-3); flex: 0 0 auto; padding: 2px 0; }
.intel-metric { display: flex; align-items: center; gap: 4px; color: var(--text-soft); font-size: var(--step--1); font-variant-numeric: tabular-nums; }
.intel-metric svg { width: 46px; height: 16px; overflow: visible; }
.intel-metric polyline { fill: none; stroke: var(--accent); stroke-width: 1.4; vector-effect: non-scaling-stroke; }
.intel-metric em { font-style: normal; color: var(--muted); }
.intel-metric em.up { color: var(--accent); }
.intel-metric em.down { color: var(--danger); }
.intel-series { color: var(--muted); font-size: var(--step--1); }
.intel-summary-zh { margin: 4px 0 0; color: var(--text); font-size: var(--step-0); line-height: 1.6; }
.intel-summary-en { margin: 4px 0 0; color: var(--text-soft); font-size: var(--step-0); line-height: 1.6; }
.intel-summary-en.is-muted { color: var(--muted); font-size: var(--step--1); }
.source-cards { list-style: none; margin: 0 0 var(--space-4); padding: 0; display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: var(--space-3); }
.source-card { display: flex; flex-direction: column; gap: var(--space-2); padding: var(--space-3) var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); min-width: 0; }
.source-card-head { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.source-card-head strong { font-weight: 600; }
.source-query { margin: 0; font-size: var(--step-0); overflow-wrap: anywhere; }
.source-query code { padding: 1px 5px; border-radius: 4px; background: var(--surface-2); font-size: var(--step--1); }
.source-card-actions { display: flex; align-items: center; gap: var(--space-3); flex-wrap: wrap; margin-top: auto; }
.schedule-box { padding: var(--space-4); border: 1px solid var(--border); border-radius: var(--radius-md); background: var(--surface); }
.schedule-box h3 { margin-bottom: var(--space-2); }
</style>
