<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import { api, ApiError } from '../../shared/api'
import { useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'
import type { Assignment, Tag } from './types'

const props = defineProps<{ resourceId: string }>()
const emit = defineEmits<{ draft: [value: boolean]; saved: []; busy: [value: boolean] }>()

const toast = useToast()
const writes = useIdempotent()
const current = ref<Assignment>()
const names = ref('')
const loading = ref(false)
const error = ref('')
const renaming = ref<Tag>()
const renameName = ref('')

const initialNames = computed(() => current.value?.tags.map(t => t.name).join('\n') ?? '')
const changedNames = computed(() => !!current.value && names.value !== initialNames.value)
const renameDirty = computed(() => !!renaming.value && renameName.value !== renaming.value.name)
useUnsaved(() => changedNames.value || renameDirty.value || writes.hasPending(), emit)

function message(reason: unknown) { return reason instanceof Error ? reason.message : '标签操作未完成，请重试。' }

async function load() {
  loading.value = true; error.value = ''
  try {
    const result = await api<Assignment>(`/resources/${props.resourceId}/tags`)
    current.value = result
    names.value = result.tags.map(t => t.name).join('\n')
    renaming.value = undefined
    writes.discard()
  } catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}

function beginRename(tag: Tag) {
  if (changedNames.value || writes.hasPending()) return
  renaming.value = tag; renameName.value = tag.name; error.value = ''
}
function cancelRename() { renaming.value = undefined }

async function saveNames() {
  if (!current.value) return
  const values = names.value.split('\n').map(s => s.trim()).filter(Boolean)
  if (values.length > 20) { error.value = '每项资料最多 20 个标签。'; return }
  try {
    const result = await writes.send<Assignment>('tags', `/resources/${props.resourceId}/tags`, {
      names: values,
      expected_version: current.value.resource_version,
      expected_tag_versions: Object.fromEntries(current.value.tags.map(t => [t.id, t.version])),
    }, 'PUT')
    current.value = result
    names.value = result.tags.map(t => t.name).join('\n')
    toast.push('标签已保存')
    emit('saved')
  } catch (reason) { error.value = message(reason) }
}

async function saveRename() {
  if (!renaming.value || !renameName.value.trim()) { error.value = '请填写标签名称。'; return }
  try {
    const updated = await writes.send<Tag>('rename', `/tags/${renaming.value.id}`, { name: renameName.value, expected_version: renaming.value.version }, 'PUT')
    if (current.value) {
      current.value = { ...current.value, tags: current.value.tags.map(t => t.id === updated.id ? updated : t) }
      names.value = current.value.tags.map(t => t.name).join('\n')
    }
    renaming.value = undefined
    toast.push('标签已改名')
    emit('saved')
  } catch (reason) {
    error.value = reason instanceof ApiError && reason.code === 'TAG_NAME_CONFLICT'
      ? '该名称已被另一标签使用，未合并或覆盖。'
      : message(reason)
  }
}

onMounted(load)
</script>

<template>
  <section class="panel" :aria-busy="loading || writes.busy.value">
    <p class="fine-print">标签按普通资料明文保存。每行一个，最多 20 个；同名会复用已有标签。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="loading" class="muted fine-print" role="status">读取中…</p>

    <template v-else-if="current">
      <form v-if="!renaming" @submit.prevent="saveNames">
        <label class="form-field"><span>标签名称（每行一个）</span>
          <textarea v-model="names" rows="6" maxlength="5120" />
        </label>
        <HubButton variant="primary" type="submit" :loading="writes.busy.value" :disabled="!changedNames">保存标签</HubButton>
      </form>

      <form v-else @submit.prevent="saveRename">
        <label class="form-field"><span>把「{{ renaming.name }}」改为</span>
          <input v-model="renameName" maxlength="256" autofocus />
        </label>
        <p class="fine-print">这是共享标签，改名会影响所有引用它的资料（当前 {{ renaming.usage_count ?? 0 }} 项）。</p>
        <div class="form-actions">
          <HubButton variant="quiet" @click="cancelRename">取消</HubButton>
          <HubButton variant="primary" type="submit" :loading="writes.busy.value" :disabled="!renameDirty">保存新名称</HubButton>
        </div>
      </form>

      <ul v-if="!renaming" class="tag-list">
        <li v-for="tag in current.tags" :key="tag.id">
          <span>#{{ tag.name }} <small class="muted">{{ tag.usage_count ?? '—' }} 项</small></span>
          <button class="plain-action" :disabled="changedNames" @click="beginRename(tag)">改名</button>
        </li>
      </ul>
      <p v-if="!renaming && !current.tags.length" class="muted fine-print">这项资料还没有标签。</p>
    </template>
  </section>
</template>
