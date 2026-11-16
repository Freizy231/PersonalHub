<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { confirmDiscard } from '../../shared/confirm'
import HubButton from '../../shared/HubButton.vue'
import { api } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { useIdempotent } from '../../shared/requests'
import { useUnsaved } from '../../shared/useUnsaved'
import { useToast } from '../../shared/toast'

interface Profile { id: string; name: string; adapter: string; installation_id: string; enabled: number; version: number; available: boolean }
interface Installation { installation_id: string; adapter: string; label: string; executables: string[] }

const emit = defineEmits<{ draft: [boolean] }>()
const toast = useToast()
const writes = useIdempotent()

const profiles = ref<Profile[]>([])
const installations = ref<Installation[]>([])
const adapters = ref<{ id: string; label: string; available: boolean }[]>([])
const editing = ref<Profile>()
const name = ref('')
const installationId = ref('')
const changed = ref(false)
const error = ref('')

const dirty = computed(() => changed.value || writes.hasPending())
useUnsaved(() => dirty.value, emit)

async function load() {
  try {
    const data = await api<{ items: Profile[]; installations: Installation[]; adapters: typeof adapters.value }>('/project-tools')
    profiles.value = data.items; installations.value = data.installations; adapters.value = data.adapters
  } catch (reason) { error.value = errorMessage(reason) }
}

async function edit(profile: Profile) {
  if (dirty.value && !(await confirmDiscard('未保存的工具配置'))) return
  writes.discard()
  editing.value = profile; name.value = profile.name; installationId.value = profile.installation_id; changed.value = false; error.value = ''
}

async function save() {
  const installation = installations.value.find(i => i.installation_id === installationId.value)
  if (!installation) return
  try {
    await writes.send('tool', '/project-tools' + (editing.value ? '/' + editing.value.id : ''), {
      name: name.value, adapter: installation.adapter, installation_id: installation.installation_id,
      expected_version: editing.value?.version || 0,
    }, editing.value ? 'PUT' : 'POST')
    toast.push('工具配置已保存；启动项目仍需逐次预览确认')
    editing.value = undefined; changed.value = false
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function toggle(profile: Profile) {
  if (dirty.value) return
  try {
    await writes.send('tool-state', `/project-tools/${profile.id}/state`, { expected_version: profile.version, enabled: !profile.enabled })
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

function reset() { writes.discard(); editing.value = undefined; name.value = ''; installationId.value = ''; changed.value = false }

onMounted(load)
</script>

<template>
  <section class="settings-section">
    <h2>项目工具</h2>
    <p class="fine-print">登记本机应用，然后在项目中预览并确认打开。程序更新后请重新绑定；不接受自定义命令。</p>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <ul class="plain-list">
      <li v-for="profile in profiles" :key="profile.id">
        <span><strong>{{ profile.name }}</strong> · {{ profile.enabled ? '已启用' : '已停用' }} · {{ profile.available ? '可用' : '程序缺失，请重新绑定' }}</span>
        <button class="plain-action" @click="edit(profile)">绑定</button>
        <button class="plain-action" :disabled="dirty" @click="toggle(profile)">{{ profile.enabled ? '停用' : '启用' }}</button>
      </li>
    </ul>
    <p v-if="!profiles.length" class="muted fine-print">尚未登记工具。</p>
    <p v-for="adapter in adapters.filter(a => !a.available)" :key="adapter.id" class="muted fine-print">{{ adapter.label }}：未找到符合要求的安装，不会自动下载。</p>

    <form class="record-editor" @submit.prevent="save">
      <fieldset :disabled="writes.busy.value">
        <h3>{{ editing ? '更新工具配置' : '登记工具' }}</h3>
        <label class="form-field"><span>名称</span><input v-model="name" required maxlength="100" @input="changed = true" /></label>
        <label class="form-field"><span>已安装应用</span>
          <select v-model="installationId" required @change="changed = true">
            <option value="">选择已安装应用</option>
            <option v-for="i in installations.filter(i => editing ? i.adapter === editing.adapter : !profiles.some(p => p.adapter === i.adapter))" :key="i.installation_id" :value="i.installation_id">
              {{ i.label }} · {{ i.executables.join(' → ') }}
            </option>
          </select>
        </label>
        <p class="fine-print">每类应用保留一份配置。Codex 仅打开只读界面，不自动发送任务。</p>
        <div class="form-actions">
          <HubButton v-if="editing || dirty" variant="quiet" @click="reset">取消编辑</HubButton>
          <HubButton variant="primary" type="submit" :loading="writes.busy.value" :disabled="!installationId">保存</HubButton>
        </div>
      </fieldset>
    </form>
  </section>
</template>
