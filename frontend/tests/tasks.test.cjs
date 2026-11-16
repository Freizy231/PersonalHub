const { assert, test, mountComponent, flatten, flush, sample, ApiError, setHandler, toastText, resetToasts } = require('./harness.cjs')

const panel = () => { resetToasts(); return mountComponent('modules/life/tasks/TaskPanel.vue', { enabled: true }) }

function titleField(view) {
  return view.input(n => n.props['aria-label'] === '任务名称')
}

async function typeInto(node, value) {
  node.value = value
  node.events.input?.({ target: node })
  await flush()
}

test('a failed create keeps the draft and retries the same idempotency key', async () => {
  const writes = []
  let attempts = 0
  setHandler(async (path, options) => {
    if (!options) return { items: [], total: 0, offset: 0, limit: 30 }
    writes.push({ path, options }); attempts++
    if (attempts === 1) throw new Error('connection lost')
    return { ...sample, title: JSON.parse(options.body).title }
  })
  const view = panel(); await flush()

  await view.button('详细新建').props.onClick(); await flush()
  const field = titleField(view)
  assert.ok(field, 'editor opens with a title field')
  await typeInto(field, '安全重试草稿')

  await view.button('保存').props.onClick(); await flush()
  assert.match(view.text(), /connection lost/)
  assert.equal(titleField(view).value, '安全重试草稿', 'draft survives a transport failure')

  await view.button('保存').props.onClick(); await flush()
  const creates = writes.filter(w => w.path === '/tasks')
  assert.equal(creates.length, 2)
  assert.equal(creates[0].options.headers['Idempotency-Key'], creates[1].options.headers['Idempotency-Key'])
  assert.equal(creates[0].options.body, creates[1].options.body)
  view.app.unmount()
})

test('a version conflict is surfaced and the draft is preserved', async () => {
  setHandler(async (path, options) => {
    if (!options) return { items: [sample], total: 1, offset: 0, limit: 30 }
    throw new ApiError('VERSION_CONFLICT', 409)
  })
  const view = panel(); await flush()

  await view.button('已有任务').props.onClick(); await flush()
  const field = titleField(view)
  await typeInto(field, '保留我的草稿')
  await view.button('保存').props.onClick(); await flush()

  assert.equal(titleField(view).value, '保留我的草稿')
  assert.match(view.text(), /已在别处修改|版本|VERSION_CONFLICT/)
  view.app.unmount()
})

test('a failed completion leaves the task unfinished and reports the error', async () => {
  setHandler(async (path, options) => {
    if (!options) return { items: [sample], total: 1, offset: 0, limit: 30 }
    throw new Error('save failed')
  })
  const view = panel(); await flush()
  const checkbox = flatten(view.root).find(n => n.props['aria-label'] === '完成任务：已有任务')
  assert.ok(checkbox)
  await checkbox.props.onClick(); await flush()
  assert.equal(checkbox.props['aria-pressed'], false)
  assert.match(toastText().join('|'), /save failed/)
  view.app.unmount()
})

test('deleting a task is immediate and offers undo instead of a blocking confirm', async () => {
  const calls = []
  let confirmed = false
  const originalConfirm = window.confirm
  window.confirm = () => { confirmed = true; return true }
  setHandler(async (path, options) => {
    if (!options) return { items: [{ ...sample, deleted_at: '2026-11-13' }], total: 1, offset: 0, limit: 30 }
    calls.push({ path, options })
    return { ...sample, version: 2, deleted_at: '2026-11-13' }
  })
  const view = panel(); await flush()
  const row = flatten(view.root).find(n => flatten(n).some(x => x.text === '已有任务'))
  const remove = flatten(row).find(n => n.tag === 'button' && n.text === '删除')
  assert.ok(remove, 'row exposes a delete action')
  await remove.props.onClick(); await flush()

  assert.equal(confirmed, false, 'reversible actions never block on confirm')
  assert.equal(calls[0].path, `/tasks/${sample.id}/trash`)
  assert.ok(toastText().some(m => m.includes('已移入回收站')), 'undo toast is shown')
  window.confirm = originalConfirm
  view.app.unmount()
})

test('an empty list explains the current filter instead of showing nothing', async () => {
  setHandler(async () => ({ items: [], total: 0, offset: 0, limit: 30 }))
  const view = panel(); await flush()
  assert.match(view.text(), /今天还没有安排/)
  view.app.unmount()
})

test('quick add creates a task from one line, with a date prefix and priority marker', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (!options) return { items: [], total: 0, offset: 0, limit: 30 }
    writes.push({ path, body: JSON.parse(options.body), key: options.headers['Idempotency-Key'] })
    return { ...sample, title: JSON.parse(options.body).title }
  })
  const view = panel(); await flush()
  const field = view.input(n => n.props['aria-label'] === '快速添加任务')
  assert.ok(field, 'quick add field is present without opening a dialog')
  await typeInto(field, '明天 ! 写周报')
  const form = view.nodes().find(n => n.tag === 'form' && n.props.class === 'quick-add')
  await form.props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(writes.length, 1)
  assert.equal(writes[0].path, '/tasks')
  assert.equal(writes[0].body.title, '写周报')
  assert.equal(writes[0].body.priority, 3)
  assert.match(writes[0].body.planned_date, /^\d{4}-\d{2}-\d{2}$/)
  const tomorrow = new Date(); tomorrow.setDate(tomorrow.getDate() + 1)
  const expected = `${tomorrow.getFullYear()}-${String(tomorrow.getMonth() + 1).padStart(2, '0')}-${String(tomorrow.getDate()).padStart(2, '0')}`
  assert.equal(writes[0].body.planned_date, expected)
  assert.equal(field.value, '', 'the field clears after saving')
  view.app.unmount()
})

test('quick add keeps the line and retries with the same key after a network failure', async () => {
  const keys = []
  let attempts = 0
  setHandler(async (path, options) => {
    if (!options) return { items: [], total: 0, offset: 0, limit: 30 }
    keys.push(options.headers['Idempotency-Key']); attempts++
    if (attempts === 1) throw new Error('offline')
    return { ...sample }
  })
  const view = panel(); await flush()
  const field = view.input(n => n.props['aria-label'] === '快速添加任务')
  await typeInto(field, '买牛奶')
  const form = view.nodes().find(n => n.tag === 'form' && n.props.class === 'quick-add')
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  assert.equal(field.value, '买牛奶', 'the typed line is not lost')
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  assert.equal(keys.length, 2)
  assert.equal(keys[0], keys[1])
  view.app.unmount()
})

test('the compact dashboard variant hides scope tabs, tag filter and pagination', async () => {
  setHandler(async () => ({ items: [sample], total: 40, offset: 0, limit: 8 }))
  resetToasts()
  const view = mountComponent('modules/life/tasks/TaskPanel.vue', { enabled: true, compact: true }); await flush()
  assert.equal(view.anyButton('回收站'), undefined, 'no trash tab on the dashboard')
  assert.equal(view.anyButton('上一页'), undefined, 'no pager on the dashboard')
  assert.equal(view.nodes().some(n => n.tag === 'select'), false, 'no tag filter on the dashboard')
  assert.match(view.text(), /今日任务/)
  view.app.unmount()
})
