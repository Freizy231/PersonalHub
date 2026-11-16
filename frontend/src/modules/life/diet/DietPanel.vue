<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { confirmAction, confirmDiscard } from '../../../shared/confirm'
import HubButton from '../../../shared/HubButton.vue'
import HubDialog from '../../../shared/HubDialog.vue'
import ResourcePicker from '../../../shared/ResourcePicker.vue'
import { api, ApiError } from '../../../shared/api'
import { isStale, useLatest, useIdempotent } from '../../../shared/requests'
import { useUnsaved } from '../../../shared/useUnsaved'
import { tabKey } from '../../../shared/useTabKeys'
import { useToast } from '../../../shared/toast'
import {
  unitNames, slotNames, localDate, addDays, weekOf,
  type Ingredient, type Line, type Recipe, type Meal, type Dish, type Shopping, type ShoppingItem, type Stock, type Preview, type Page, type Unit, type Slot,
} from './types'

const props = defineProps<{ enabled: boolean; initialId?: string }>()
const emit = defineEmits<{ draft: [boolean] }>()

const toast = useToast()
const latest = useLatest()
const writes = useIdempotent()

type Tab = 'meals' | 'recipes' | 'shopping' | 'stock'
type EditorKind = 'recipe' | 'meal' | 'shopping' | 'stock' | 'ingredient' | ''

const tabs: { value: Tab; label: string }[] = [
  { value: 'meals', label: '每周三餐' },
  { value: 'recipes', label: '食谱' },
  { value: 'shopping', label: '购物清单' },
  { value: 'stock', label: '库存' },
]

const tab = ref<Tab>('meals')
const week = ref(weekOf(localDate()))
const query = ref('')
const offset = ref(0)
const total = ref(0)
const trash = ref(false)
const favorites = ref(false)
const loading = ref(false)
const error = ref('')

const recipes = ref<Recipe[]>([])
const meals = ref<Meal[]>([])
const shopping = ref<Shopping>({ version: 0, items: [] })
const stock = ref<Stock[]>([])
const preview = ref<Preview | null>(null)
const selected = ref<string[]>([])

const days = computed(() => Array.from({ length: 7 }, (_, i) => addDays(week.value, i)))
const activeItems = computed(() => shopping.value.items.filter(i => !i.removed_at))
const removedItems = computed(() => shopping.value.items.filter(i => i.removed_at))

/* --------------------------------------------------------------- editor */
const kind = ref<EditorKind>('')
const editId = ref('')
const editVersion = ref(0)
const recipeForm = ref({ title: '', instructions: '', base_servings: 1, favorite: false, ingredients: [] as Line[] })
type DishForm = { recipe_id: string; label: string }
const mealForm = ref({ local_date: localDate(), slot: 'breakfast' as Slot, dishes: [] as DishForm[], servings: 1, note: '' })
const shopForm = ref({ ingredient_id: '', quantity: '1', unit: 'g' as Unit, note: '', purchased: false })
const stockForm = ref({ ingredient_id: '', quantity: '0', unit: 'g' as 'g' | 'ml' | 'piece' | 'portion' })
const ingredientName = ref('')
const baseline = ref('')
const editorOpen = computed(() => kind.value !== '')

const formSnapshot = computed(() => JSON.stringify([kind.value, editId.value, recipeForm.value, mealForm.value, shopForm.value, stockForm.value, ingredientName.value]))
const dirty = computed(() => editorOpen.value && formSnapshot.value !== baseline.value)
useUnsaved(() => dirty.value || writes.hasPending(), emit)

const ingredients = ref<Ingredient[]>([])
const ingredientQuery = ref('')
const ingredientOffset = ref(0)
const ingredientTotal = ref(0)
const recipesForPicker = ref<Recipe[]>([])
const recipeQuery = ref('')
const recipeOffset = ref(0)
const recipeTotal = ref(0)

function message(reason: unknown) { return reason instanceof Error ? reason.message : '饮食资料暂不可用。' }

