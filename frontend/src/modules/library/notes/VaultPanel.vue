<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { confirmAction, confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import MarkdownView from '../../../shared/MarkdownView.vue'
import { api, ApiError } from '../../../shared/api'

type VaultState = { initialized: boolean; unlocked: boolean; expires_in: number }
type Revision = { title: string; body: string; note_kind: string; local_date: string | null; version: number; updated_at: string }
type PrivateNote = Revision & { id: string; deleted_at: string | null; revisions: Revision[] }
const props = defineProps<{ enabled: boolean }>()
const emit = defineEmits<{ draft: [value: boolean] }>()
const state = ref<VaultState>({ initialized: false, unlocked: false, expires_in: 0 })
const ready = ref(false)
const error = ref(''), busy = ref(false), password = ref(''), confirmation = ref(''), oldPassword = ref(''), acknowledged = ref(false)
const changing = ref(false), notes = ref<PrivateNote[]>([]), selected = ref<PrivateNote | null>(null), editing = ref(false), trash = ref(false)
// Reading is separate from editing, matching ordinary notes: opening shows the article, edit is explicit.
const reading = ref(false)
const title = ref(''), body = ref(''), kind = ref('private_journal'), date = ref(''), dirty = ref(false), conflict = ref(false), offset = ref(0), total = ref(0)
const pending = ref<{ key: string; payload: string; path: string; method: string } | null>(null)
let deadline = 0
let generation = 0, timer: ReturnType<typeof setInterval> | undefined, polling = false, disposed = false, lastTouch = 0
function clear() {
  generation++; notes.value = []; selected.value = null; title.value = ''; body.value = ''; date.value = ''; kind.value = 'private_journal'
  password.value = ''; confirmation.value = ''; oldPassword.value = ''; acknowledged.value = false; changing.value = false
  editing.value = false; reading.value = false; dirty.value = false; conflict.value = false; pending.value = null; total.value = 0; offset.value = 0; emit('draft', false)
}
function purged() { deadline = 0; clear(); state.value = { ...state.value, unlocked: false, expires_in: 0 } }
function message(reason: unknown) { return reason instanceof Error ? reason.message : '私密库操作未完成。' }
async function failed(reason: unknown) {
  if (reason instanceof ApiError && ['VAULT_LOCKED', 'SESSION_REQUIRED', 'VAULT_CHANGED', 'VAULT_FILE_UNSAFE', 'VAULT_NOT_INITIALIZED'].includes(reason.code)) {
    purged(); void api('/vault/lock', { method: 'POST' }).catch(() => {})
  }
  error.value = message(reason)
}
async function poll() {
  if (polling || disposed || !props.enabled) return
  polling = true; const g = generation
  try {
    const result = await api<VaultState>('/vault/status')
    if (g !== generation || disposed) return
    if (!result.unlocked && state.value.unlocked) purged()
    // A fresh mount always requires an explicit unlock even if another tab shares its cookie.
    if (result.unlocked && !state.value.unlocked && !ready.value) { await api('/vault/lock', { method: 'POST' }); if (g !== generation || disposed) return }
    state.value = { ...result, unlocked: result.unlocked && state.value.unlocked }
    if (state.value.unlocked) deadline = Date.now() + result.expires_in * 1000
    ready.value = true
  } catch (reason) { if (g === generation && !disposed) { purged(); error.value = message(reason); void api('/vault/lock', { method: 'POST' }).catch(() => {}) } }
  finally { polling = false }
}
async function list() {
  const g = generation
  const result = await api<{ items: PrivateNote[]; total: number }>(`/vault/notes?trash=${trash.value}&limit=20&offset=${offset.value}`)
  if (g !== generation || disposed) return
  notes.value = result.items; total.value = result.total
}
async function unlock() {
  if (busy.value) return
  busy.value = true; error.value = ''; const g = generation
  const initializing = !state.value.initialized
  const payload = initializing ? { password: password.value, confirmation: confirmation.value, acknowledge_no_recovery: acknowledged.value } : { password: password.value }
  password.value = ''; confirmation.value = ''; acknowledged.value = false
  try {
    const result = await api<VaultState>(initializing ? '/vault/setup' : '/vault/unlock', { method: 'POST', body: JSON.stringify(payload) })
    if (g !== generation || disposed) { void api('/vault/lock', { method: 'POST' }).catch(() => {}); return }
    state.value = result; deadline = Date.now() + result.expires_in * 1000; lastTouch = Date.now(); await list()
  } catch (reason) { if (g === generation && !disposed) await failed(reason) } finally { busy.value = false }
}
async function lock(force = false) {
  if (!force && dirty.value && !(await confirmAction('锁定会立即清除屏幕上未保存的私密草稿，且无法找回。', { title: '锁定私密库？', confirmLabel: '锁定并清除', cancelLabel: '继续编辑', danger: true }))) return
  purged(); error.value = ''
  try { await api('/vault/lock', { method: 'POST', keepalive: true }) } catch { error.value = '页面已清空；锁定请求未确认，服务器会在空闲 5 分钟或本机会话到期后锁定。' }
}
function hidden() { if (document.hidden) void lock(true) }
function leaving() { void lock(true) }
function beforeUnload(event: BeforeUnloadEvent) { if (dirty.value) { event.preventDefault(); event.returnValue = '' } }
function activity() {
  if (!state.value.unlocked || Date.now() - lastTouch < 30000) return
  lastTouch = Date.now(); const g = generation
  void api<VaultState>('/vault/touch', { method: 'POST' }).then(result => { if (g === generation && !disposed) { state.value = result; deadline = Date.now() + result.expires_in * 1000 } }).catch(reason => { if (g === generation && !disposed) void failed(reason) })
}
function changed() { dirty.value = true; emit('draft', true); activity() }
/**
 * Asks before throwing away a private draft. If the vault was locked or purged
 * while the dialog was open, the answer is void: nothing may be shown again.
 */
async function discard() {
  if (!dirty.value) return true
  const g = generation
  const keep = await confirmDiscard('未保存的私密草稿')
  return keep && g === generation && !disposed
}
async function start() {
  if (!(await discard())) return
  selected.value = null; title.value = ''; body.value = ''; date.value = ''; kind.value = 'private_journal'
  reading.value = false; editing.value = true; dirty.value = false; conflict.value = false; pending.value = null; emit('draft', false)
}
async function open(id: string) {
  if (!(await discard())) return
  const g = generation
  try {
    const note = await api<PrivateNote>('/vault/notes/' + id)
    if (g !== generation || disposed) return
    selected.value = note; title.value = note.title; body.value = note.body; date.value = note.local_date || ''; kind.value = note.note_kind
    editing.value = false; reading.value = true; dirty.value = false; conflict.value = false; pending.value = null; emit('draft', false)
  } catch (reason) { if (g === generation && !disposed) await failed(reason) }
}
function closeReading() { reading.value = false; selected.value = null; title.value = ''; body.value = ''; date.value = ''; emit('draft', false) }
function editCurrent() { if (selected.value?.deleted_at) return; editing.value = true; reading.value = false }
function revision(item: Revision) {
  if (pending.value) return
  title.value = item.title; body.value = item.body; date.value = item.local_date || ''; kind.value = item.note_kind; changed()
}
async function save() {
  if (busy.value || conflict.value) return
  const payload = JSON.stringify({ title: title.value, body: body.value, note_kind: kind.value, local_date: date.value || null, ...(selected.value ? { expected_version: selected.value.version } : {}) })
  if (pending.value && payload !== pending.value.payload) { error.value = '上次保存结果未确认，请恢复原草稿后重试，或重新解锁核对记录。'; return }
  pending.value ||= { key: crypto.randomUUID(), payload, path: selected.value ? '/vault/notes/' + selected.value.id : '/vault/notes', method: selected.value ? 'PUT' : 'POST' }
  busy.value = true; error.value = ''; const g = generation
  try {
    const operation = pending.value
    const result = await api<PrivateNote>(operation.path, { method: operation.method, body: operation.payload, headers: { 'Idempotency-Key': operation.key } })
    if (g !== generation || disposed) return
    selected.value = result; title.value = result.title; body.value = result.body; date.value = result.local_date || ''; kind.value = result.note_kind
    pending.value = null; dirty.value = false; reading.value = true; editing.value = false; emit('draft', false); await list()
  } catch (reason) {
    if (g === generation && !disposed) {
      if (reason instanceof ApiError && reason.code === 'VERSION_CONFLICT') conflict.value = true
      await failed(reason)
    }
  } finally { busy.value = false }
}
async function copy() { const g = generation; if (!(await confirmAction('冲突的草稿将作为一条新的私密笔记保存，原笔记与历史修订保持不变。', { title: '另存为新私密笔记？', confirmLabel: '另存为新笔记' }))) return; if (g !== generation || disposed) return; selected.value = null; pending.value = null; conflict.value = false; changed() }
async function lifecycle() {
  if (!selected.value || busy.value || !(await discard())) return
  const note = selected.value, action = note.deleted_at ? 'restore' : 'trash'
  const lifecycleGeneration = generation
  const agreed = await confirmAction(action === 'trash' ? '这条私密笔记会移入私密回收站，历史修订仍保留在加密库内，可随时恢复。' : '这条私密笔记会从回收站恢复。', { title: action === 'trash' ? '移入私密回收站？' : '恢复私密笔记？', confirmLabel: action === 'trash' ? '移入回收站' : '恢复' })
  if (!agreed || lifecycleGeneration !== generation || disposed) return
  busy.value = true; const g = generation
  try {
    await api(`/vault/notes/${note.id}/${action}`, { method: 'POST', body: JSON.stringify({ expected_version: note.version }), headers: { 'Idempotency-Key': crypto.randomUUID() } })
    if (g !== generation || disposed) return
    editing.value = false; reading.value = false; selected.value = null; title.value = ''; body.value = ''; date.value = ''; dirty.value = false; emit('draft', false); await list()
  } catch (reason) { if (g === generation && !disposed) await failed(reason) } finally { busy.value = false }
}
async function filter(value: boolean) {
  if (!(await discard())) return
  trash.value = value; offset.value = 0; editing.value = false; selected.value = null
  title.value = ''; body.value = ''; date.value = ''; pending.value = null; dirty.value = false; emit('draft', false)
  try { await list() } catch (reason) { await failed(reason) }
}
async function page(delta: number) {
  if (!(await discard())) return
  offset.value += delta; editing.value = false; selected.value = null
  title.value = ''; body.value = ''; date.value = ''; dirty.value = false; pending.value = null; emit('draft', false)
  try { await list() } catch (reason) { await failed(reason) }
}
async function changePassword() {
  if (!(await confirmAction('改密后库会立即锁定。旧备份和任何暂存文件仍可用旧密码解开，请确认已妥善保管新密码与备份；忘记密码无法恢复。', { title: '更换私密库密码？', confirmLabel: '更换并锁定', danger: true }))) return
  busy.value = true; const g = generation
  const payload = { old_password: oldPassword.value, password: password.value, confirmation: confirmation.value, acknowledge_no_recovery: acknowledged.value }
  password.value = ''; confirmation.value = ''; oldPassword.value = ''; acknowledged.value = false
  try { await api('/vault/password', { method: 'POST', body: JSON.stringify(payload) }); if (g === generation && !disposed) { purged(); error.value = '密码已更换，请使用新密码解锁。' } }
  catch (reason) {
    if (g === generation && !disposed) {
      await failed(reason); await lock(true)
      error.value = '改密结果需核对。请重新解锁：替换前失败可用旧密码；提交后异常可能需新密码。'
    }
  } finally { busy.value = false }
}
onMounted(() => {
  void poll()
  timer = setInterval(() => { if (state.value.unlocked && Date.now() >= deadline) void lock(true); else void poll() }, 1000)
  document.addEventListener('visibilitychange', hidden)
  window.addEventListener('pagehide', leaving)
  window.addEventListener('beforeunload', beforeUnload)
})
onBeforeUnmount(() => {
  disposed = true; clearInterval(timer)
  document.removeEventListener('visibilitychange', hidden)
  window.removeEventListener('pagehide', leaving)
  window.removeEventListener('beforeunload', beforeUnload)
  purged(); void api('/vault/lock', { method: 'POST', keepalive: true }).catch(() => {})
})
</script>

<template>
  <section class="panel vault-panel" aria-label="独立私密库" @keydown="activity" @pointerdown="activity">
    <div class="panel-toolbar">
      <h2>私密日记</h2>
      <div class="panel-actions">
        <HubButton v-if="state.unlocked" variant="quiet" size="sm" :disabled="busy" @click="lock()">立即锁定</HubButton>
      </div>
    </div>

    <p class="fine-print">独立加密保存标题、日期、正文与历史。普通首页、搜索与导出不包含这里的内容。密码遗忘无法恢复。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!enabled" class="fine-print">请先确认个人资料空间可用。</p>
    <p v-else-if="!ready" class="fine-print" role="status">正在核对私密库状态…</p>

    <form v-else-if="!state.unlocked" class="vault-form" @submit.prevent="unlock">
      <h3>{{ state.initialized ? '解锁私密库' : '设置独立解锁密码' }}</h3>
      <label class="form-field"><span>密码（12～256 字符）</span>
        <input v-model="password" type="password" minlength="12" maxlength="256" required
          :autocomplete="state.initialized ? 'current-password' : 'new-password'" :disabled="busy" autofocus />
      </label>
      <template v-if="!state.initialized">
        <label class="form-field"><span>再次输入密码</span>
          <input v-model="confirmation" type="password" minlength="12" maxlength="256" required autocomplete="new-password" :disabled="busy" />
        </label>
        <label class="checkbox-field">
          <input v-model="acknowledged" type="checkbox" :disabled="busy" />
          我明白忘记密码将无法恢复任何私密内容。
        </label>
      </template>
      <div class="form-actions">
        <HubButton variant="primary" type="submit" :loading="busy" :disabled="!state.initialized && (!acknowledged || password !== confirmation)">
          {{ state.initialized ? '解锁' : '创建私密库' }}
        </HubButton>
      </div>
      <p class="fine-print">密码只在本机提交，不写入配置或浏览器存储。</p>
    </form>

    <template v-else>
      <p class="fine-print" role="status">已解锁 · 空闲剩余 {{ state.expires_in }} 秒；单次最长 30 分钟。页面隐藏或离开会清空未保存草稿。</p>

      <div class="panel-toolbar">
        <div class="chip-row" role="group" aria-label="私密视图">
          <button :aria-pressed="!trash" @click="filter(false)">私密记录</button>
          <button :aria-pressed="trash" @click="filter(true)">回收站</button>
        </div>
        <div class="panel-actions">
          <HubButton variant="quiet" size="sm" :disabled="busy" @click="changing = !changing">改密码</HubButton>
          <HubButton variant="primary" size="sm" :disabled="busy || trash" @click="start">＋ 写私密笔记</HubButton>
        </div>
      </div>

      <form v-if="changing" class="vault-form" @submit.prevent="changePassword">
        <h3>更换密码</h3>
        <p class="fine-print">改密不会更新旧备份的密码；请先保存草稿。</p>
        <label class="form-field"><span>旧密码</span><input v-model="oldPassword" type="password" autocomplete="current-password" :disabled="busy" /></label>
        <label class="form-field"><span>新密码（12～256 字符）</span><input v-model="password" type="password" minlength="12" maxlength="256" autocomplete="new-password" :disabled="busy" /></label>
        <label class="form-field"><span>再次输入新密码</span><input v-model="confirmation" type="password" minlength="12" maxlength="256" autocomplete="new-password" :disabled="busy" /></label>
        <label class="checkbox-field"><input v-model="acknowledged" type="checkbox" :disabled="busy" /> 我明白旧备份仍需旧密码。</label>
        <div class="form-actions">
          <HubButton variant="quiet" :disabled="busy" @click="changing = false">取消</HubButton>
          <HubButton variant="primary" type="submit" :loading="busy" :disabled="!acknowledged || password !== confirmation">更换密码</HubButton>
        </div>
      </form>

      <p v-if="!notes.length" class="empty-state">{{ trash ? '私密回收站为空。' : '暂无私密记录。点击新建开始，仅在明确保存后写入密文。' }}</p>
      <ul v-else-if="!reading" class="task-list">
        <li v-for="note in notes" :key="note.id">
          <div class="task-main">
            <button class="task-title" :disabled="busy" @click="open(note.id)">{{ note.title }}</button>
            <div class="task-meta"><span>{{ note.local_date || '未指定日期' }}</span><span>v{{ note.version }}</span></div>
          </div>
        </li>
      </ul>
      <div v-if="total > 20" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="offset === 0 || busy" @click="page(-20)">上一页</HubButton>
        <span class="muted">{{ offset + 1 }}–{{ Math.min(offset + 20, total) }} / {{ total }}</span>
        <HubButton variant="quiet" size="sm" :disabled="offset + 20 >= total || busy" @click="page(20)">下一页</HubButton>
      </div>

      <article v-if="reading && selected && !editing" class="note-reading">
        <div class="note-reading-toolbar">
          <button class="plain-action" :disabled="busy" @click="closeReading">← 返回列表</button>
          <span>{{ selected.note_kind === 'treehole' ? '树洞' : '私密日记' }}{{ selected.deleted_at ? ' · 已在回收站' : '' }}</span>
          <HubButton variant="secondary" size="sm" :disabled="busy || !!selected.deleted_at" @click="editCurrent">编辑</HubButton>
        </div>
        <h2>{{ selected.title }}</h2>
        <div class="note-reading-meta">
          {{ selected.local_date || '未指定日期' }} · 版本 {{ selected.version }}
          · {{ new Date(selected.updated_at).toLocaleDateString('zh-CN', { year: 'numeric', month: 'long', day: 'numeric' }) }}
        </div>
        <MarkdownView :body="selected.body || ''" />
        <div class="form-actions">
          <HubButton v-if="selected.deleted_at && !trash" variant="quiet" :disabled="busy" @click="lifecycle">恢复</HubButton>
          <HubButton v-else-if="!selected.deleted_at && !trash" variant="quiet" :disabled="busy" @click="lifecycle">移入回收站</HubButton>
        </div>
        <p class="fine-print">阅读态不写入磁盘；锁定、页面隐藏或离开都会立即清空这页内容。</p>
      </article>

      <form v-if="editing" class="vault-form record-editor" @submit.prevent="save">
        <h3>{{ selected ? '编辑私密笔记' : '新建私密笔记' }}</h3>
        <label class="form-field"><span>标题</span>
          <input v-model="title" maxlength="500" required :disabled="busy || !!selected?.deleted_at" autofocus @input="changed" />
        </label>
        <div class="form-grid">
          <label class="form-field"><span>类型</span>
            <select v-model="kind" :disabled="busy || !!selected?.deleted_at" @change="changed">
              <option value="private_journal">私密日记</option>
              <option value="treehole">树洞</option>
            </select>
          </label>
          <label class="form-field"><span>日期</span>
            <input v-model="date" type="date" :disabled="busy || !!selected?.deleted_at" @input="changed" />
          </label>
        </div>
        <label class="form-field"><span>正文（Markdown）</span>
          <textarea v-model="body" rows="14" maxlength="200000" :disabled="busy || !!selected?.deleted_at" spellcheck="false" @input="changed" />
        </label>
        <p class="fine-print">仅原文编辑，不执行 HTML、远程图片或脚本。没有自动保存。</p>
        <div class="form-actions">
          <HubButton v-if="selected" variant="quiet" :disabled="busy" @click="lifecycle">{{ selected.deleted_at ? '恢复' : '移入回收站' }}</HubButton>
          <HubButton v-if="conflict" variant="quiet" @click="copy">另存为新笔记</HubButton>
          <span class="spacer" />
          <HubButton variant="primary" type="submit" :loading="busy" :disabled="!!selected?.deleted_at || conflict">保存</HubButton>
        </div>
        <p v-if="dirty" class="fine-print" role="status">有未保存的私密草稿</p>

        <details v-if="selected">
          <summary>加密历史修订（{{ selected.revisions.length }}）</summary>
          <ol class="member-list">
            <li v-for="item in selected.revisions.slice().reverse()" :key="item.version">
              <span>v{{ item.version }} · {{ new Date(item.updated_at).toLocaleString('zh-CN') }}</span>
              <button type="button" class="plain-action" :disabled="busy || !!pending" @click="revision(item)">放入草稿</button>
            </li>
          </ol>
        </details>
      </form>

      <p class="fine-print">备份：先停止服务，再按运行手册执行完整备份；包含此加密库。恢复只进入全新目录，仍需对应密码。本版不提供私密明文导出。</p>
    </template>
  </section>
</template>

<style scoped>
.vault-form { display: grid; gap: var(--space-3); max-width: 720px; margin: var(--space-4) 0; }
.vault-form .form-field { margin-bottom: 0; }
</style>
