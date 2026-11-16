<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import TagPanel from '../tags/TagPanel.vue'
import { api, ApiError } from '../../shared/api'
import { type Resource, errorMessage, resourceKinds, label } from '../../shared/resource'
import { useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const props = defineProps<{ resource: Resource; relationsReadOnly?: boolean }>()
const emit = defineEmits<{ changed: []; draft: [boolean] }>()

interface Edge { id: string; source_id: string; target_id: string; relation_type: string; resource_id: string; title: string }

const toast = useToast()
const writes = useIdempotent()
const context = ref<{ resource: Resource; relations: Edge[]; backlinks: Resource[]; outgoing: Resource[] }>()
const error = ref('')
const query = ref('')
const results = ref<Resource[]>([])
const target = ref('')
const relation = ref('related')
const showTags = ref(false)
const tagDirty = ref(false)

useUnsaved(() => tagDirty.value || writes.hasPending(), emit)

async function load() {
  try { context.value = await api<typeof context.value>(`/resources/${props.resource.id}/context`) }
  catch (reason) { error.value = errorMessage(reason) }
}
async function find() {
  try { results.value = (await api<{ items: Resource[] }>(`/resources?q=${encodeURIComponent(query.value)}&limit=50`)).items }
  catch (reason) { error.value = errorMessage(reason) }
}
async function add() {
  if (props.relationsReadOnly || !target.value || !context.value) return
  try {
    context.value = await writes.send<typeof context.value>('relation', `/resources/${props.resource.id}/relations`, {
      target_id: target.value,
      relation_type: relation.value,
      expected_version: context.value.resource.version,
    })
    target.value = ''
    toast.push('已建立关联')
    emit('changed')
  } catch (reason) { error.value = errorMessage(reason) }
}
async function remove(edge: Edge) {
  if (props.relationsReadOnly || !context.value) return
  try {
    context.value = await api<typeof context.value>(`/resources/${props.resource.id}/relations/${edge.id}/remove`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: context.value.resource.version }),
    })
    toast.push('已解除关联')
    emit('changed')
  } catch (reason) { error.value = errorMessage(reason) }
}
async function toggleTags() {
  if (tagDirty.value && !(await confirmDiscard('未保存的标签修改'))) return
  showTags.value = !showTags.value
}
onMounted(load)
</script>

<template>
  <section class="resource-context">
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <details>
      <summary>标签</summary>
      <div class="context-tags">
        <HubButton v-if="!showTags" variant="secondary" size="sm" @click="toggleTags">编辑标签</HubButton>
        <TagPanel v-if="showTags" :resource-id="resource.id" @draft="tagDirty = $event" @saved="load(); emit('changed')" />
      </div>
    </details>

    <details>
      <summary>关联资料 {{ context?.relations.length ? `(${context.relations.length})` : '' }}</summary>
      <ul class="plain-list">
        <li v-for="edge in context?.relations" :key="edge.id">
          <router-link :to="'/resource/' + edge.resource_id" class="text-link">{{ edge.title }}</router-link>
          <span class="muted fine-print">{{ label(edge.relation_type) }}</span>
          <button v-if="edge.source_id === resource.id && !relationsReadOnly" class="plain-action" @click="remove(edge)">解除</button>
        </li>
      </ul>
      <p v-if="!context?.relations.length" class="muted fine-print">还没有关联。</p>
      <form v-if="!relationsReadOnly" class="inline-form" @submit.prevent="add">
        <input v-model="query" placeholder="按名称查找资料" maxlength="256" @keydown.enter.prevent="find" />
        <button type="button" class="plain-action" @click="find">查找</button>
        <select v-model="target" aria-label="关联目标">
          <option value="">选择资料</option>
          <option v-for="r in results.filter(r => r.id !== resource.id)" :key="r.id" :value="r.id">{{ r.title }} · {{ resourceKinds[r.kind] || '资料' }}</option>
        </select>
        <select v-model="relation" aria-label="关联类型">
          <option value="related">相关</option>
          <option value="material">学习材料</option>
          <option v-if="resource.kind === 'knowledge_node'" value="practice">实际练习</option>
          <option value="output">成果 / 备注</option>
        </select>
        <HubButton variant="secondary" size="sm" :disabled="!target" @click="add">添加</HubButton>
      </form>
      <p v-else class="muted fine-print">项目已归档，关联仅供查阅。</p>
    </details>

    <details v-if="context?.outgoing?.length">
      <summary>Markdown 链接</summary>
      <ul class="plain-list">
        <li v-for="r in context.outgoing" :key="r.id"><router-link :to="'/resource/' + r.id" class="text-link">{{ r.title }}</router-link></li>
      </ul>
    </details>

    <details v-if="context?.backlinks?.length">
      <summary>反向链接 ({{ context.backlinks.length }})</summary>
      <ul class="plain-list">
        <li v-for="n in context.backlinks" :key="n.id"><router-link :to="'/resource/' + n.id" class="text-link">{{ n.title }}</router-link></li>
      </ul>
    </details>
  </section>
</template>