async function load() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    if (tab.value === 'meals') {
      const result = await latest.track(() => api<{ items: Meal[] }>(`/diet/meals?week_start=${week.value}`))
      meals.value = result.items
    } else if (tab.value === 'recipes') {
      const params = new URLSearchParams({ q: query.value, offset: String(offset.value), limit: '20', trash: String(trash.value), favorite: String(favorites.value) })
      const result = await latest.track(() => api<Page<Recipe>>(`/diet/recipes?${params}`))
      recipes.value = result.items; total.value = result.total
      if (result.total > 0 && result.items.length === 0 && offset.value > 0) { offset.value = Math.max(0, offset.value - 20); await load(); return }
    } else if (tab.value === 'shopping') {
      const result = await latest.track(() => api<Shopping>(`/diet/shopping?week_start=${week.value}`))
      shopping.value = result
    } else {
      const params = new URLSearchParams({ q: query.value, offset: String(offset.value), limit: '20' })
      const result = await latest.track(() => api<Page<Stock>>(`/diet/stock?${params}`))
      stock.value = result.items; total.value = result.total
      if (result.total > 0 && result.items.length === 0 && offset.value > 0) { offset.value = Math.max(0, offset.value - 20); await load(); return }
    }
  } catch (reason) {
    if (!isStale(reason)) error.value = message(reason)
  } finally { loading.value = false }
}

async function loadIngredients(reset = false) {
  if (reset) ingredientOffset.value = 0
  try {
    const params = new URLSearchParams({ q: ingredientQuery.value, limit: '50', offset: String(ingredientOffset.value) })
    const result = await api<Page<Ingredient>>(`/diet/ingredients?${params}`)
    ingredients.value = result.items; ingredientTotal.value = result.total
  } catch (reason) { toast.error(message(reason)) }
}
async function loadRecipeChoices(reset = false) {
  if (reset) recipeOffset.value = 0
  try {
    const params = new URLSearchParams({ q: recipeQuery.value, limit: '50', offset: String(recipeOffset.value) })
    const result = await api<Page<Recipe>>(`/diet/recipes?${params}`)
    recipesForPicker.value = result.items; recipeTotal.value = result.total
  } catch (reason) { toast.error(message(reason)) }
}

async function begin(next: EditorKind, item?: Recipe | Meal | ShoppingItem | Stock | Ingredient) {
  if (dirty.value && !(await confirmDiscard('未保存的修改'))) return
  writes.discard()
  kind.value = next
  editId.value = (item as Recipe | undefined)?.id || ''
  editVersion.value = item && 'version' in item ? item.version : 0
  if (next === 'recipe') {
    const v = item as Recipe | undefined
    recipeForm.value = v
      ? { title: v.title, instructions: v.instructions, base_servings: v.base_servings, favorite: !!v.favorite, ingredients: v.ingredients.map(x => ({ ingredient_id: x.ingredient_id, quantity: x.quantity, unit: x.unit, name: x.name })) }
      : { title: '', instructions: '', base_servings: 1, favorite: false, ingredients: [] }
    void loadIngredients(true)
  }
  if (next === 'ingredient') { ingredientName.value = (item as Ingredient | undefined)?.name || ''; void loadIngredients(true) }
  if (next === 'meal') {
    const v = item as Meal | undefined
    mealForm.value = {
      local_date: v ? v.local_date : days.value[0]!,
      slot: v ? v.slot : 'breakfast',
      servings: v ? v.servings : 1,
      note: v ? v.note : '',
      dishes: v
        ? v.dishes.map(d => ({ recipe_id: d.recipe_id || '', label: d.label || '' }))
        : [{ recipe_id: '', label: '' }],
    }
    void loadRecipeChoices(true)
  }
  if (next === 'shopping') {
    const v = item as ShoppingItem | undefined
    shopForm.value = v ? { ingredient_id: v.ingredient_id, quantity: v.quantity, unit: v.unit, note: v.note, purchased: !!v.purchased } : { ingredient_id: '', quantity: '1', unit: 'g', note: '', purchased: false }
    editVersion.value = shopping.value.version
    void loadIngredients(true)
  }
  if (next === 'stock') {
    const v = item as Stock | undefined
    stockForm.value = v ? { ingredient_id: v.ingredient_id, quantity: v.quantity, unit: v.unit as 'g' | 'ml' | 'piece' | 'portion' } : { ingredient_id: '', quantity: '0', unit: 'g' }
    void loadIngredients(true)
  }
  baseline.value = formSnapshot.value
}

