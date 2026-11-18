<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import HubButton from '../../shared/HubButton.vue'
import { useToast } from '../../shared/toast'

/**
 * Authorization and personal templates, kept in settings rather than in the daily flow.

 * The two permissions are separate on purpose: letting this machine create tasks is not the
 * same decision as letting a model service receive your notes, and each says what it covers.
 */
interface Goal { id: string; title: string }
interface Grant { id: string; kind: string; scope_id: string | null; enabled: boolean; active: boolean;
  expires_at: string | null; endpoint: string; model: string }
interface Template { id: string; name: string; purpose: string; version: number; enabled: boolean;
  steps: { capability_id: string; arguments: Record<string, unknown> }[] }

const toast = useToast()
const goals = ref<Goal[]>([])
const grants = ref<Grant[]>([])
const templates = ref<Template[]>([])
const scope = ref('')
const busy = ref('')
const error = ref('')

async function load() {
  busy.value = 'load'; error.value = ''
  try {
    const [g, gr, tp] = await Promise.all([
      api<{ items: Goal[] }>('/learning/learning_goal?limit=100'),
      api<{ items: Grant[] }>('/ai/grants'),
      api<{ items: Template[] }>('/ai/templates'),
    ])
    goals.value = g.items; grants.value = gr.items; templates.value = tp.items
    if (!scope.value && goals.value.length) scope.value = goals.value[0]!.id
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = '' }
}

async function setGrant(kind: 'local_write' | 'outbound', enabled: boolean) {
  busy.value = kind
  try {
    await api('/ai/grants', { method: 'PUT', body: JSON.stringify({
      kind, scope_id: kind === 'local_write' ? (scope.value || null) : null,
      enabled, days: enabled ? 30 : null,
    }) })
    toast.push(enabled ? '已授权' : '已撤销')
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
  finally { busy.value = '' }
}

async function toggleTemplate(template: Template) {
  busy.value = template.id
  try {
    await api(`/ai/templates/${template.id}/state`, {
      method: 'POST', body: JSON.stringify({ enabled: !template.enabled }),
    })
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
  finally { busy.value = '' }
}

function active(kind: string) { return grants.value.find(g => g.kind === kind && g.active) }

onMounted(load)
</script>

<template>
  <section class="settings-section ai-permissions">
    <h2>AI 权限</h2>
    <p class="muted">两项权限分开授予。撤销后，后续的写入与外发立即停止；已经发送出去的内容无法收回。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <div class="permission-row">
      <div>
        <strong>允许在本机创建学习内容</strong>
        <p>在选定学习目标范围内创建知识点、练习任务和笔记。只写入本机数据库，不发送任何资料。</p>
      </div>
      <div class="permission-control">
        <select v-model="scope" aria-label="授权的学习目标">
          <option v-for="goal in goals" :key="goal.id" :value="goal.id">{{ goal.title }}</option>
        </select>
        <HubButton v-if="active('local_write')" variant="quiet" size="sm" :loading="busy === 'local_write'"
          @click="setGrant('local_write', false)">撤销</HubButton>
        <HubButton v-else variant="secondary" size="sm" :loading="busy === 'local_write'"
          :disabled="!scope" @click="setGrant('local_write', true)">授权 30 天</HubButton>
      </div>
    </div>

    <div class="permission-row">
      <div>
        <strong>允许把资料发送给模型服务</strong>
        <p>只发送你选定的资料与问题。绑定当前配置的服务地址与模型；改动配置后需要重新授权。</p>
      </div>
      <div class="permission-control">
        <span v-if="active('outbound')" class="pill ok">已授权至 {{ active('outbound')!.expires_at?.slice(0, 10) }}</span>
        <HubButton v-if="active('outbound')" variant="quiet" size="sm" :loading="busy === 'outbound'"
          @click="setGrant('outbound', false)">撤销</HubButton>
        <HubButton v-else variant="secondary" size="sm" :loading="busy === 'outbound'"
          @click="setGrant('outbound', true)">授权 30 天</HubButton>
      </div>
    </div>

    <h3>个人模板</h3>
    <p class="muted">模板只引用已注册的能力与参数，不含脚本。停用后不会被执行。</p>
    <ul class="plain-list">
      <li v-for="template in templates" :key="template.id">
        <b>{{ template.name }}</b>
        <span class="muted"> · {{ template.steps.length }} 步 · v{{ template.version }}</span>
        <span v-if="!template.enabled" class="pill off">已停用</span>
        <button class="plain-action" :disabled="busy === template.id" @click="toggleTemplate(template)">
          {{ template.enabled ? '停用' : '启用' }}
        </button>
      </li>
    </ul>
    <p v-if="!templates.length" class="fine-print muted">还没有模板。服务启动时会安装两个默认模板。</p>
  </section>
</template>

<style scoped>
.ai-permissions { display: grid; gap: var(--space-3); }
.permission-row { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: var(--space-4); align-items: center; padding: var(--space-3) 0; border-bottom: 1px solid var(--border); }
.permission-row p { margin: 4px 0 0; font-size: var(--step--1); color: var(--muted); }
.permission-control { display: flex; align-items: center; gap: var(--space-2); flex-wrap: wrap; }
.ai-permissions h3 { margin: var(--space-3) 0 0; font-size: var(--step-2); }
@media (max-width: 720px) { .permission-row { grid-template-columns: minmax(0, 1fr); } }
</style>
