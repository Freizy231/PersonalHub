<script setup lang="ts">
import { onMounted, ref } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import { api, apiBlob } from '../../shared/api'
import { errorMessage } from '../../shared/resource'
import { useIdempotent } from '../../shared/requests'
import { useToast } from '../../shared/toast'

const toast = useToast()
const writes = useIdempotent()

const error = ref('')
const profiles = ref<{ id: string; name: string; executable: string; version: number; enabled: number }[]>([])
const allowed = ref<{ executable: string; available: boolean }[]>([])
const history = ref<{ id: string; title: string; status: string; created_at: string }[]>([])
const name = ref('PDF 阅读器')
const executable = ref('')
const busy = ref(false)

async function load() {
  try {
    const result = await api<{ items: typeof profiles.value; allowed: typeof allowed.value }>('/readers')
    profiles.value = result.items
    allowed.value = result.allowed
    history.value = (await api<{ items: typeof history.value }>('/readers/history')).items
  } catch (reason) { error.value = errorMessage(reason) }
}

async function save() {
  busy.value = true
  try {
    await writes.send('reader', '/readers', { name: name.value, executable: executable.value })
    toast.push('阅读器已登记；启动前仍需预览确认')
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
  finally { busy.value = false }
}

async function toggle(profile: typeof profiles.value[number]) {
  try {
    await api(`/readers/${profile.id}`, {
      method: 'PUT',
      body: JSON.stringify({ name: profile.name, executable: profile.executable, enabled: !profile.enabled, expected_version: profile.version }),
    })
    await load()
  } catch (reason) { toast.error(errorMessage(reason)) }
}

async function exportAll() {
  busy.value = true; error.value = ''
  try {
    const blob = await apiBlob('/exports/ordinary')
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a'); link.href = url; link.download = 'personal-hub-ordinary.zip'; link.click()
    setTimeout(() => URL.revokeObjectURL(url), 1000)
  } catch (reason) { error.value = errorMessage(reason) }
  finally { busy.value = false }
}

onMounted(load)
</script>

<template>
  <section class="settings-section">
    <h2>备份与导出</h2>
    <p class="fine-print">
      普通导出含资料、关联、Markdown 与 PDF 原件，不含私密库、令牌或旧情报。大资料库请使用停服完整备份。
    </p>
    <HubButton variant="secondary" size="sm" :loading="busy" @click="exportAll">导出普通资料 ZIP</HubButton>

    <details>
      <summary>完整备份与恢复命令</summary>
      <p class="fine-print">完整备份含数据库、所有修订、PDF、提取文本、在途文件与私密密文。先停止服务，使用全新输出目录：</p>
      <pre>backend/.venv/bin/python -B -m backend.manage backup --data-root runtime/personal-hub --output runtime/backups/备份名</pre>
      <p class="fine-print">恢复验证只写入全新目录，不替换活动资料：</p>
      <pre>backend/.venv/bin/python -B -m backend.manage restore-check --backup runtime/backups/备份名 --output runtime/restore-checks/恢复名</pre>
      <p class="fine-print">同一磁盘的备份不能防磁盘损坏。私密库还需要原密码在本机解锁核对。</p>
    </details>

    <h2>本机 PDF 阅读器</h2>
    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
    <p v-if="!profiles.length" class="fine-print">尚未登记阅读器。</p>
    <ul class="plain-list">
      <li v-for="profile in profiles" :key="profile.id">
        <span>{{ profile.name }} · <code>{{ profile.executable }}</code> · {{ profile.enabled ? '已启用' : '已停用' }}</span>
        <button class="plain-action" @click="toggle(profile)">{{ profile.enabled ? '停用' : '启用' }}</button>
      </li>
    </ul>
    <form class="filter-row" @submit.prevent="save">
      <input v-model="name" placeholder="阅读器名称" required maxlength="100" />
      <select v-model="executable" aria-label="阅读器程序" required>
        <option value="">选择已安装程序</option>
        <option v-for="item in allowed" :key="item.executable" :value="item.executable" :disabled="!item.available">{{ item.executable }}{{ item.available ? '' : '（未安装）' }}</option>
      </select>
      <HubButton variant="secondary" size="sm" type="submit" :disabled="busy">登记</HubButton>
    </form>

    <details v-if="history.length">
      <summary>最近启动记录</summary>
      <ul class="plain-list">
        <li v-for="item in history" :key="item.id">
          <span>{{ item.title }} · {{ item.status }}</span>
          <span class="muted fine-print">{{ new Date(item.created_at).toLocaleString('zh-CN') }}</span>
        </li>
      </ul>
    </details>
  </section>
</template>
