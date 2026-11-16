<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmAction, confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import { api } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { useToast } from '../../../shared/toast'

defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Entry { id: string; local_date: string; month?: string; category_id: string; category_name?: string; direction?: string; currency: string; amount: string; memo: string | null; version: number }
interface Category { id: string; name: string; direction: 'income' | 'expense' }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const thisMonth = () => new Date().toLocaleDateString('sv-SE').slice(0, 7)
const month = ref(thisMonth())
const trash = ref(false)
const offset = ref(0)
const loading = ref(false)
const error = ref('')
const categories = ref<Category[]>([])
const entries = ref<{ items: Entry[]; total: number }>({ items: [], total: 0 })
const report = ref<{ month: string; totals: { currency: string; income: string; expense: string; balance: string }[]; categories: Entry[]; budgets: any[] }>({ month: '', totals: [], categories: [], budgets: [] })
const limit = 50

useUnsaved(() => writes.hasPending(), emit)

async function load() {
  loading.value = true; error.value = ''
  try {
    const [c, e, r] = await latest.track(() => Promise.all([
      api<{ items: Category[] }>('/finance/categories'),
      api<{ items: Entry[]; total: number }>(`/finance/entries?month=${month.value}&trash=${trash.value}&limit=${limit}&offset=${offset.value}`),
      api<typeof report.value>(`/finance/report?month=${month.value}`),
    ]))
    categories.value = c.items; entries.value = e; report.value = r
    if (e.total > 0 && e.items.length === 0 && offset.value > 0) { offset.value = Math.max(0, offset.value - limit); await load(); return }
  } catch (reason) {
    if (!isStale(reason)) error.value = reason instanceof Error ? reason.message : '账本暂不可用。'
  } finally { loading.value = false }
}

onMounted(load)

/* ----------------------------------------------------------------- editing */
type Area = 'categories' | 'entries' | 'budgets'
const area = ref<Area | ''>('')
const editing = ref<Entry>()
const categoryForm = ref({ name: '', direction: 'expense' as 'income' | 'expense' })
const entryForm = ref({ local_date: new Date().toLocaleDateString('sv-SE'), category_id: '', currency: 'CNY', amount: '', memo: '' })
const budgetForm = ref({ month: thisMonth(), category_id: '', currency: 'CNY', amount: '' })
const baseline = ref('')
const dirty = computed(() => area.value !== '' && JSON.stringify(editorSnapshot()) !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

function editorSnapshot() {
  return [area.value, editing.value?.id || '', categoryForm.value, entryForm.value, budgetForm.value]
}

const expenseCategories = computed(() => categories.value.filter(c => c.direction === 'expense'))
const currencies = ['CNY', 'USD', 'EUR', 'JPY'] as const

async function open(next: Area, row?: any) {
  if (area.value && !(await confirmDiscard('草稿'))) return
  writes.discard()
  area.value = next; editing.value = row
  if (next === 'categories') categoryForm.value = { name: row?.name || '', direction: row?.direction || 'expense' }
  if (next === 'entries') entryForm.value = {
    local_date: row?.local_date || new Date().toLocaleDateString('sv-SE'),
    category_id: row?.category_id || '',
    currency: row?.currency || 'CNY',
    amount: row?.amount || '',
    memo: row?.memo || '',
  }
  if (next === 'budgets') budgetForm.value = { month: row?.month || month.value, category_id: row?.category_id || expenseCategories.value[0]?.id || '', currency: row?.currency || 'CNY', amount: row?.amount || '' }
  baseline.value = JSON.stringify(editorSnapshot())
}

async function close() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的账本修改'))) return
  writes.discard(); area.value = ''; editing.value = undefined
}

async function save() {
  const version = { expected_version: editing.value?.version || 0 }
  try {
    if (area.value === 'categories') {
      const path = `/finance/categories${editing.value ? '/' + editing.value.id : ''}`
      await writes.send('finance', path, { ...categoryForm.value, ...version }, editing.value ? 'PUT' : 'POST')
    } else if (area.value === 'entries') {
      const path = `/finance/entries${editing.value ? '/' + editing.value.id : ''}`
      await writes.send('finance', path, { ...entryForm.value, ...version }, editing.value ? 'PUT' : 'POST')
    } else if (area.value === 'budgets') {
      const path = `/finance/budgets${editing.value ? '/' + editing.value.id : ''}`
      await writes.send('finance', path, { ...budgetForm.value, ...version }, editing.value ? 'PUT' : 'POST')
    }
    area.value = ''; editing.value = undefined
    toast.push('已保存')
    await load()
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '保存失败') }
}

