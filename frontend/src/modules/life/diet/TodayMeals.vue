<script setup lang="ts">
import { onMounted, ref } from 'vue'
import HubButton from '../../../shared/HubButton.vue'
import { api } from '../../../shared/api'
import { localDate, slotNames, type Meal } from './types'

const props = defineProps<{ enabled: boolean }>()
const items = ref<Meal[]>([])
const loading = ref(false)
const error = ref('')
const day = ref(localDate())

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  day.value = localDate()
  try { items.value = (await api<{ items: Meal[] }>(`/diet/today?local_date=${day.value}`)).items }
  catch (reason) { error.value = reason instanceof Error ? reason.message : '三餐暂不可用。' }
  finally { loading.value = false }
}

onMounted(load)
</script>

<template>
  <section class="today-meals">
    <div class="task-heading"><h2>今日三餐</h2><router-link to="/diet" class="plain-action">管理 →</router-link></div>
    <p v-if="error" class="fine-print error-text" role="alert">{{ error }}</p>
    <p v-else-if="loading && !items.length" class="fine-print">读取中…</p>
    <template v-else>
      <div v-for="(label, slot) in slotNames" :key="slot">
        <strong class="slot-label">{{ label }}</strong>
        <ul>
          <li v-for="meal in items.filter(x => x.slot === slot)" :key="meal.id">
            <template v-for="(dish, i) in meal.dishes || []" :key="dish.id">
              <router-link v-if="dish.recipe_id" :to="'/resource/' + dish.recipe_id" class="text-link">{{ dish.name }}</router-link>
              <span v-else>{{ dish.name }}</span><span v-if="i < (meal.dishes || []).length - 1" class="muted">、</span>
            </template>
            <span class="muted"> · {{ meal.servings }} 份</span>
          </li>
          <li v-if="!items.some(x => x.slot === slot)" class="muted">未安排</li>
        </ul>
      </div>
    </template>
  </section>
</template>
