<script setup lang="ts">
import { ref, watch } from 'vue'
import HubButton from './HubButton.vue'
import HubDialog from './HubDialog.vue'
import { api } from './api'
import { errorMessage } from './resource'

/**
 * Permanent removal of one trashed resource. The server decides whether it is allowed
 * and what it would destroy; this only shows that answer and asks once, explicitly.
 */
interface PurgePreview {
  id: string; kind: string; kind_label: string; title: string; version: number; trashed_at: string
  blockers: { reason: string; count: number }[]
  deletable: boolean
  removes: { relations: number; tags: number; revisions: number }
  files: string[]
  file_count: number
  notice: string
}

const props = defineProps<{ show: boolean; resourceId: string; title?: string }>()
const emit = defineEmits<{ 'update:show': [boolean]; purged: [string] }>()

const preview = ref<PurgePreview>()
const loading = ref(false)
const busy = ref(false)
const error = ref('')

async function load() {
  if (!props.resourceId) return
  loading.value = true; error.value = ''; preview.value = undefined
  try { preview.value = await api<PurgePreview>(`/trash/${props.resourceId}/purge-preview`) }
  catch (reason) { error.value = errorMessage(reason) }
  finally { loading.value = false }
}

watch(() => [props.show, props.resourceId], () => { if (props.show) void load(); else { preview.value = undefined; error.value = '' } }, { immediate: true })

function close() { if (!busy.value) emit('update:show', false) }

async function confirm() {
  if (!preview.value || !preview.value.deletable || busy.value) return
  busy.value = true; error.value = ''
  try {
    await api(`/trash/${preview.value.id}/purge`, {
      method: 'POST',
      body: JSON.stringify({ expected_version: preview.value.version }),
      headers: { 'Idempotency-Key': crypto.randomUUID() },
    })
    const id = preview.value.id
    preview.value = undefined
    emit('update:show', false)
    emit('purged', id)
  } catch (reason) { error.value = errorMessage(reason); await load() }
  finally { busy.value = false }
}

function when(value: string) { return new Date(value).toLocaleString('zh-CN') }
</script>

<template>
  <HubDialog :show="show" title="永久删除" :busy="busy" @update:show="v => { if (!v) close() }">
    <p v-if="loading" class="fine-print" role="status">正在核对这项内容的影响范围…</p>
    <p v-else-if="error" class="panel-error" role="alert">{{ error }}</p>

    <template v-else-if="preview">
      <p><strong>{{ preview.title }}</strong><span class="muted"> · {{ preview.kind_label }}</span></p>
      <p class="fine-print">已进入回收站：{{ when(preview.trashed_at) }}</p>

      <template v-if="preview.deletable">
        <p class="purge-warning">{{ preview.notice }}</p>
        <ul class="purge-list">
          <li v-for="(count, name) in preview.removes" :key="name" v-show="count">
            {{ name === 'relations' ? '关联' : name === 'tags' ? '标签' : '历史修订' }}：{{ count }} 项
          </li>
          <li v-if="preview.file_count">磁盘文件：{{ preview.file_count }} 个（正文或原件，会一并删除）</li>
          <li v-if="preview.kind === 'learning_goal' || preview.kind === 'learning_path' || preview.kind === 'knowledge_node'">
            下级内容与验收记录：如果仍在回收站，会一并删除
          </li>
        </ul>
        <p class="fine-print">删除后不进入备份，也无法撤销。如需保留，请先取消。</p>
      </template>

      <template v-else>
        <p class="purge-warning">现在还不能永久删除，下面这些内容仍在使用它：</p>
        <ul class="purge-list">
          <li v-for="blocker in preview.blockers" :key="blocker.reason">{{ blocker.reason }}（{{ blocker.count }} 项）</li>
        </ul>
        <p class="fine-print">请先解除这些关联（或先删除那些内容），再回来删除这一项。</p>
      </template>
    </template>

    <template #actions>
      <HubButton variant="quiet" :disabled="busy" @click="close">取消</HubButton>
      <HubButton
        variant="danger"
        :loading="busy"
        :disabled="loading || !preview?.deletable || !!error"
        @click="confirm"
      >永久删除</HubButton>
    </template>
  </HubDialog>
</template>

<style scoped>
.purge-warning { margin: var(--space-3) 0; padding: var(--space-2) var(--space-3); border-left: 2px solid var(--danger); background: color-mix(in srgb, var(--danger) 6%, transparent); color: var(--danger); border-radius: 0 var(--radius-sm) var(--radius-sm) 0; }
.purge-list { margin: 0 0 var(--space-3); padding-left: var(--space-5); color: var(--text-soft); font-size: var(--step-0); }
.purge-list li { margin: 3px 0; }
</style>
