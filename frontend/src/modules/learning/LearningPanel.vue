<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import RecordEditor from '../../shared/RecordEditor.vue'
import ResourceContext from '../knowledge/ResourceContext.vue'
import { api } from '../../shared/api'
import { type Resource, type Field, errorMessage, label, resourceKinds } from '../../shared/resource'
import { useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const props = defineProps<{ enabled: boolean; initialId?: string; initialKind?: string }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Tree { path: Resource; nodes: Resource[]; progress: { verified: number; total: number; denominator: string } }
interface Assessment { id: string; mastery: string; criteria_snapshot: string; conclusion: string; created_at: string; evidence: { resource_id: string; title_snapshot: string; changed: boolean }[] }
interface NodeContext { node: Resource; assessments: Assessment[]; sessions: { id: string; duration_seconds: number; started_at: string }[]; tasks: { id: string; title: string; status: string; planned_date: string }[] }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const goals = ref<Resource[]>([])
const paths = ref<Resource[]>([])
const trashNodes = ref<Resource[]>([])
const tree = ref<Tree>()
const selected = ref<Resource>()
const context = ref<NodeContext>()
const reviews = ref<{ id: string; note_id: string; title: string; period_start: string; period_end: string }[]>([])
const pathId = ref('')
const trash = ref(false)
const loading = ref(false)
const error = ref('')

const kindNames: Record<string, string> = { learning_goal: '目标', learning_path: '路线', knowledge_node: '知识点' }

const editKind = ref('')
const editing = ref<Resource>()
const childDirty = ref(false)
const formDirty = ref(false)
const dirty = computed(() => childDirty.value || formDirty.value || writes.hasPending())
useUnsaved(() => dirty.value, emit)

/* ------------------------------------------------------------------ forms */
const mastery = ref('exposed')
const conclusion = ref('')
const evidence = ref<string[]>([])
const evidenceQuery = ref('')
const evidenceResults = ref<Resource[]>([])
const practiceForm = ref({ title: '', description: '', planned_date: '' })
const sessionForm = ref({ start: '', end: '' })
const reviewForm = ref({ title: '', body: '', period_start: '', period_end: '' })

const fields = computed<Field[]>(() => {
  const common: Field[] = [{ key: 'description', label: '说明', type: 'textarea' }]
  if (editKind.value === 'learning_goal') return [...common,
    { key: 'target_date', label: '目标日期', type: 'date', nullable: true },
    { key: 'acceptance_criteria', label: '验收标准', type: 'textarea' },
    { key: 'status', label: '状态', type: 'select', default: 'active', options: [{ value: 'active', label: '进行中' }, { value: 'completed', label: '完成' }, { value: 'paused', label: '暂停' }] },
  ]
  if (editKind.value === 'learning_path') return [...common,
    { key: 'goal_id', label: '所属目标', nullable: true, type: 'select', options: [{ value: '', label: '不关联' }, ...goals.value.map(r => ({ value: r.id, label: r.title }))] },
    { key: 'status', label: '状态', type: 'select', default: 'active', options: [{ value: 'active', label: '进行中' }, { value: 'completed', label: '完成' }, { value: 'paused', label: '暂停' }] },
  ]
  return [...common,
    { key: 'path_id', label: '所属路线', required: true, type: 'select', default: pathId.value, options: paths.value.map(r => ({ value: r.id, label: r.title })) },
    { key: 'parent_id', label: '上级知识点', type: 'select', nullable: true, options: [{ value: '', label: '根知识点' }, ...(tree.value?.nodes || []).filter(r => r.id !== editing.value?.id).map(r => ({ value: r.id, label: r.title }))] },
    { key: 'position', label: '同级顺序', type: 'number', default: 0, min: 0 },
    { key: 'acceptance_criteria', label: '可检查的验收标准', type: 'textarea' },
  ]
})

function clearForms() {
  childDirty.value = false; formDirty.value = false; writes.discard()
  conclusion.value = ''; evidence.value = []
  practiceForm.value = { title: '', description: '', planned_date: '' }
  sessionForm.value = { start: '', end: '' }
  reviewForm.value = { title: '', body: '', period_start: '', period_end: '' }
}

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const [g, p] = await latest.track(() => Promise.all([
      api<{ items: Resource[] }>(`/learning/learning_goal?trash=${trash.value}&limit=100`),
      api<{ items: Resource[] }>(`/learning/learning_path?trash=${trash.value}&limit=100`),
    ]))
    goals.value = g.items; paths.value = p.items
    if (trash.value) trashNodes.value = (await api<{ items: Resource[] }>('/learning/knowledge_node?trash=true&limit=100')).items
    if (pathId.value && !trash.value) {
      tree.value = await api<Tree>(`/learning/paths/${pathId.value}/tree`)
      reviews.value = (await api<{ items: typeof reviews.value }>(`/learning/paths/${pathId.value}/reviews`)).items
    }
  } catch (reason) { error.value = errorMessage(reason) }
  finally { loading.value = false }
}

