<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { confirmAction } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import HubDialog from '../../shared/HubDialog.vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const props = withDefaults(defineProps<{ enabled: boolean; mode?: 'chat' | 'settings' }>(), { mode: 'chat' })
const emit = defineEmits<{ draft: [boolean] }>()

interface Config { endpoint: string; model: string; enabled: boolean; version: number; has_token: boolean; needs_rebind: boolean }
interface Source { id: string; kind: string; title: string; version: number; truncated?: boolean; source_hash?: string; text?: string }
interface Preview { id: string; digest: string; endpoint: string; model: string; question: string; sources: Source[]; expires_in_seconds: number; notice: string }
interface Run { id: string; status: string; answer?: string; sources?: Source[]; error_code?: string | null; result_expired?: boolean }

const toast = useToast()
const writes = useIdempotent()

const config = ref<Config>()
const endpoint = ref('')
const model = ref('')
const token = ref('')
const apiEnabled = ref(false)
const clearToken = ref(false)
const configBusy = ref(false)
const configDirty = ref(false)

const question = ref('')
const resourceIds = ref<string[]>([])
const intelIds = ref<string[]>([])
const preview = ref<Preview>()
const run = ref<Run>()
const error = ref('')
const busy = ref(false)
const confirmOpen = ref(false)
const presetQuestions = ['总结这几份资料的核心结论', '指出资料之间相互矛盾的地方', '根据资料列出下一步可执行的动作']

let timer: ReturnType<typeof setTimeout> | undefined
let configBaseline = ''
let questionBaseline = ''

const dirty = computed(() => configDirty.value || question.value !== questionBaseline || !!preview.value || resourceIds.value.length + intelIds.value.length > 0 || writes.hasPending())
useUnsaved(() => dirty.value, emit)

const runLabels: Record<string, string> = {
  starting: '正在等待回答', success: '已完成', failed: '请求失败',
  cancelled: '已取消', unknown: '结果未知', unavailable: '暂时无法读取',
}

async function load() {
  try {
    config.value = await api<Config>('/assistant/config')
    endpoint.value = config.value.endpoint
    model.value = config.value.model
    apiEnabled.value = config.value.enabled
    clearToken.value = false
    configBaseline = JSON.stringify([endpoint.value, model.value, apiEnabled.value])
    configDirty.value = false
  } catch (reason) { error.value = errorMessage(reason) }
}

async function saveConfig() {
  configBusy.value = true; error.value = ''
  try {
    await api<Config>('/assistant/config', {
      method: 'PUT',
      body: JSON.stringify({
        endpoint: endpoint.value, model: model.value, enabled: apiEnabled.value,
        token: token.value || null, clear_token: clearToken.value, expected_version: config.value?.version ?? 0,
      }),
    })
    token.value = ''; configDirty.value = false
    toast.push('配置已保存')
    await load()
  } catch (reason) { error.value = errorMessage(reason) }
  finally { configBusy.value = false }
}

// Any edit invalidates a previous preview. The leave-page guard follows `dirty` through useUnsaved,
// so it can never be left switched on by a hand-written emit.
function changed() { preview.value = undefined }

async function usePreset(value: string) {
  if (busy.value) return
  if (question.value.trim() && question.value !== value && !(await confirmAction('当前输入的问题会被预设问题替换。', { title: '替换当前问题？', confirmLabel: '替换', cancelLabel: '保留我的问题' }))) return
  question.value = value; changed()
}

function removeSource(id: string, intel = false) {
  if (intel) intelIds.value = intelIds.value.filter(x => x !== id)
  else resourceIds.value = resourceIds.value.filter(x => x !== id)
  changed()
}

const sourceTitles = ref<Record<string, string>>({})
const catalogQuery = ref('')
const catalog = ref<{ id: string; title: string; kind: string }[]>([])
const intelQuery = ref('')
const intelCatalog = ref<{ id: string; title: string }[]>([])

async function searchCatalog() {
  try {
    const result = await api<{ items: { id: string; title: string; kind: string }[] }>(`/resources?q=${encodeURIComponent(catalogQuery.value)}&limit=50`)
    catalog.value = result.items.filter(r => ['note', 'document', 'book', 'bookmark', 'course', 'knowledge_node', 'learning_path', 'learning_goal', 'recipe'].includes(r.kind))
  } catch (reason) { toast.error(errorMessage(reason)) }
}
async function searchIntel() {
  try {
    const result = await api<{ items: { id: string; title: string }[] }>(`/intelligence/items?q=${encodeURIComponent(intelQuery.value)}&limit=30`)
    intelCatalog.value = result.items
  } catch (reason) { toast.error(errorMessage(reason)) }
}
function toggleSource(id: string, title: string, intel = false) {
  const list = intel ? intelIds : resourceIds
  if (list.value.includes(id)) { removeSource(id, intel); return }
  if (resourceIds.value.length + intelIds.value.length >= 8) { toast.error('最多 8 项资料，请先移除一项。'); return }
  sourceTitles.value = { ...sourceTitles.value, [id]: title }
  list.value = [...list.value, id]
  changed()
}

