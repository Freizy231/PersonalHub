const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts, toastText } = require('./harness.cjs')

const iid = '11111111-1111-4111-8111-111111111111'
const rid = '22222222-2222-4222-8222-222222222222'
const recipe = {
  id: rid, title: '米饭', instructions: '蒸熟', version: 1, base_servings: 2, favorite: 1,
  ingredients: [{ ingredient_id: iid, name: '大米', quantity: '0.2', unit: 'kg' }], deleted_at: null,
}
const ingredient = { id: iid, name: '大米', version: 1 }
const preview = {
  version: 0, digest: 'a'.repeat(64),
  items: [{ ingredient_id: iid, name: '大米', unit: 'g', required: '200', stock: '25', confirmed: '0', quantity: '175' }],
  policy: '库存不跨周预留，只对照不预留',
}
const empty = { items: [], total: 0, offset: 0, limit: 20 }
const shopping = { version: 0, items: [] }

function mount() { resetToasts(); return mountComponent('modules/life/diet/DietPanel.vue', { enabled: true }) }

function server(write, reads) {
  setHandler(async (path, options) => {
    if (options) return write(path, options)
    if (reads) { const value = await reads(path); if (value !== undefined) return value }
    if (path.startsWith('/diet/ingredients')) return { ...empty, items: [ingredient], total: 1 }
    if (path.startsWith('/diet/recipes')) return { ...empty, items: [recipe], total: 1 }
    if (path.startsWith('/diet/suggestions')) return preview
    if (path.startsWith('/diet/shopping')) return shopping
    if (path.startsWith('/diet/meals')) return { items: [], week_start: '2026-11-09' }
    if (path.startsWith('/diet/stock')) return empty
    return empty
  })
}

async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }
const dialogForm = view => view.nodes().filter(n => n.tag === 'form').pop()

test('weekly meals load with zero writes and the tab buttons change view', async () => {
  const writes = []
  server(async (path, options) => { writes.push(path); return {} })
  const view = mount(); await flush()
  assert.equal(writes.length, 0, 'reading the week performs no writes')

  await view.button('食谱').props.onClick(); await flush()
  assert.ok(view.anyButton('＋ 新建食谱'))
  view.app.unmount()
})

test('a recipe save retries the same idempotency key after a transport failure', async () => {
  const writes = []
  let attempt = 0
  server(async (path, options) => {
    writes.push({ path, options }); attempt++
    if (attempt === 1) throw new Error('connection lost')
    return recipe
  })
  const view = mount(); await flush()
  await view.button('食谱').props.onClick(); await flush()
  await view.button('＋ 新建食谱').props.onClick(); await flush()

  await typeInto(view.input(n => n.props.maxlength === '200'), '新菜')
  const form = dialogForm(view)
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  assert.ok(toastText().some(m => m.includes('connection lost')))

  await dialogForm(view).props.onSubmit({ preventDefault() {} }); await flush()
  const posts = writes.filter(w => w.path === '/diet/recipes')
  assert.equal(posts.length, 2)
  assert.equal(posts[0].options.headers['Idempotency-Key'], posts[1].options.headers['Idempotency-Key'])
  view.app.unmount()
})

test('a recipe conflict keeps the draft and reports it', async () => {
  server(async (path, options) => { throw new ApiError('VERSION_CONFLICT', 409) })
  const view = mount(); await flush()
  await view.button('食谱').props.onClick(); await flush()
  const row = view.nodes().find(n => n.tag === 'li' && text(n).includes('米饭'))
  const edit = flatten(row).find(n => n.tag === 'button' && n.text === '编辑')
  await edit.props.onClick(); await flush()

  await typeInto(view.input(n => n.props.maxlength === '200'), '并发草稿')
  await dialogForm(view).props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(view.input(n => n.props.maxlength === '200').value, '并发草稿')
  assert.ok(toastText().some(m => /VERSION_CONFLICT|已变化/.test(m)))
  view.app.unmount()
})

test('the purchase preview is read-only and confirmation appends only the selected keys', async () => {
  const writes = []
  server(async (path, options) => { writes.push({ path, body: options.body }); return shopping })
  const view = mount(); await flush()
  await view.button('购物清单').props.onClick(); await flush()
  await view.button('从菜单生成采购').props.onClick(); await flush()

  assert.equal(writes.length, 0, 'previewing performs no write')
  assert.match(view.text(), /175/)

  await view.button('加入购物清单').props.onClick(); await flush()
  const confirm = writes.find(w => w.path.includes('/confirm'))
  assert.ok(confirm)
  const body = JSON.parse(confirm.body)
  assert.equal(body.digest, preview.digest)
  assert.deepEqual(body.selected, [{ ingredient_id: iid, unit: 'g' }])
  view.app.unmount()
})