async function toggleTrashView(next: boolean, box?: HTMLInputElement) {
  if (dirty.value && !(await confirmDiscard('学习记录草稿'))) {
    if (box) box.checked = trash.value   // the browser already flipped it; put it back to the real state
    return
  }
  clearForms(); selected.value = undefined; context.value = undefined; editKind.value = ''
  trash.value = next; pathId.value = ''; tree.value = undefined
  await load()
}

async function pickPath(id: string) {
  if (dirty.value && !(await confirmDiscard('未保存的学习记录草稿'))) return
  clearForms(); pathId.value = id; selected.value = undefined; context.value = undefined; editKind.value = ''
  await load()
}

async function pickNode(node: Resource) {
  if (dirty.value && !(await confirmDiscard('未保存的学习记录草稿'))) return
  clearForms(); editKind.value = ''
  try { context.value = await api<NodeContext>(`/learning/nodes/${node.id}/context`); selected.value = context.value.node }
  catch (reason) { error.value = errorMessage(reason) }
}

async function edit(kind: string, record?: Resource) {
  if (dirty.value && !(await confirmDiscard('未保存的学习记录草稿'))) return
  clearForms(); editKind.value = kind; editing.value = record
}

async function onSaved(value: Record<string, unknown>) {
  const record = value as Resource
  editKind.value = ''; clearForms()
  toast.push('已保存')
  if (record.kind === 'learning_path') pathId.value = record.id
  await load()
  if (record.kind === 'knowledge_node') await pickNode(record)
}

async function action(slot: string, path: string, body: Record<string, unknown>) {
  try {
    await writes.send(slot, path, body)
    formDirty.value = false
    await load()
    if (selected.value) {
      context.value = await api<NodeContext>(`/learning/nodes/${selected.value.id}/context`)
      selected.value = context.value.node
    }
    return true
  } catch (reason) { toast.error(errorMessage(reason)); return false }
}

async function assess() {
  if (!selected.value) return
  if (await action('assessment', `/learning/nodes/${selected.value.id}/assessments`, {
    expected_version: selected.value.version, mastery: mastery.value, conclusion: conclusion.value, evidence: evidence.value,
  })) { conclusion.value = ''; evidence.value = [] }
}

async function practice() {
  if (!selected.value) return
  if (await action('practice', `/learning/nodes/${selected.value.id}/practice`, {
    expected_version: selected.value.version, ...practiceForm.value, planned_date: practiceForm.value.planned_date || null,
  })) practiceForm.value = { title: '', description: '', planned_date: '' }
}

async function session() {
  if (!selected.value || !sessionForm.value.start || !sessionForm.value.end) { toast.error('请填写起止时间。'); return }
  if (await action('session', '/learning/sessions', {
    node_id: selected.value.id,
    started_at: new Date(sessionForm.value.start).toISOString(),
    ended_at: new Date(sessionForm.value.end).toISOString(),
  })) sessionForm.value = { start: '', end: '' }
}