async function makePreview() {
  if (!question.value.trim()) { toast.error('请先输入问题。'); return }
  busy.value = true; error.value = ''
  try {
    preview.value = await api<Preview>('/assistant/preview', {
      method: 'POST',
      body: JSON.stringify({ question: question.value, resource_ids: resourceIds.value, intelligence_ids: intelIds.value }),
    })
    run.value = undefined
    confirmOpen.value = true
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false }
}

async function confirmSend() {
  if (!preview.value) return
  busy.value = true; error.value = ''
  try {
    const result = await writes.send<Run>('assistant', '/assistant/confirm', { preview_id: preview.value.id, digest: preview.value.digest })
    run.value = result
    confirmOpen.value = false
    // The preview has been used: keeping it would show a stale confirmation box, and a second
    // click would send a new key with the same preview id.
    preview.value = undefined
    void poll()
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false }
}

async function poll() {
  if (timer) clearTimeout(timer)
  if (!run.value?.id) return
  const id = run.value.id
  try {
    const result = await api<Run>(`/assistant/runs/${id}`)
    if (run.value?.id !== id) return
    run.value = result
    if (result.status === 'starting') timer = setTimeout(() => void poll(), 800)
    else if (result.status === 'success') timer = setTimeout(() => void poll(), 5000)
  } catch (reason) {
    run.value = { id, status: 'unavailable', error_code: reason instanceof Error ? reason.message : 'CONNECTION_FAILED' }
  }
}

async function cancelRun() {
  if (!run.value?.id) return
  try {
    run.value = await api<Run>(`/assistant/runs/${run.value.id}/cancel`, { method: 'POST' })
    toast.push('已停止本地请求；服务商可能已收到内容或计费')
  } catch (reason) { error.value = errorMessage(reason) }
}

function clearAll() {
  if (busy.value) return
  question.value = ''; resourceIds.value = []; intelIds.value = []
  preview.value = undefined; run.value = undefined
  toast.push('已清空本页内容')
}

onMounted(() => { void load(); questionBaseline = question.value })
onBeforeUnmount(() => { if (timer) clearTimeout(timer); token.value = '' })
</script>