async function closeEditor() {
  if (writes.busy.value) return
  if (dirty.value && !(await confirmDiscard('未保存的饮食修改'))) return
  writes.discard(); kind.value = ''; editId.value = ''; baseline.value = ''
}

async function save() {
  const base = { expected_version: editVersion.value }
  try {
    if (kind.value === 'ingredient') {
      await writes.send('diet', `/diet/ingredients${editId.value ? '/' + editId.value : ''}`, { name: ingredientName.value, ...base }, editId.value ? 'PUT' : 'POST')
    } else if (kind.value === 'recipe') {
      await writes.send('diet', `/diet/recipes${editId.value ? '/' + editId.value : ''}`, {
        ...recipeForm.value,
        ingredients: recipeForm.value.ingredients.map(x => ({ ingredient_id: x.ingredient_id, quantity: x.quantity, unit: x.unit })),
        ...base,
      }, editId.value ? 'PUT' : 'POST')
    } else if (kind.value === 'meal') {
      const dishes = mealForm.value.dishes
        .map(d => d.recipe_id ? { recipe_id: d.recipe_id } : { label: d.label.trim() })
        .filter(d => ('recipe_id' in d && d.recipe_id) || ('label' in d && d.label))
      if (!dishes.length) { toast.error('至少填写一道菜或选择一个食谱。'); return }
      await writes.send('diet', `/diet/meals${editId.value ? '/' + editId.value : ''}`, {
        local_date: mealForm.value.local_date,
        slot: mealForm.value.slot,
        servings: mealForm.value.servings,
        note: mealForm.value.note,
        dishes,
        ...base,
      }, editId.value ? 'PUT' : 'POST')
    } else if (kind.value === 'shopping') {
      await writes.send('diet', `/diet/shopping/${week.value}${editId.value ? '/' + editId.value : ''}`, { ...shopForm.value, ...base }, editId.value ? 'PUT' : 'POST')
    } else if (kind.value === 'stock') {
      await writes.send('diet', '/diet/stock', { ...stockForm.value, ...base }, 'PUT')
    }
    kind.value = ''; editId.value = ''; baseline.value = ''
    toast.push('已保存')
    await load()
  } catch (reason) {
    toast.error(reason instanceof ApiError && reason.status < 500 ? message(reason) + ' 草稿已保留。' : message(reason))
  }
}

async function changeState(path: string) {
  try { await api(path, { method: 'POST' }); await load() }
  catch (reason) { toast.error(message(reason)) }
}
async function recipeState(item: Recipe) {
  try {
    await writes.send('diet-state', `/diet/recipes/${item.id}/${item.deleted_at ? 'restore' : 'trash'}`, { expected_version: item.version })
    toast.push(item.deleted_at ? '食谱已恢复' : '食谱已回收')
    await load()
  } catch (reason) { toast.error(message(reason)) }
}
async function removeMeal(item: Meal) {
  try { await writes.send('diet-state', `/diet/meals/${item.id}/remove`, { expected_version: item.version }); toast.push('安排已移除'); await load() }
  catch (reason) { toast.error(message(reason)) }
}
async function removeStock(item: Stock) {
  if (!(await confirmAction(`将删除「${item.name}」的这条库存记录。盘点数不参与其他计算，已确认的购物项不受影响，8 秒内可撤销。`, { title: '删除库存记录？', confirmLabel: '删除', danger: true }))) return
  try {
    await writes.send('diet-state', `/diet/stock/${item.id}/remove`, { expected_version: item.version })
    toast.push('库存记录已删除', async () => {
      await api('/diet/stock', { method: 'PUT', body: JSON.stringify({ ingredient_id: item.ingredient_id, unit: item.unit, quantity: item.quantity, expected_version: 0 }), headers: { 'Idempotency-Key': crypto.randomUUID() } })
      await load()
    })
    await load()
  } catch (reason) { toast.error(message(reason)) }
}

async function shoppingState(item: ShoppingItem) {
  try {
    await writes.send('diet-state', `/diet/shopping/${week.value}/${item.id}/state`, { expected_version: shopping.value.version, removed: !item.removed_at })
    await load()
  } catch (reason) { toast.error(message(reason)) }
}