async function review() {
  if (!tree.value) return
  if (await action('review', `/learning/paths/${tree.value.path.id}/reviews`, {
    expected_version: tree.value.path.version, ...reviewForm.value,
  })) reviewForm.value = { title: '', body: '', period_start: '', period_end: '' }
}

async function findEvidence() {
  try { evidenceResults.value = (await api<{ items: Resource[] }>(`/resources?q=${encodeURIComponent(evidenceQuery.value)}&limit=50`)).items }
  catch (reason) { toast.error(errorMessage(reason)) }
}

async function toggle(record: Resource) {
  if (dirty.value) return
  try {
    await api(`/learning/${record.kind}/${record.id}/${record.deleted_at ? 'restore' : 'trash'}`, { method: 'POST', body: JSON.stringify({ expected_version: record.version }) })
    clearForms(); selected.value = undefined; context.value = undefined
    toast.push(record.deleted_at ? '已恢复' : '已移入回收站')
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

function mark() { formDirty.value = true }

onMounted(async () => {
  await load()
  if (props.initialId && props.initialKind) {
    try {
      const record = await api<Resource>(`/learning/${props.initialKind}/${props.initialId}`)
      if (record.kind === 'knowledge_node') { await pickPath(String(record.path_id)); await pickNode(record) }
      else if (record.kind === 'learning_path') await pickPath(record.id)
      else edit('learning_goal', record)
    } catch (reason) { error.value = errorMessage(reason) }
  }
})
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <span class="muted fine-print">选一条路线查看知识树与验收记录。</span>
      <div class="panel-actions">
        <label class="checkbox-field"><input :checked="trash" type="checkbox" @change="toggleTrashView(($event.target as HTMLInputElement).checked, $event.target as HTMLInputElement)" /> 回收站</label>
        <HubButton variant="secondary" size="sm" :disabled="dirty || !enabled" @click="edit('learning_goal')">＋ 目标</HubButton>
        <HubButton variant="secondary" size="sm" :disabled="dirty || !enabled" @click="edit('learning_path')">＋ 路线</HubButton>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading && !goals.length" class="muted fine-print" role="status">读取中…</p>

    <div class="learning-columns">
      <aside>
        <h3>目标</h3>
        <p v-if="!goals.length" class="muted fine-print">先明确想达成什么。</p>
        <ul class="plain-list">
          <li v-for="goal in goals" :key="goal.id">
            <div>
              <strong>{{ goal.title }}</strong>
              <div class="task-meta"><span>{{ label(goal.status) }}</span><span v-if="goal.target_date">{{ goal.target_date }}</span></div>
            </div>
            <button v-if="!goal.deleted_at" class="plain-action" :disabled="dirty" @click="edit('learning_goal', goal)">编辑</button>
            <button class="plain-action muted" :disabled="dirty" @click="toggle(goal)">{{ goal.deleted_at ? '恢复' : '删除' }}</button>
          </li>
        </ul>

        <h3>路线</h3>
        <p v-if="!paths.length" class="muted fine-print">路线用于组织知识点。</p>
        <ul class="plain-list">
          <li v-for="path in paths" :key="path.id">
            <button v-if="!path.deleted_at" class="task-title" :class="{ active: pathId === path.id }" :disabled="dirty" @click="pickPath(path.id)">{{ path.title }}</button>
            <span v-else>{{ path.title }}</span>
            <button v-if="!path.deleted_at" class="plain-action" :disabled="dirty" @click="edit('learning_path', path)">编辑</button>
            <button class="plain-action muted" :disabled="dirty" @click="toggle(path)">{{ path.deleted_at ? '恢复' : '删除' }}</button>
          </li>
        </ul>
      </aside>

      <div>
        <RecordEditor
          v-if="editKind"
          :key="editKind + (editing?.id || 'new')"
          :endpoint="'/learning/' + editKind"
          :record="editing"
          :fields="fields"
          :title="'编辑' + kindNames[editKind]"
          @draft="d => childDirty = d"
          @saved="onSaved"
          @close="editKind = ''; childDirty = false"
        />

        <template v-else-if="trash">
          <h3>已删除知识点</h3>
          <p v-if="!trashNodes.length" class="muted fine-print">回收站为空。</p>
          <ul class="plain-list">
            <li v-for="node in trashNodes" :key="node.id">
              <span>{{ node.title }}</span>
              <button class="plain-action" @click="toggle(node)">恢复</button>
            </li>
          </ul>
          <p class="fine-print">请先恢复所属目标、路线和上级知识点。</p>
        </template>

        <template v-else-if="tree">
          <div class="panel-toolbar">
            <h2>{{ tree.path.title }}</h2>
            <HubButton variant="secondary" size="sm" :disabled="dirty" @click="edit('knowledge_node')">＋ 知识点</HubButton>
          </div>
          <div class="progress-line">
            <span>{{ tree.progress.verified }} / {{ tree.progress.total }}</span>
            <span class="muted">已验收</span>
          </div>
          <p class="fine-print">{{ tree.progress.denominator }}</p>

          <ul class="knowledge-tree">
            <li v-for="node in tree.nodes" :key="node.id" :style="{ marginLeft: Math.min(Number((node as any).depth || 0), 10) * 18 + 'px' }">
              <button class="task-title" :class="{ active: selected?.id === node.id }" @click="pickNode(node)">{{ node.title }}</button>
              <span class="muted fine-print">{{ label(node.mastery) }}</span>
              <button class="plain-action" :disabled="dirty" @click="edit('knowledge_node', node)">编辑</button>
              <button class="plain-action muted" :disabled="dirty" @click="toggle(node)">删除</button>
            </li>
          </ul>

          <section v-if="selected && context" class="record-details">
            <h3>{{ selected.title }} · {{ label(selected.mastery) }}</h3>
            <p v-if="selected.description">{{ selected.description }}</p>
            <p class="fine-print"><strong>验收标准：</strong>{{ selected.acceptance_criteria || '尚未填写，不能标为已验收' }}</p>

            <details>
              <summary>练习任务</summary>
              <ul class="plain-list">
                <li v-for="task in context.tasks" :key="task.id">
                  <router-link :to="'/resource/' + task.id" class="text-link">{{ task.title }}</router-link>
                  <span class="muted fine-print">{{ label(task.status) }} · {{ task.planned_date || '未排日期' }}</span>
                </li>
              </ul>
              <form @submit.prevent="practice">
                <fieldset :disabled="writes.busy.value">
                  <label class="form-field"><span>练习任务</span><input v-model="practiceForm.title" required maxlength="500" @input="mark" /></label>
                  <label class="form-field"><span>要求</span><textarea v-model="practiceForm.description" maxlength="10000" rows="2" @input="mark" /></label>
                  <label class="form-field"><span>计划日</span><input v-model="practiceForm.planned_date" type="date" @input="mark" /></label>
                  <HubButton variant="secondary" size="sm" type="submit" :loading="writes.busy.value">创建任务</HubButton>
                </fieldset>
              </form>
            </details>

            <details>
              <summary>学习时长 ({{ context.sessions.length }})</summary>
              <ul class="plain-list">
                <li v-for="item in context.sessions" :key="item.id">
                  <span>{{ new Date(item.started_at).toLocaleString('zh-CN') }} · {{ Math.round(item.duration_seconds / 60) }} 分钟</span>
                </li>
              </ul>
              <form @submit.prevent="session">
                <fieldset :disabled="writes.busy.value">
                  <div class="form-grid">
                    <label class="form-field"><span>开始</span><input v-model="sessionForm.start" type="datetime-local" required @input="mark" /></label>
                    <label class="form-field"><span>结束</span><input v-model="sessionForm.end" type="datetime-local" required @input="mark" /></label>
                  </div>
                  <HubButton variant="secondary" size="sm" type="submit" :loading="writes.busy.value">保存时长</HubButton>
                </fieldset>
              </form>
            </details>

            <details open>
              <summary>阶段验收</summary>
              <form @submit.prevent="assess">
                <fieldset :disabled="writes.busy.value">
                  <label class="form-field"><span>掌握程度</span>
                    <select v-model="mastery" @change="mark">
                      <option value="unlearned">未学习</option><option value="exposed">已接触</option>
                      <option value="practiced">已练习</option><option value="verified">已验收（需要证据）</option>
                    </select>
                  </label>
                  <label class="form-field"><span>结论 / 验收过程</span><textarea v-model="conclusion" maxlength="10000" rows="3" required @input="mark" /></label>
                  <div class="filter-row">
                    <input v-model="evidenceQuery" placeholder="查找证据" maxlength="256" @keydown.enter.prevent="findEvidence" />
                    <HubButton variant="quiet" size="sm" @click="findEvidence">查找</HubButton>
                  </div>
                  <label v-for="record in evidenceResults" :key="record.id" class="evidence-option">
                    <input v-model="evidence" type="checkbox" :value="record.id" @change="mark" />
                    {{ record.title }} <small class="muted">{{ resourceKinds[record.kind] || '资料' }}</small>
                  </label>
                  <HubButton variant="primary" size="sm" type="submit" :loading="writes.busy.value">保存验收</HubButton>
                </fieldset>
              </form>
              <ol class="assessment-list">
                <li v-for="assessment in context.assessments" :key="assessment.id">
                  <strong>{{ label(assessment.mastery) }} · {{ new Date(assessment.created_at).toLocaleString('zh-CN') }}</strong>
                  <p class="fine-print">标准快照：{{ assessment.criteria_snapshot }}</p>
                  <p>{{ assessment.conclusion }}</p>
                  <ul class="plain-list">
                    <li v-for="item in assessment.evidence" :key="item.resource_id">
                      <router-link :to="'/resource/' + item.resource_id" class="text-link">{{ item.title_snapshot }}</router-link>
                      <span v-if="item.changed" class="muted fine-print">（资料已修改，证据保持原版本）</span>
                    </li>
                  </ul>
                </li>
              </ol>
            </details>
          </section>

          <details>
            <summary>路线复盘</summary>
            <ul class="plain-list">
              <li v-for="reviewItem in reviews" :key="reviewItem.id">
                <router-link :to="'/resource/' + reviewItem.note_id" class="text-link">{{ reviewItem.title }}</router-link>
                <span class="muted fine-print">{{ reviewItem.period_start }} – {{ reviewItem.period_end }}</span>
              </li>
            </ul>
            <form @submit.prevent="review">
              <fieldset :disabled="writes.busy.value">
                <label class="form-field"><span>标题</span><input v-model="reviewForm.title" required maxlength="500" @input="mark" /></label>
                <div class="form-grid">
                  <label class="form-field"><span>开始日</span><input v-model="reviewForm.period_start" type="date" required @input="mark" /></label>
                  <label class="form-field"><span>结束日</span><input v-model="reviewForm.period_end" type="date" required @input="mark" /></label>
                </div>
                <label class="form-field"><span>复盘内容</span><textarea v-model="reviewForm.body" rows="6" required maxlength="100000" @input="mark" /></label>
                <HubButton variant="secondary" size="sm" type="submit" :loading="writes.busy.value">保存复盘</HubButton>
              </fieldset>
            </form>
          </details>

          <ResourceContext v-if="selected" :key="selected.id" :resource="selected" @draft="d => childDirty = d" @changed="pickNode(selected)" />
        </template>

        <p v-else class="empty-state">选择一条路线，或先创建学习目标与路线。</p>
      </div>
    </div>
  </section>
</template>