test('a stale preview is surfaced and never silently regenerated', async () => {
  let previews = 0
  server(async (path, options) => {
    if (path.includes('/suggestions') && options) throw new ApiError('DIET_PREVIEW_STALE', 409)
    return shopping
  }, path => { if (path.includes('/suggestions')) { previews++; return preview } })
  const view = mount(); await flush()
  await view.button('购物清单').props.onClick(); await flush()
  await view.button('从菜单生成采购').props.onClick(); await flush()
  await view.button('加入购物清单').props.onClick(); await flush()

  assert.ok(toastText().some(m => /DIET_PREVIEW_STALE|已变化/.test(m)))
  assert.equal(previews, 1, 'the preview is not re-fetched behind the user')
  view.app.unmount()
})

test('today meals are a date-scoped read-only projection', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) writes.push(path)
    if (path.startsWith('/diet/today')) return { items: [{ id: 'm1', recipe_id: null, title: '米饭', local_date: '2026-11-13', slot: 'lunch', servings: 1, note: '',
      dishes: [{ id: 'd1', recipe_id: null, label: '米饭', name: '米饭', recipe_version: null, position: 0 }] }] }
    return empty
  })
  const view = mountComponent('modules/life/diet/TodayMeals.vue', { enabled: true })
  await flush()
  assert.match(view.text(), /米饭/)
  assert.equal(writes.length, 0)
  view.app.unmount()
})

test('a meal shows the actual dishes, not just a recipe title', async () => {
  server(async () => ({}), path => {
    if (path.startsWith('/diet/meals')) return {
      // The panel asks for the current week; a fixed date would stop matching next Monday.
      week_start: '2026-11-09',
      items: [
        { id: 'm1', local_date: new Date().toLocaleDateString('sv-SE'), slot: 'lunch', recipe_id: null, title: '番茄炒蛋', servings: 1, note: '', version: 1,
          dishes: [{ id: 'd1', recipe_id: null, label: '番茄炒蛋', name: '番茄炒蛋', recipe_version: null, position: 0 },
                   { id: 'd2', recipe_id: rid, label: '', name: '米饭', recipe_version: 1, position: 1 }] },
      ],
    }
    return undefined
  })
  const view = mount(); await flush()
  assert.match(view.text(), /番茄炒蛋/)
  assert.match(view.text(), /米饭/)
  assert.match(view.text(), /、/, 'dishes are joined visibly')
  view.app.unmount()
})

test('a meal can be recorded with a hand-written dish and no recipe', async () => {
  const writes = []
  server(async (path, options) => { writes.push({ path, body: options.body }); return {} })
  const view = mount(); await flush()
  await view.button('＋ 安排一餐').props.onClick(); await flush()

  const name = view.input(n => n.props.maxlength === '200' && n.props.placeholder)
  assert.ok(name, 'free-text dish field is available by default')
  name.value = '番茄炒蛋'; name.events.input?.({ target: name }); await flush()

  await dialogForm(view).props.onSubmit({ preventDefault() {} }); await flush()
  const save = writes.find(w => w.path === '/diet/meals')
  assert.ok(save, 'meal is saved')
  const body = JSON.parse(save.body)
  assert.deepEqual(body.dishes, [{ label: '番茄炒蛋' }])
  assert.equal(body.recipe_id, undefined, 'no recipe is required')
  view.app.unmount()
})

test('an empty meal is refused before any request', async () => {
  const writes = []
  server(async (path, options) => { writes.push(path); return {} })
  const view = mount(); await flush()
  await view.button('＋ 安排一餐').props.onClick(); await flush()
  await dialogForm(view).props.onSubmit({ preventDefault() {} }); await flush()
  assert.equal(writes.filter(p => p === '/diet/meals').length, 0)
  assert.ok(toastText().some(m => m.includes('至少填写一道菜')))
  view.app.unmount()
})

test('stock rows can be deleted with an explicit confirmation', async () => {
  const writes = []
  server(async (path, options) => { writes.push(path); return path.includes('/remove') ? { status: 'removed' } : {} },
    path => path.startsWith('/diet/stock') ? { items: [{ id: 'st1', ingredient_id: iid, name: '大米', quantity: '2', unit: 'piece', version: 3 }], total: 1, offset: 0, limit: 20 } : undefined)
  const view = mount(); await flush()
  await view.button('库存').props.onClick(); await flush()

  const original = window.confirm
  window.confirm = () => true
  await view.button('删除').props.onClick(); await flush()

  assert.ok(writes.includes('/diet/stock/st1/remove'))
  window.confirm = original
  view.app.unmount()
})