async function makePreview() {
  if (!props.enabled) return
  loading.value = true; error.value = ''
  try {
    const result = await latest.track(() => api<Preview>(`/diet/suggestions?week_start=${week.value}`))
    preview.value = result
    selected.value = result.items.filter(i => Number(i.quantity) > 0).map(i => `${i.ingredient_id}:${i.unit}`)
  } catch (reason) { error.value = message(reason) }
  finally { loading.value = false }
}
async function confirmPreview() {
  if (!preview.value) return
  try {
    await writes.send('diet-confirm', `/diet/suggestions/${week.value}/confirm`, {
      expected_version: preview.value.version,
      digest: preview.value.digest,
      selected: selected.value.map(k => { const [ingredient_id, unit] = k.split(':'); return { ingredient_id, unit } }),
    })
    preview.value = null; selected.value = []
    toast.push('已加入购物清单')
    await load()
  } catch (reason) { toast.error(message(reason)) }
}

function moveWeek(delta: number) { week.value = addDays(week.value, delta); void load() }
function turn(delta: number) { offset.value = Math.max(0, offset.value + delta); void load() }

watch(() => [props.enabled, tab.value, trash.value], () => { offset.value = 0; void load() })
onMounted(async () => {
  await load()
  if (props.initialId) {
    try { const recipe = await api<Recipe>(`/diet/recipes/${props.initialId}`); tab.value = 'recipes'; begin('recipe', recipe) }
    catch (reason) { error.value = message(reason) }
  }
})
</script>