async function change(entry: Entry) {
  try {
    // The server requires an Idempotency-Key on every write, state changes included.
    await api(`/finance/entries/${entry.id}/${trash.value ? 'restore' : 'trash'}`, {
      method: 'POST', body: JSON.stringify({ expected_version: entry.version }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    toast.push(trash.value ? '已恢复' : '已移入回收站')
    await load()
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '操作失败') }
}

/* --------------------------------------------------------------- import/export */
const importPreview = ref<{ categories: number; entries: number; budgets: number }>()
const importBody = ref('')
let importKey = ''
const fileInput = ref<HTMLInputElement>()

async function exportLedger() {
  if (!(await confirmAction('导出文件是未加密的 JSON，包含全部收支、备注与预算。导出后请自行妥善保管，不要放进同步盘或聊天工具。', { title: '导出明文账本？', confirmLabel: '导出' }))) return
  try {
    const data = await api<object>('/finance/export', { method: 'POST' })
    const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' }))
    const link = document.createElement('a'); link.href = url; link.download = 'personal-hub-private-ledger.json'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '导出失败') }
}

async function chooseImport(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  try {
    if (file.size > 900000) throw new Error('导入上限 900KB，请按月拆分或使用完整备份。')
    const data = JSON.parse(await file.text())
    if (data.format !== 'personal-hub-private-ledger' || data.version !== 1) throw new Error('请选择 Hub 账本导出 JSON。')
    const pick = (row: any, keys: string[]) => Object.fromEntries(keys.map(k => [k, row[k]]))
    const body = {
      categories: data.categories.map((r: any) => pick(r, ['id', 'name', 'direction'])),
      entries: data.entries.filter((r: any) => !r.deleted_at).map((r: any) => pick(r, ['id', 'local_date', 'category_id', 'currency', 'amount', 'memo'])),
      budgets: data.budgets.map((r: any) => pick(r, ['id', 'month', 'category_id', 'currency', 'amount'])),
    }
    importBody.value = JSON.stringify(body)
    importKey = crypto.randomUUID()
    importPreview.value = { categories: body.categories.length, entries: body.entries.length, budgets: body.budgets.length }
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '无法读取文件') }
}

async function confirmImport() {
  if (!(await confirmAction('只追加新记录。若有相同 ID 但内容不同的记录，整批导入会被拒绝，已有记录不会被覆盖。', { title: '追加导入账本？', confirmLabel: '开始导入' }))) return
  try {
    await api('/finance/import', { method: 'POST', headers: { 'Idempotency-Key': importKey }, body: importBody.value })
    importPreview.value = undefined; importBody.value = ''
    toast.push('导入完成')
    await load()
  } catch (reason) { toast.error(reason instanceof Error ? reason.message : '导入失败') }
}

