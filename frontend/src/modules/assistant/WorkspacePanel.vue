<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import HubButton from '../../shared/HubButton.vue'
import { useToast } from '../../shared/toast'

/**
 * The AI workspace: one place to say what you want to learn, see the candidate plan, and
 * adopt it. Kept deliberately close to the existing learning page: the output is real goals,
 * paths, nodes and practice tasks that open in 学习, not a chat transcript pretending to be one.
 */
interface Goal { id: string; title: string; version: number }
interface PlanStep { position: number; capability_id: string; arguments: Record<string, unknown>; state: string }
interface Plan { id: string; title: string; rationale: string; steps: PlanStep[]; rejected: { code: string; detail: string }[]; state: string }
interface RunStep { position: number; capability_id: string; status: string; summary: string; resource_ids: string[]; error_code: string | null }
interface Run { id: string; status: string; steps: RunStep[]; error_code?: string | null }

defineProps<{ enabled: boolean }>()
const toast = useToast()

const goals = ref<Goal[]>([])
const scope = ref('')
const question = ref('')
const intent = ref('')
const plan = ref<Plan>()
const run = ref<Run>()
const busy = ref('')
const error = ref('')
const review = ref<any>()
/** Whether this goal may be written to. Drafting works either way; adopting needs it. */
const granted = ref(false)
/** Whether content may be sent to the model at all. Drafting needs this one. */
const outbound = ref(false)

const capabilityNames: Record<string, string> = {
  'learning.goal.create': '创建学习目标',
  'learning.path.create': '创建学习路线',
  'learning.node.create': '创建知识点',
  'learning.practice.create': '创建练习任务',
  'notes.create': '创建笔记',
}

function stepName(id: string) { return capabilityNames[id] || id }

async function load() {
  busy.value = 'load'; error.value = ''
  try {
    const [found, grants] = await Promise.all([
      api<{ items: Goal[] }>('/learning/learning_goal?limit=100'),
      api<{ items: { kind: string; scope_id: string | null; active: boolean }[] }>('/ai/grants'),
    ])
    goals.value = found.items
    if (!scope.value && goals.value.length) scope.value = goals.value[0]!.id
    refreshGrant(grants.items)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = '' }
}

let grantCache: { kind: string; scope_id: string | null; active: boolean }[] = []
function refreshGrant(items?: { kind: string; scope_id: string | null; active: boolean }[]) {
  if (items) grantCache = items
  granted.value = grantCache.some(g => g.kind === 'local_write' && g.active && g.scope_id === scope.value)
  outbound.value = grantCache.some(g => g.kind === 'outbound' && g.active)
}
// Switching goals switches the authorization too; the state must follow the selection.
watch(scope, () => refreshGrant())

async function makePlan() {
  if (!scope.value || !question.value.trim()) { error.value = '请先选择学习目标并写下你的想法。'; return }
  busy.value = 'plan'; error.value = ''; plan.value = undefined; run.value = undefined
  try {
    const conversation = await api<{ id: string }>('/ai/conversations', {
      method: 'POST', body: JSON.stringify({ scope_id: scope.value, title: question.value.slice(0, 120) }),
    })
    plan.value = await api<Plan>(`/ai/conversations/${conversation.id}/plan`, {
      method: 'POST',
      body: JSON.stringify({ conversation_id: conversation.id, scope_id: scope.value,
                             question: question.value, intent: intent.value }),
    })
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = '' }
}

async function adopt() {
  if (!plan.value) return
  busy.value = 'adopt'; error.value = ''
  try {
    run.value = await api<Run>(`/ai/plans/${plan.value.id}/adopt`, { method: 'POST', body: '{}' })
    if (run.value.status === 'success') toast.push('计划已采用，已写入学习与任务')
    else if (run.value.status === 'partial') toast.error('部分步骤未完成，请查看下面的结果。')
    else toast.error('计划没有执行成功，原因见下方。')
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = '' }
}

async function loadReview() {
  if (!scope.value) return
  busy.value = 'review'; error.value = ''
  try { review.value = await api(`/ai/review`, { method: 'POST', body: JSON.stringify({ scope_id: scope.value }) }) }
  catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = '' }
}

const planned = computed(() => plan.value?.steps.filter(s => s.capability_id !== 'notes.create') || [])

onMounted(load)
</script>

