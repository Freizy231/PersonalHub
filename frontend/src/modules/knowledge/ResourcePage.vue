<script setup lang="ts">
import { onMounted, ref } from 'vue'
import HubButton from '../../shared/HubButton.vue'
import ResourceContext from './ResourceContext.vue'
import DocumentPanel from '../library/documents/DocumentPanel.vue'
import CatalogPanel from '../library/CatalogPanel.vue'
import NotePanel from '../library/notes/NotePanel.vue'
import TaskPanel from '../life/tasks/TaskPanel.vue'
import EventPanel from '../life/events/EventPanel.vue'
import DietPanel from '../life/diet/DietPanel.vue'
import ProjectPanel from '../projects/ProjectPanel.vue'
import LearningPanel from '../learning/LearningPanel.vue'
import { api } from '../../shared/api'
import { type Resource, errorMessage, resourceKinds } from '../../shared/resource'
import { useUnsaved } from '../../shared/useUnsaved'

const props = defineProps<{ id: string; enabled: boolean }>()
const emit = defineEmits<{ draft: [boolean] }>()

const resource = ref<Resource>()
const error = ref('')
const editorDirty = ref(false)
const contextDirty = ref(false)
useUnsaved(() => editorDirty.value || contextDirty.value, emit)

async function load() {
  try { resource.value = await api<Resource>(`/resources/${props.id}`) }
  catch (reason) { error.value = errorMessage(reason) }
}

onMounted(load)
</script>

<template>
  <p v-if="error" class="panel-error" role="alert">{{ error }}</p>
  <template v-if="resource">
    <DocumentPanel v-if="resource.kind === 'document'" unified-search :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
    <CatalogPanel v-else-if="['book', 'bookmark', 'course'].includes(resource.kind)" unified-search :kind="resource.kind as 'book' | 'bookmark' | 'course'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
    <ProjectPanel v-else-if="resource.kind === 'project'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
    <LearningPanel v-else-if="['learning_goal', 'learning_path', 'knowledge_node'].includes(resource.kind)" :initial-id="id" :initial-kind="resource.kind" :enabled="enabled" @draft="editorDirty = $event" />
    <template v-else>
      <NotePanel v-if="resource.kind === 'note'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
      <DietPanel v-else-if="resource.kind === 'recipe'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
      <TaskPanel v-else-if="resource.kind === 'task'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
      <EventPanel v-else-if="resource.kind === 'event'" :initial-id="id" :enabled="enabled" @draft="editorDirty = $event" />
      <template v-else>
        <div class="page-heading">
          <div><div class="eyebrow">{{ resourceKinds[resource.kind] || '资料' }}</div><h1>{{ resource.title }}</h1></div>
          <HubButton variant="quiet" size="sm" @click="$router.back()">← 返回</HubButton>
        </div>
        <ResourceContext :resource="resource" @draft="contextDirty = $event" @changed="load" />
      </template>
    </template>
  </template>
</template>