<template>
  <section class="panel">
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <p v-if="!config" class="muted fine-print" role="status">读取配置中…</p>

    <template v-else>
      <section v-if="mode === 'settings' || !config.enabled" class="settings-section">
        <h2>API 助手</h2>
        <p class="fine-print">使用兼容 Chat Completions 的 API，默认关闭。密钥只保存在本机，不进入备份或导出。</p>
        <p v-if="config.needs_rebind" class="panel-error" role="alert">原有密钥文件缺失，请重新输入密钥后再启用。</p>
        <label class="form-field"><span>请求地址（HTTPS）</span>
          <input v-model="endpoint" placeholder="https://api.example.com/v1/chat/completions" @input="configDirty = true" />
        </label>
        <label class="form-field"><span>模型</span><input v-model="model" placeholder="model-name" @input="configDirty = true" /></label>
        <label class="form-field"><span>API 密钥{{ config.has_token ? '（已保存，留空保持不变）' : '' }}</span>
          <input v-model="token" type="password" autocomplete="off" placeholder="只填写在密钥栏" />
        </label>
        <label class="checkbox-field"><input v-model="apiEnabled" type="checkbox" @change="configDirty = true" /> 启用助手</label>
        <label v-if="config.has_token" class="checkbox-field"><input v-model="clearToken" type="checkbox" @change="configDirty = true" /> 清除已保存的密钥</label>
        <div class="form-actions">
          <HubButton variant="primary" :loading="configBusy" :disabled="!configDirty && !token" @click="saveConfig">保存配置</HubButton>
        </div>
      </section>

      <template v-if="mode !== 'settings'">
        <section v-if="config.enabled" class="chat-area">
          <div class="filter-row">
            <span class="muted fine-print">已选 {{ resourceIds.length + intelIds.length }} / 8 项资料</span>
            <button v-if="resourceIds.length + intelIds.length" class="plain-action" @click="clearAll">清空</button>
          </div>

          <details :open="!resourceIds.length && !intelIds.length">
            <summary>选择资料</summary>
            <div class="source-grid">
              <div>
                <div class="filter-row">
                  <input v-model="catalogQuery" placeholder="查找普通资料" maxlength="256" @keydown.enter.prevent="searchCatalog" />
                  <HubButton variant="quiet" size="sm" @click="searchCatalog">查找</HubButton>
                </div>
                <label v-for="record in catalog" :key="record.id" class="evidence-option">
                  <input type="checkbox" :checked="resourceIds.includes(record.id)" @change="toggleSource(record.id, record.title, false)" />
                  {{ record.title }}
                </label>
              </div>
              <div>
                <div class="filter-row">
                  <input v-model="intelQuery" placeholder="查找已采集情报" maxlength="256" @keydown.enter.prevent="searchIntel" />
                  <HubButton variant="quiet" size="sm" @click="searchIntel">查找</HubButton>
                </div>
                <label v-for="item in intelCatalog" :key="item.id" class="evidence-option">
                  <input type="checkbox" :checked="intelIds.includes(item.id)" @change="toggleSource(item.id, item.title, true)" />
                  {{ item.title }}
                </label>
              </div>
            </div>
          </details>

          <ul v-if="resourceIds.length || intelIds.length" class="plain-list">
            <li v-for="id in resourceIds" :key="'r' + id">
              <span>{{ sourceTitles[id] || '已选资料' }}</span>
              <button class="plain-action" @click="removeSource(id)">移除</button>
            </li>
            <li v-for="id in intelIds" :key="'i' + id">
              <span>{{ sourceTitles[id] || '已选情报' }}</span>
              <button class="plain-action" @click="removeSource(id, true)">移除</button>
            </li>
          </ul>

          <div class="chip-row">
            <button v-for="preset in presetQuestions" :key="preset" class="chip" @click="usePreset(preset)">{{ preset }}</button>
          </div>

          <label class="form-field"><span>问题</span>
            <textarea v-model="question" rows="3" maxlength="4000" placeholder="基于所选资料提问…" @input="changed" />
          </label>

          <div class="form-actions">
            <HubButton variant="primary" :loading="busy" :disabled="!question.trim()" @click="makePreview">预览并确认发送</HubButton>
          </div>

          <section v-if="preview" class="preview-box">
            <h3>发送前确认</h3>
            <p>{{ preview.notice }}</p>
            <ul class="plain-list">
              <li v-for="source in preview.sources" :key="source.id">
                <span>[{{ source.id.slice(0, 6) }}] {{ source.title }}</span>
                <span class="muted fine-print">{{ source.kind }}{{ source.truncated ? ' · 已截断' : '' }}</span>
              </li>
            </ul>
            <div class="form-actions">
              <HubButton variant="quiet" @click="preview = undefined">取消</HubButton>
              <HubButton variant="primary" :loading="busy" @click="confirmSend">确认发送</HubButton>
            </div>
          </section>

          <section v-if="run" class="preview-box">
            <h3>{{ runLabels[run.status] || run.status }}</h3>
            <template v-if="run.status === 'success' && run.answer">
              <div class="answer">{{ run.answer }}</div>
              <p v-for="source in run.sources || []" :key="source.id" class="fine-print">[{{ source.id.slice(0, 6) }}] {{ source.title }}</p>
            </template>
            <p v-else-if="run.result_expired" class="fine-print">来源版本或可见性已变化，临时回答不再展示。</p>
            <p v-else-if="run.error_code" class="fine-print">{{ run.error_code }}</p>
            <div class="form-actions">
              <HubButton v-if="run.status === 'starting'" variant="quiet" @click="cancelRun">取消请求</HubButton>
              <HubButton variant="quiet" @click="run = undefined">关闭</HubButton>
            </div>
          </section>
        </section>

        <p v-else class="empty-state">AI 助手尚未启用。可在设置中配置 API。</p>
      </template>
    </template>
  </section>
</template>

<style scoped>
.source-grid { display: grid; grid-template-columns: 1fr 1fr; gap: var(--space-3); }
.answer { white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--step-1); line-height: 1.8; }
.chip { padding: 5px 12px; border: 1px solid var(--border); border-radius: 100px; background: none; color: var(--text-soft); font: inherit; font-size: var(--step--1); cursor: pointer; }
.chip:hover { border-color: var(--accent); color: var(--accent); }
@media (max-width: 680px) { .source-grid { grid-template-columns: 1fr; } }
</style>