<template>
  <section class="panel ai-workspace">
    <div class="page-heading">
      <div>
        <h1>AI 工作台</h1>
        <p class="muted">先说你想学什么，系统给出可编辑的计划；采用后写入真实的学习与任务，可以在「学习」里打开。</p>
      </div>
      <HubButton variant="quiet" size="sm" :loading="busy === 'load'" @click="load">刷新目标</HubButton>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <p v-if="!goals.length" class="empty-state">
      还没有学习目标。请先在「学习」里创建一个目标，再回到这里生成计划。
    </p>

    <template v-else>
      <label class="form-field">
        <span>学习目标</span>
        <select v-model="scope" @change="refreshGrant()">
          <option v-for="goal in goals" :key="goal.id" :value="goal.id">{{ goal.title }}</option>
        </select>
      </label>

      <label class="form-field">
        <span>你想学什么</span>
        <textarea v-model="question" rows="3" maxlength="4000"
          placeholder="例如：我想四周内做出一个能用的 RAG 小项目，每天四十分钟。我有 Python 基础。" />
      </label>
      <label class="form-field">
        <span>补充说明（可选）</span>
        <input v-model="intent" maxlength="4000" placeholder="例如：优先使用我已有的资料；周末不安排。" />
      </label>

      <p v-if="!outbound || !granted" class="fine-print notice-line">
        <template v-if="!outbound">
          还没有授权把内容发送给模型服务，因此暂时不能生成计划。到
          <router-link class="text-link" to="/settings">设置 → AI 权限</router-link>
          开启「允许把资料发送给模型服务」；它只发送你选定的内容。
        </template>
        <template v-else>
          这个目标还没有开启写入授权，可以先生成计划看一看。采用前需要在
          <router-link class="text-link" to="/settings">设置 → AI 权限</router-link> 里为它开启。
        </template>
      </p>
      <div class="panel-actions">
        <HubButton variant="primary" :loading="busy === 'plan'" :disabled="!outbound" @click="makePlan">生成计划草稿</HubButton>
        <HubButton variant="quiet" :loading="busy === 'review'" @click="loadReview">看看进展</HubButton>
      </div>

      <section v-if="plan" class="preview-box plan-box">
        <h2>{{ plan.title }}</h2>
        <p class="muted">{{ plan.rationale }}</p>
        <ol class="plan-steps">
          <li v-for="step in planned" :key="step.position">
            <b>{{ stepName(step.capability_id) }}</b>
            <span class="muted"> · {{ step.arguments.title }}</span>
          </li>
        </ol>
        <p v-if="plan.rejected.length" class="fine-print warning-text">
          有 {{ plan.rejected.length }} 个步骤未通过校验，已跳过：
          {{ plan.rejected.map(r => r.detail).join('；') }}
        </p>
        <div class="panel-actions">
          <HubButton variant="quiet" @click="plan = undefined">放弃</HubButton>
          <HubButton variant="primary" :loading="busy === 'adopt'" :disabled="!granted" @click="adopt">采用计划</HubButton>
        </div>
      </section>

      <section v-if="run" class="preview-box run-box">
        <h2>执行结果</h2>
        <p class="muted">状态：{{ run.status === 'success' ? '全部完成' : run.status === 'partial' ? '部分完成' : '未完成' }}</p>
        <ul class="plain-list">
          <li v-for="step in run.steps" :key="step.position">
            {{ step.status === 'done' ? '✓' : '·' }} {{ step.summary || stepName(step.capability_id) }}
            <router-link v-if="step.resource_ids.length" :to="'/resource/' + step.resource_ids[0]" class="text-link">打开</router-link>
          </li>
        </ul>
      </section>

      <section v-if="review" class="preview-box">
        <h2>当前进展</h2>
        <p>{{ review.observation }}</p>
        <p class="fine-print muted">{{ review.notice }}</p>
        <ul class="plain-list">
          <li v-for="(item, index) in review.next" :key="index">
            <b>{{ item.action }}</b> <span class="muted">— {{ item.reason }}</span>
          </li>
        </ul>
      </section>
    </template>
  </section>
</template>

<style scoped>
.ai-workspace { display: grid; gap: var(--space-3); }
.plan-box h2, .run-box h2 { font-size: var(--step-2); margin: 0 0 var(--space-2); }
.plan-steps { margin: 0; padding-left: 1.2rem; display: grid; gap: var(--space-1); }
.warning-text { color: var(--warning); }
.notice-line { margin: 0; padding: var(--space-2) var(--space-3); background: var(--surface-2); border-radius: var(--radius-sm); }
</style>