<template>
  <section class="panel">
    <div class="panel-toolbar">
      <div class="chip-row" role="tablist" aria-label="饮食视图">
        <button v-for="t in tabs" :key="t.value" role="tab" :aria-selected="tab === t.value" :tabindex="tab === t.value ? 0 : -1" @click="tab = t.value" @keydown="tabKey($event, tabs.map(x => x.value), tab, v => { tab = v as typeof tab })">{{ t.label }}</button>
      </div>
      <div class="panel-actions">
        <div v-if="tab === 'meals' || tab === 'shopping'" class="week-switch">
          <HubButton variant="quiet" size="sm" @click="moveWeek(-7)">←</HubButton>
          <span>{{ week }}</span>
          <HubButton variant="quiet" size="sm" @click="moveWeek(7)">→</HubButton>
        </div>
      </div>
    </div>

    <p v-if="error" class="panel-error" role="alert">{{ error }}</p>

    <!-- meals -->
    <template v-if="tab === 'meals' && !editorOpen">
      <div class="panel-actions" style="margin: 12px 0">
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="begin('meal')">＋ 安排一餐</HubButton>
      </div>
      <div class="diet-week">
        <section v-for="day in days" :key="day">
          <h3>{{ day }} <small v-if="day === localDate()">今天</small></h3>
          <template v-for="(slotLabel, slot) in slotNames" :key="slot">
            <strong class="slot-label">{{ slotLabel }}</strong>
            <ul>
              <li v-for="meal in meals.filter(x => x.local_date === day && x.slot === slot)" :key="meal.id">
                <div class="meal-dishes">
                  <template v-for="(dish, i) in meal.dishes" :key="dish.id">
                    <router-link v-if="dish.recipe_id" :to="'/resource/' + dish.recipe_id" class="text-link">{{ dish.name }}</router-link>
                    <span v-else class="meal-dish-text">{{ dish.name }}</span><span v-if="i < meal.dishes.length - 1" class="muted">、</span>
                  </template>
                </div>
                <span class="muted">{{ meal.servings }} 份<template v-if="meal.note"> · {{ meal.note }}</template></span>
                <button class="plain-action" @click="begin('meal', meal)">编辑</button>
                <button class="plain-action muted" @click="removeMeal(meal)">移除</button>
              </li>
              <li v-if="!meals.some(x => x.local_date === day && x.slot === slot)" class="muted">未安排</li>
            </ul>
          </template>
        </section>
      </div>
    </template>

    <!-- recipes / stock list -->
    <template v-if="(tab === 'recipes' || tab === 'stock') && !editorOpen">
      <div class="filter-row">
        <input v-model="query" placeholder="查找" maxlength="200" @keydown.enter.prevent="offset = 0; load()" />
        <label v-if="tab === 'recipes'" class="checkbox-field"><input v-model="favorites" type="checkbox" @change="offset = 0; load()" /> 常用</label>
        <label v-if="tab === 'recipes'" class="checkbox-field"><input v-model="trash" type="checkbox" @change="load()" /> 回收站</label>
        <HubButton variant="secondary" size="sm" @click="offset = 0; load()">查找</HubButton>
      </div>
      <div class="panel-actions" style="margin-bottom: 8px">
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="begin(tab === 'recipes' ? 'recipe' : 'stock')">{{ tab === 'recipes' ? '＋ 新建食谱' : '＋ 盘点库存' }}</HubButton>
        <HubButton variant="quiet" size="sm" @click="begin('ingredient')">登记食材</HubButton>
      </div>
      <p v-if="loading" class="muted fine-print" role="status">读取中…</p>
      <p v-else-if="!total" class="empty-state">{{ tab === 'recipes' ? '还没有食谱。先登记食材，再新建食谱。' : '还没有库存记录。' }}</p>
      <ul v-else class="diet-list">
        <li v-for="item in (tab === 'recipes' ? recipes : stock)" :key="item.id">
          <template v-if="tab === 'recipes'">
            <strong>{{ (item as Recipe).title }}</strong>
            <span class="muted">· {{ (item as Recipe).base_servings }} 份</span>
            <p>{{ (item as Recipe).ingredients.length }} 种食材</p>
            <button class="plain-action" @click="begin('recipe', item as Recipe)">编辑</button>
            <button class="plain-action muted" @click="recipeState(item as Recipe)">{{ (item as Recipe).deleted_at ? '恢复' : '删除' }}</button>
          </template>
          <template v-else>
            <strong>{{ (item as Stock).name }}</strong>
            <span class="muted">· {{ (item as Stock).quantity }} {{ unitNames[(item as Stock).unit] }}</span>
            <button class="plain-action" @click="begin('stock', item as Stock)">盘点</button>
            <button class="plain-action muted" @click="removeStock(item as Stock)">删除</button>
          </template>
        </li>
      </ul>
      <div v-if="total > 20" class="pager">
        <HubButton variant="quiet" size="sm" :disabled="!offset" @click="turn(-20)">上一页</HubButton>
        <span class="muted">{{ Math.floor(offset / 20) + 1 }} / {{ Math.ceil(total / 20) }}</span>
        <HubButton variant="quiet" size="sm" :disabled="offset + 20 >= total" @click="turn(20)">下一页</HubButton>
      </div>
    </template>

    <!-- shopping -->
    <template v-if="tab === 'shopping' && !editorOpen">
      <div class="panel-actions" style="margin: 12px 0">
        <HubButton variant="primary" size="sm" :disabled="!enabled" @click="makePreview">从菜单生成采购</HubButton>
        <HubButton variant="secondary" size="sm" :disabled="!enabled" @click="begin('shopping')">＋ 手动添加</HubButton>
      </div>

      <section v-if="preview" class="preview-box">
        <h3>采购建议</h3>
        <p class="fine-print">{{ preview.policy }}</p>
        <div class="diet-table">
          <table>
            <thead><tr><th>选择</th><th>食材 / 单位</th><th>需求</th><th>库存</th><th>已确认</th><th>建议追加</th></tr></thead>
            <tbody>
              <tr v-for="item in preview.items" :key="item.ingredient_id + item.unit">
                <td><input v-model="selected" type="checkbox" :value="item.ingredient_id + ':' + item.unit" :disabled="Number(item.quantity) <= 0" :aria-label="'选择' + item.name" /></td>
                <td>{{ item.name }} / {{ unitNames[item.unit] }}</td>
                <td>{{ item.required }}</td>
                <td>{{ item.stock }}</td>
                <td>{{ item.confirmed }}</td>
                <td>{{ item.quantity }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p v-if="!preview.items.length" class="muted fine-print">本周还没有可计算的食材需求。</p>
        <div class="form-actions">
          <HubButton variant="quiet" @click="preview = null; selected = []">关闭</HubButton>
          <HubButton variant="primary" :disabled="!selected.length" :loading="writes.busy.value" @click="confirmPreview">加入购物清单</HubButton>
        </div>
      </section>

      <p v-if="!activeItems.length && !loading" class="empty-state">本周还没有购物项。</p>
      <ul v-else class="diet-list">
        <li v-for="item in activeItems" :key="item.id">
          <strong>{{ item.name }}</strong>
          <span class="muted">· {{ item.quantity }} {{ unitNames[item.unit] }} · {{ item.purchased ? '已买' : '待买' }}</span>
          <button class="plain-action" @click="begin('shopping', item)">编辑</button>
          <button class="plain-action muted" @click="shoppingState(item)">移除</button>
        </li>
      </ul>

      <details v-if="removedItems.length">
        <summary>已移除 ({{ removedItems.length }})</summary>
        <ul class="diet-list">
          <li v-for="item in removedItems" :key="item.id">{{ item.name }} · {{ item.quantity }} {{ unitNames[item.unit] }} <button class="plain-action" @click="shoppingState(item)">恢复</button></li>
        </ul>
      </details>
    </template>

    <!-- editor -->
    <HubDialog :show="editorOpen" :title="({ recipe: '食谱', meal: '三餐安排', shopping: '购物项', stock: '库存盘点', ingredient: '食材' } as Record<string, string>)[kind] || ''" :busy="writes.busy.value" @update:show="v => { if (!v) closeEditor() }" @submit="save">
      <form id="diet-form" @submit.prevent="save">
        <template v-if="kind === 'ingredient'">
          <label class="form-field"><span>食材名称</span><input v-model="ingredientName" required maxlength="80" autofocus /></label>
        </template>

        <template v-if="kind === 'recipe'">
          <label class="form-field"><span>食谱名称</span><input v-model="recipeForm.title" required maxlength="200" autofocus /></label>
          <div class="form-grid">
            <label class="form-field"><span>基准份数</span><input v-model.number="recipeForm.base_servings" type="number" min="1" max="100" required /></label>
            <label class="checkbox-field"><input v-model="recipeForm.favorite" type="checkbox" /> 常用菜品</label>
          </div>
          <label class="form-field"><span>步骤</span><textarea v-model="recipeForm.instructions" maxlength="20000" rows="4" /></label>
          <h4>食材</h4>
          <div v-for="(line, index) in recipeForm.ingredients" :key="index" class="diet-line">
            <label class="form-field"><span>食材</span>
              <select v-model="line.ingredient_id" required>
                <option value="">选择</option>
                <option v-if="line.ingredient_id && !ingredients.some(i => i.id === line.ingredient_id)" :value="line.ingredient_id">{{ line.name || '保留当前' }}</option>
                <option v-for="i in ingredients" :key="i.id" :value="i.id">{{ i.name }}</option>
              </select>
            </label>
            <label class="form-field"><span>用量</span><input v-model="line.quantity" required inputmode="decimal" /></label>
            <label class="form-field"><span>单位</span>
              <select v-model="line.unit"><option v-for="(name, key) in unitNames" :key="key" :value="key">{{ name }}</option></select>
            </label>
            <button type="button" class="plain-action" @click="recipeForm.ingredients.splice(index, 1)">移除</button>
          </div>
          <HubButton variant="secondary" size="sm" :disabled="recipeForm.ingredients.length >= 100" @click="recipeForm.ingredients.push({ ingredient_id: '', quantity: '1', unit: 'g' })">＋ 添加食材行</HubButton>
        </template>

        <template v-if="kind === 'meal'">
          <div class="form-grid">
            <label class="form-field"><span>日期</span><input v-model="mealForm.local_date" type="date" :min="week" :max="addDays(week, 6)" required /></label>
            <label class="form-field"><span>餐次</span><select v-model="mealForm.slot"><option v-for="(label, key) in slotNames" :key="key" :value="key">{{ label }}</option></select></label>
          </div>
          <h4>这一餐吃什么</h4>
          <p class="fine-print">可以直接写菜名（例如「番茄炒蛋」），也可以关联已保存的食谱。关联食谱的菜会参与采购建议计算；手写菜名不会。</p>
          <div v-for="(dish, index) in mealForm.dishes" :key="index" class="diet-line">
            <label class="form-field dish-kind"><span>方式</span>
              <select :value="dish.recipe_id ? 'recipe' : 'text'" @change="dish.recipe_id = ($event.target as HTMLSelectElement).value === 'recipe' ? (recipesForPicker[0]?.id || '') : ''; dish.label = ''">
                <option value="text">手写菜名</option>
                <option value="recipe">关联食谱</option>
              </select>
            </label>
            <label v-if="dish.recipe_id !== ''" class="form-field"><span>食谱</span>
              <select v-model="dish.recipe_id">
                <option v-if="dish.recipe_id && !recipesForPicker.some(r => r.id === dish.recipe_id)" :value="dish.recipe_id">保留当前食谱</option>
                <option v-for="r in recipesForPicker" :key="r.id" :value="r.id">{{ r.title }}</option>
              </select>
            </label>
            <label v-else class="form-field"><span>菜名</span>
              <input v-model="dish.label" maxlength="200" placeholder="例如：番茄炒蛋" />
            </label>
            <button type="button" class="plain-action" :disabled="mealForm.dishes.length <= 1" @click="mealForm.dishes.splice(index, 1)">移除</button>
          </div>
          <HubButton variant="secondary" size="sm" :disabled="mealForm.dishes.length >= 30" @click="mealForm.dishes.push({ recipe_id: '', label: '' })">＋ 再加一道</HubButton>
          <div class="filter-row">
            <input v-model="recipeQuery" placeholder="查找食谱" maxlength="200" @keydown.enter.prevent="loadRecipeChoices(true)" />
            <HubButton variant="quiet" size="sm" @click="loadRecipeChoices(true)">查找</HubButton>
          </div>
          <label class="form-field"><span>份数</span><input v-model.number="mealForm.servings" type="number" min="1" max="100" required /></label>
          <label class="form-field"><span>备注</span><textarea v-model="mealForm.note" maxlength="1000" rows="2" /></label>
        </template>

        <template v-if="kind === 'shopping' || kind === 'stock'">
          <label v-if="kind === 'shopping'" class="form-field"><span>食材</span>
            <select v-model="shopForm.ingredient_id" required>
              <option value="">选择食材</option>
              <option v-if="shopForm.ingredient_id && !ingredients.some(i => i.id === shopForm.ingredient_id)" :value="shopForm.ingredient_id">保留当前食材</option>
              <option v-for="i in ingredients" :key="i.id" :value="i.id">{{ i.name }}</option>
            </select>
          </label>
          <label v-else class="form-field"><span>食材</span>
            <select v-model="stockForm.ingredient_id" required :disabled="!!editId">
              <option value="">选择食材</option>
              <option v-if="stockForm.ingredient_id && !ingredients.some(i => i.id === stockForm.ingredient_id)" :value="stockForm.ingredient_id">保留当前食材</option>
              <option v-for="i in ingredients" :key="i.id" :value="i.id">{{ i.name }}</option>
            </select>
          </label>
          <div class="form-grid">
            <label class="form-field"><span>{{ kind === 'stock' ? '实际余额' : '数量' }}</span>
              <input v-if="kind === 'shopping'" v-model="shopForm.quantity" required inputmode="decimal" />
              <input v-else v-model="stockForm.quantity" required inputmode="decimal" />
            </label>
            <label class="form-field"><span>单位</span>
              <select v-if="kind === 'shopping'" v-model="shopForm.unit"><option v-for="(name, key) in unitNames" :key="key" :value="key">{{ name }}</option></select>
              <select v-else v-model="stockForm.unit" :disabled="!!editId"><option v-for="key in (['g', 'ml', 'piece', 'portion'] as const)" :key="key" :value="key">{{ unitNames[key] }}</option></select>
            </label>
          </div>
          <template v-if="kind === 'shopping'">
            <label class="form-field"><span>备注</span><textarea v-model="shopForm.note" maxlength="1000" rows="2" /></label>
            <label class="checkbox-field"><input v-model="shopForm.purchased" type="checkbox" /> 已买（不自动入库）</label>
          </template>
        </template>

        <div class="filter-row">
          <input v-model="ingredientQuery" placeholder="查找食材" maxlength="80" @keydown.enter.prevent="loadIngredients(true)" />
          <HubButton variant="quiet" size="sm" @click="loadIngredients(true)">查找</HubButton>
          <span class="muted fine-print">{{ ingredientTotal }} 种</span>
        </div>
      </form>
      <template #actions>
        <HubButton variant="quiet" :disabled="writes.busy.value" @click="closeEditor">取消</HubButton>
        <HubButton variant="primary" :loading="writes.busy.value" @click="save">保存</HubButton>
      </template>
    </HubDialog>
  </section>
</template>