function turn(delta: number) { offset.value = Math.max(0, offset.value + delta); void load() }
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="filter-row" style="padding: 0">
        <input v-model="month" type="month" aria-label="账本月份" @change="offset = 0; load()" />
        <button class="chip-button" :aria-pressed="!trash" @click="trash = false; offset = 0; load()">账本</button>
        <button class="chip-button" :aria-pressed="trash" @click="trash = true; offset = 0; load()">回收站</button>
      </div>
      <div class="panel-actions">
        <HubButton variant="quiet" size="sm" @click="exportLedger">导出</HubButton>
        <HubButton variant="secondary" size="sm" @click="fileInput?.click()">导入</HubButton>
        <input ref="fileInput" type="file" accept=".json,application/json" hidden @change="chooseImport" />
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <div class="panel-actions" style="margin: 12px 0">
      <HubButton variant="primary" size="sm" @click="open('entries')">＋ 记一笔</HubButton>
      <HubButton variant="secondary" size="sm" @click="open('budgets')">设预算</HubButton>
      <HubButton variant="quiet" size="sm" @click="open('categories')">分类</HubButton>
    </div>

    <div v-if="importPreview" class="preview-box">
      <p>待导入：分类 {{ importPreview.categories }} · 收支 {{ importPreview.entries }} · 预算 {{ importPreview.budgets }}</p>
      <div class="form-actions">
        <HubButton variant="quiet" @click="importPreview = undefined; importBody = ''">放弃</HubButton>
        <HubButton variant="primary" @click="confirmImport">确认追加</HubButton>
      </div>
    </div>

    <p v-if="loading" class="muted fine-print" role="status">读取中…</p>

    <template v-else>
      <section v-if="!trash" class="report-strip">
        <div v-for="total in report.totals" :key="total.currency">
          <span class="muted fine-print">{{ total.currency }}</span>
          <strong :class="{ income: true }">+{{ total.income }}</strong>
          <strong class="expense">−{{ total.expense }}</strong>
          <span class="muted">结余 {{ total.balance }}</span>
        </div>
        <p v-if="!report.totals.length" class="muted fine-print">本月暂无实际收支。</p>
      </section>

      <template v-if="!trash && report.budgets.length">
        <h3>预算</h3>
        <ul class="plain-list">
          <li v-for="budget in report.budgets" :key="budget.id">
            <span>{{ budget.name }} · {{ budget.currency }}</span>
            <span class="muted">预算 {{ budget.amount }} / 已支出 {{ budget.spent }} / 剩余 {{ budget.remaining }}</span>
            <button class="plain-action" @click="open('budgets', budget)">修改</button>
          </li>
        </ul>
      </template>

      <h3>{{ trash ? '回收站' : '记录' }}</h3>
      <p v-if="!entries.items.length" class="empty-state">{{ trash ? '本月回收站为空。' : categories.length ? '本月还没有记录，点"记一笔"开始。' : '先添加一个收支分类。' }}</p>
      <ul v-else class="task-list">
        <li v-for="entry in entries.items" :key="entry.id">
          <div class="task-main">
            <strong class="task-title">
              <span class="amount" :class="entry.direction === 'income' ? 'income' : 'expense'">{{ entry.direction === 'income' ? '+' : '−' }}{{ entry.currency }} {{ entry.amount }}</span>
              {{ entry.category_name }}
            </strong>
            <div class="task-meta">
              <span>{{ entry.local_date }}</span>
              <span v-if="entry.memo">{{ entry.memo }}</span>
            </div>
          </div>
          <div class="task-actions">
            <button v-if="!trash" class="plain-action" @click="open('entries', entry)">编辑</button>
            <button class="plain-action muted" @click="change(entry)">{{ trash ? '恢复' : '删除' }}</button>
          </div>
        </li>
      </ul>
      <div v-if="entries.total > limit" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!offset" @click="turn(-limit)">上一页</HubButton>
        <span class="muted">{{ Math.floor(offset / limit) + 1 }} / {{ Math.ceil(entries.total / limit) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="offset + limit >= entries.total" @click="turn(limit)">下一页</HubButton>
      </div>

      <details v-if="!trash && categories.length">
        <summary>分类 ({{ categories.length }})</summary>
        <ul class="plain-list">
          <li v-for="category in categories" :key="category.id">
            <span>{{ category.direction === 'income' ? '收入' : '支出' }} / {{ category.name }}</span>
            <button class="plain-action" @click="open('categories', category)">改名</button>
          </li>
        </ul>
      </details>
    </template>

    <HubDialog :show="area !== ''" :title="({ categories: '收支分类', entries: '记一笔', budgets: '分类预算' } as Record<string, string>)[area] || ''" :busy="writes.busy.value" @update:show="v => { if (!v) close() }" @submit="save">
      <form id="finance-form" @submit.prevent="save">
        <template v-if="area === 'categories'">
          <label class="form-field"><span>分类名称</span><input v-model="categoryForm.name" required maxlength="100" autofocus /></label>
          <label class="form-field"><span>类型（创建后不可改）</span>
            <select v-model="categoryForm.direction" :disabled="!!editing"><option value="expense">支出</option><option value="income">收入</option></select>
          </label>
        </template>

        <template v-if="area === 'entries'">
          <div class="form-grid">
            <label class="form-field"><span>日期</span><input v-model="entryForm.local_date" type="date" required /></label>
            <label class="form-field"><span>金额</span><input v-model="entryForm.amount" required inputmode="decimal" placeholder="正数" autofocus /></label>
          </div>
          <div class="form-grid">
            <label class="form-field"><span>分类</span>
              <select v-model="entryForm.category_id" required>
                <option value="">选择分类</option>
                <option v-for="c in categories" :key="c.id" :value="c.id">{{ c.direction === 'income' ? '收入' : '支出' }} / {{ c.name }}</option>
              </select>
            </label>
            <label class="form-field"><span>币种</span>
              <select v-model="entryForm.currency"><option v-for="c in currencies" :key="c" :value="c">{{ c }}</option></select>
            </label>
          </div>
          <label class="form-field"><span>备注</span><textarea v-model="entryForm.memo" rows="2" maxlength="2000" /></label>
        </template>

        <template v-if="area === 'budgets'">
          <div class="form-grid">
            <label class="form-field"><span>月份</span><input v-model="budgetForm.month" type="month" required /></label>
            <label class="form-field"><span>金额</span><input v-model="budgetForm.amount" required inputmode="decimal" autofocus /></label>
          </div>
          <div class="form-grid">
            <label class="form-field"><span>支出分类</span>
              <select v-model="budgetForm.category_id" required>
                <option value="">选择分类</option>
                <option v-for="c in expenseCategories" :key="c.id" :value="c.id">{{ c.name }}</option>
              </select>
            </label>
            <label class="form-field"><span>币种</span>
              <select v-model="budgetForm.currency"><option v-for="c in currencies" :key="c" :value="c">{{ c }}</option></select>
            </label>
          </div>
        </template>
        <p class="fine-print">账本以明文保存在本机 SQLite，不进入搜索、AI 或普通资料导出；完整备份包含账本。</p>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="close">取消</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" @click="save">保存</HubButton>
      </template>
    </HubDialog>
  </section>
</template>
