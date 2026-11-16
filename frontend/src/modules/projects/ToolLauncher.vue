<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmAction } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import { api } from '../../shared/api'
import { type Resource, type Page, errorMessage } from '../../shared/resource'
import { useLatest, useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

const props = defineProps<{ project: Resource }>()
const emit = defineEmits<{ draft: [boolean] }>()

interface Profile { id: string; name: string; enabled: number; available: boolean }
interface Preview { id: string; title: string; profile_name: string; arguments: string[]; directory: string; expires_at: string; warning: string }
interface History { id: string; profile_name: string; status: string; error_code: string | null; created_at: string }

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

const labels: Record<string, string> = {
  started: '已发起，请到桌面确认', failed: '启动失败',
  uncertain: '结果不确定，请先检查桌面，不会自动重启', starting: '结果不确定，请先检查桌面',
}

const profiles = ref<Profile[]>([])
const profileId = ref('')
const preview = ref<Preview>()
const uncertain = ref(false)
const history = ref<Page<History>>({ items: [], total: 0, limit: 10, offset: 0 })
const error = ref('')

const dirty = computed(() => !!preview.value || writes.hasPending())
useUnsaved(() => dirty.value, emit)

async function load() {
  try {
    const [p, h] = await latest.track(() => Promise.all([
      api<{ items: Profile[] }>('/project-tools'),
      api<Page<History>>(`/project-tools/history?project_id=${props.project.id}&limit=10&offset=${history.value.offset}`),
    ]))
    profiles.value = p.items; history.value = h
  } catch (reason) { error.value = errorMessage(reason) }
}

async function prepare() {
  if (!profileId.value) return
  try {
    preview.value = await writes.send<Preview>('preview', '/project-tools/preview', {
      project_id: props.project.id, profile_id: profileId.value, expected_project_version: props.project.version,
    })
    uncertain.value = false
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function confirm() {
  if (!preview.value) return
  try {
    const result = await api<History>(`/project-tools/confirm/${preview.value.id}`, { method: 'POST', body: JSON.stringify({ confirmed: true }) })
    toast.push(labels[result.status] || result.status)
    preview.value = undefined; uncertain.value = false
    await load()
  } catch (reason) { toast.error(errorMessage(reason)); uncertain.value = true }
}

async function cancel() {
  if (uncertain.value && !(await confirmAction('启动可能已经发起，请先检查桌面和启动记录。关闭只会关闭预览，不会终止已打开的外部程序。', { title: '关闭启动预览？', confirmLabel: '仍然关闭', cancelLabel: '先去核对' }))) return
  preview.value = undefined; writes.discard(); uncertain.value = false
}

function turn(delta: number) { history.value.offset = Math.max(0, history.value.offset + delta); void load() }
onMounted(load)
</script>

<template>
  <section class="panel">
    <p class="fine-print">用已登记的本机应用打开项目目录。每次先预览，再确认启动。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <p v-if="project.status === 'archived'" class="muted fine-print">项目已归档，重启项目后可打开工具。</p>
    <p v-else-if="!project.local_directory" class="muted fine-print">先在项目基本信息中登记本地目录。</p>
    <template v-else>
      <p v-if="!profiles.some(p => p.enabled && p.available)" class="fine-print">
        暂无可用工具，请前往<router-link to="/settings" class="text-link">设置 → 项目工具</router-link>登记。
      </p>
      <div class="filter-row">
        <select v-model="profileId" aria-label="项目工具" :disabled="!!preview">
          <option value="">选择工具</option>
          <option v-for="p in profiles.filter(p => p.enabled && p.available)" :key="p.id" :value="p.id">{{ p.name }}</option>
        </select>
        <HubButton variant="secondary" size="sm" :disabled="!profileId || !!preview" :loading="writes.busy.value" @click="prepare">预览启动</HubButton>
      </div>
    </template>

    <section v-if="preview" class="preview-box">
      <h4>{{ preview.profile_name }} → {{ preview.title }}</h4>
      <p class="fine-print">{{ preview.warning }}</p>
      <p class="fine-print">目录：<code>{{ preview.directory }}</code></p>
      <p class="fine-print">有效至 {{ new Date(preview.expires_at).toLocaleTimeString() }}；目录或工具变化后需重新预览。</p>
      <details><summary>启动参数</summary><pre>{{ preview.arguments.join('\n') }}</pre></details>
      <p v-if="uncertain" class="fine-print">结果未确认。可检查桌面和启动记录后再查询，不会重复执行。</p>
      <div class="form-actions">
        <HubButton variant="quiet" @click="cancel">关闭</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" @click="confirm">{{ uncertain ? '查询原确认' : '确认打开' }}</HubButton>
      </div>
    </section>

    <h4>启动记录</h4>
    <p v-if="!history.items.length" class="muted fine-print">还没有启动记录。预览与取消不会启动应用。</p>
    <ul v-else class="plain-list">
      <li v-for="item in history.items" :key="item.id">
        <span>{{ item.profile_name }} · {{ labels[item.status] || item.status }}</span>
        <span class="muted fine-print">{{ new Date(item.created_at).toLocaleString('zh-CN') }}</span>
      </li>
    </ul>
    <div v-if="history.total > 10" class="pager">
      <HubButton variant="quiet" size="sm" :disabled="!history.offset" @click="turn(-10)">上一页</HubButton>
      <HubButton variant="quiet" size="sm" :disabled="history.offset + 10 >= history.total" @click="turn(10)">下一页</HubButton>
    </div>
  </section>
</template>
