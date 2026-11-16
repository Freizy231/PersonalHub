const { assert, test, mountComponent, flatten, flush, ApiError, setHandler, toastText, resetToasts } = require('./harness.cjs')

const event = {
  id: '22222222-2222-4222-8222-222222222222', title: '已有日程', description: '说明', location: '本机',
  is_all_day: true, status: 'scheduled', timezone: 'Asia/Shanghai', task_id: null, linked_task: null,
  start_date: '2026-11-13', end_date: '2026-11-14', start_at: null, end_at: null, version: 1, deleted_at: null,
}

function mount() { resetToasts(); return mountComponent('modules/life/events/EventPanel.vue', { enabled: true }) }

async function typeInto(node, value) {
  node.value = value
  node.events.input?.({ target: node })
  await flush()
}

test('creating an event converts the inclusive end date and reuses the idempotency key', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (!options) return { items: [], total: 0, offset: 0, limit: 30 }
    writes.push({ path, options })
    if (writes.length === 1) throw new Error('network down')
    return { ...event, title: JSON.parse(options.body).title }
  })
  const view = mount(); await flush()

  await view.button('＋ 新建日程').props.onClick(); await flush()
  const title = view.input(n => n.props.maxlength === '500')
  await typeInto(title, '新日程')

  await view.button('保存').props.onClick(); await flush()
  assert.match(view.text(), /network down/)

  await view.button('保存').props.onClick(); await flush()
  const creates = writes.filter(w => w.path === '/events')
  assert.equal(creates.length, 2)
  assert.equal(creates[0].options.headers['Idempotency-Key'], creates[1].options.headers['Idempotency-Key'])
  const body = JSON.parse(creates[1].options.body)
  // The editor defaults both ends to today, so assert the invariant, not a fixed date.
  const day = new Date(body.start_date + 'T12:00:00Z')
  day.setUTCDate(day.getUTCDate() + 1)
  assert.equal(body.end_date, day.toISOString().slice(0, 10), 'exclusive storage end = inclusive UI end + 1 day')
  view.app.unmount()
})

test('an event conflict keeps the draft and reports it without overwriting', async () => {
  setHandler(async (path, options) => {
    if (!options) return { items: [event], total: 1, offset: 0, limit: 30 }
    throw new ApiError('VERSION_CONFLICT', 409)
  })
  const view = mount(); await flush()

  await view.button('已有日程').props.onClick(); await flush()
  const title = view.input(n => n.props.maxlength === '500')
  await typeInto(title, '冲突草稿')
  await view.button('保存').props.onClick(); await flush()

  assert.equal(view.input(n => n.props.maxlength === '500').value, '冲突草稿')
  assert.match(view.text(), /版本|已变化|VERSION_CONFLICT/)
  view.app.unmount()
})

test('a reminder acknowledgement that fails stays unread and can be retried', async () => {
  let fail = true
  setHandler(async (path, options) => {
    if (!options) {
      if (path.startsWith('/reminders')) return { items: [{ id: 'r1', resource_id: 'x', title: '提醒一', remind_at: '2026-11-13T09:00:00Z', status: 'delivered', version: 1, acknowledged_at: null }], total: 1, offset: 0, limit: 20 }
      return { items: [], total: 0 }
    }
    if (fail) { fail = false; throw new Error('ack failed') }
    return { status: 'ok' }
  })
  const view = mountComponent('modules/life/reminders/ReminderPanel.vue', { enabled: true })
  await flush()
  resetToasts()

  await view.button('标为已读').props.onClick(); await flush()
  assert.ok(toastText().some(m => m.includes('ack failed')))
  assert.ok(view.anyButton('标为已读'), 'the action is still offered after a failure')

  await view.button('标为已读').props.onClick(); await flush()
  view.app.unmount()
})

test('switching date keeps a failed load visible instead of showing an empty success', async () => {
  let fail = false
  setHandler(async () => {
    if (fail) throw new Error('read failed')
    return { items: [event], total: 1, offset: 0, limit: 30 }
  })
  const view = mount(); await flush()
  assert.match(view.text(), /已有日程/)

  fail = true
  const nextMonth = flatten(view.root).find(n => n.props['aria-label'] === '下个月')
  await nextMonth.props.onClick(); await flush()
  assert.match(view.text(), /read failed/)
  view.app.unmount()
})
