const { assert, test, mountComponent, flatten, flush, setHandler, resetToasts, toastText } = require('./harness.cjs')
const { defaults } = require('./loader.cjs').load('modules/dashboard/types.ts', {})

function mount(mode) { resetToasts(); return mountComponent('modules/dashboard/DashboardPanel.vue', { enabled: true, mode }) }

const usable = { ...defaults(), widgets: defaults().widgets.map(w => ({ ...w })), version: 3, state: 'saved', updated_at: '2026-11-12' }

test('the home view renders only enabled widgets and never loads preferences it does not need', async () => {
  let reads = 0
  setHandler(async (path) => {
    if (path === '/dashboard/preferences') { reads++; return { ...usable, widgets: usable.widgets.map(w => w.id === 'intelligence' ? { ...w, enabled: false } : w) } }
    if (path.startsWith('/diet/today')) return { items: [] }
    if (path.startsWith('/events')) return { items: [], total: 0 }
    if (path.startsWith('/tasks')) return { items: [], total: 0, limit: 30, offset: 0 }
    return { items: [], total: 0 }
  })
  const view = mount('home'); await flush()
  assert.equal(reads, 1)
  const widgets = flatten(view.root).filter(n => n.props && n.props['data-widget'])
  assert.equal(widgets.some(n => n.props['data-widget'] === 'intelligence'), false, 'a disabled widget is not mounted')
  view.app.unmount()
})

test('the settings view previews layout changes and only persists on save', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, options }); return { ...usable, version: 4 } }
    return usable
  })
  const view = mount('settings'); await flush()

  await view.button('调整布局').props.onClick(); await flush()
  const checks = flatten(view.root).filter(n => n.tag === 'input' && n.type === 'checkbox')
  assert.ok(checks.length, 'widget toggles are present')

  await checks[0].props.onChange({ target: { checked: false } }); await flush()
  assert.equal(writes.length, 0, 'toggling is a local draft')

  await view.button('保存布局').props.onClick(); await flush()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].path, '/dashboard/preferences')
  assert.equal(JSON.parse(writes[0].options.body).expected_version, 3)
  view.app.unmount()
})

test('a version conflict reports and reloads without silently overwriting', async () => {
  const { ApiError } = require('./harness.cjs')
  setHandler(async (path, options) => {
    if (options) throw new ApiError('VERSION_CONFLICT', 409)
    return usable
  })
  const view = mount('settings'); await flush()
  await view.button('调整布局').props.onClick(); await flush()
  const checks = flatten(view.root).filter(n => n.tag === 'input' && n.type === 'checkbox')
  await checks[0].props.onChange({ target: { checked: false } }); await flush()

  await view.button('保存布局').props.onClick(); await flush()
  assert.ok(toastText().some(m => /VERSION_CONFLICT|已在别处修改/.test(m)), 'conflict is surfaced to the user')
  view.app.unmount()
})

test('reset only changes the draft and cancel leaves committed settings untouched', async () => {
  setHandler(async () => usable)
  const view = mount('settings'); await flush()
  await view.button('调整布局').props.onClick(); await flush()
  await view.button('恢复默认').props.onClick(); await flush()
  assert.ok(view.anyButton('保存布局'))
  await view.button('取消').props.onClick(); await flush()
  assert.equal(view.anyButton('保存布局'), undefined, 'leaving edit mode discards the draft')
  view.app.unmount()
})

test('the stalled view lists what stopped moving, with its age and destination', async () => {
  setHandler(async path => {
    if (path.startsWith('/stalled')) return {
      items: [
        { id: 't1', kind: 'task', kind_label: '任务', title: '一直没动的任务', days: 46,
          version: 3, since: '2026-10-01T00:00:00Z', basis: '创建后未修改', detail: '高优先级 · 没有计划日', destination: '/tasks' },
        { id: 'n1', kind: 'knowledge_node', kind_label: '知识点', title: '卡住的知识点', days: 23,
          version: 2, since: '2026-10-24T00:00:00Z', basis: '最后练习或验收', detail: '未验收', destination: '/learning' },
      ],
      total: 2, as_of: '2026-11-16T00:00:00Z', thresholds: {}, notice: '按最后变动时间计算，不是截止日期。',
    }
    if (path.startsWith('/tasks')) return { items: [], total: 0, limit: 8, offset: 0 }
    if (path.startsWith('/events')) return { items: [], total: 0 }
    if (path === '/dashboard/preferences') return { schema_version: 1, widgets: [], shortcuts: [], version: 0, state: 'default', updated_at: null }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/dashboard/StalledWidget.vue', { enabled: true })
  await flush(); await flush()

  const text = view.text()
  assert.match(text, /搁置的事/)
  assert.match(text, /一直没动的任务/)
  assert.match(text, /1 个月/, 'age is shown in a form you can judge at a glance')
  assert.match(text, /23 天/)
  assert.match(text, /按最后变动时间计算/, 'the basis for the number is stated')
  const link = view.nodes().find(n => n.tag === 'a' && n.props.href === '/tasks')
  assert.ok(link, 'each row leads to where the work can be resumed')
  view.app.unmount()
})

test('the stalled view says so plainly when nothing is stuck', async () => {
  setHandler(async () => ({ items: [], total: 0, notice: '按最后变动时间计算，不是截止日期。' }))
  resetToasts()
  const view = mountComponent('modules/dashboard/StalledWidget.vue', { enabled: true })
  await flush(); await flush()
  assert.match(view.text(), /没有长期没动的事/)
  view.app.unmount()
})

test('each stuck item offers the decisions that kind can honestly record', async () => {
  setHandler(async path => {
    if (path.startsWith('/stalled')) return {
      items: [
        { id: 't1', kind: 'task', kind_label: '任务', title: '停滞任务', days: 30, version: 4,
          since: '2026-10-01T00:00:00Z', basis: '创建后未修改', detail: '高优先级', destination: '/tasks' },
        { id: 'n1', kind: 'knowledge_node', kind_label: '知识点', title: '停滞知识点', days: 30, version: 2,
          since: '2026-10-01T00:00:00Z', basis: '创建后未练习', detail: '未验收', destination: '/learning' },
      ],
      total: 2, as_of: '2026-11-16T00:00:00Z', thresholds: {}, notice: '按最后变动时间计算。',
    }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/dashboard/StalledWidget.vue', { enabled: true })
  await flush(); await flush()

  const labels = view.nodes().filter(n => n.tag === 'button').map(n => n.text.trim())
  assert.ok(labels.includes('完成'), 'a task can be finished from here')
  assert.ok(labels.includes('放弃'), 'a task can be set aside')
  assert.equal(labels.includes('已完成'), false, 'a task is not called a project')
  assert.equal(labels.filter(l => l === '完成').length, 1, 'only the task row offers "完成"')
  // A node is verified through an assessment that keeps its evidence; a button must not fake it.
  assert.equal(labels.includes('不学了'), true, 'a node can be set aside')
  const nodeRow = view.nodes().find(n => n.tag === 'li' && require('./harness.cjs').text(n).includes('停滞知识点'))
  const nodeLabels = flatten(nodeRow).filter(n => n.tag === 'button').map(n => n.text.trim())
  assert.equal(nodeLabels.includes('完成'), false, 'a node cannot be ticked done without an assessment')
  view.app.unmount()
})

test('saying "later" records a decision and takes the row off the list', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (path.startsWith('/stalled')) return {
      items: [{ id: 't1', kind: 'task', kind_label: '任务', title: '停滞任务', days: 30, version: 4,
        since: '2026-10-01T00:00:00Z', basis: '创建后未修改', detail: '高优先级', destination: '/tasks' }],
      total: 1, as_of: '2026-11-16T00:00:00Z', thresholds: {}, notice: '按最后变动时间计算。',
    }
    if (path.startsWith('/reviews/')) { writes.push({ path, body: JSON.parse(options.body), options }); return { review: {}, resource: {} } }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/dashboard/StalledWidget.vue', { enabled: true })
  await flush(); await flush()

  const later = view.nodes().find(n => n.tag === 'button' && n.text.trim() === '以后再说')
  assert.ok(later, 'a deferral is offered')
  await later.props.onClick()
  await flush(); await flush()

  assert.equal(writes.length, 1)
  assert.equal(writes[0].path, '/reviews/t1')
  assert.equal(writes[0].body.decision, 'snooze')
  assert.equal(writes[0].body.expected_version, 4, 'the decision carries the version it was made against')
  assert.ok(writes[0].options.headers['Idempotency-Key'], 'the write is replay-safe')
  assert.equal(view.text().includes('停滞任务'), false, 'a deferred row leaves the list right away')
  view.app.unmount()
})

test('dropping something asks once before taking it off the list', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (path.startsWith('/stalled')) return {
      items: [{ id: 'p1', kind: 'project', kind_label: '项目', title: '旧项目', days: 60, version: 2,
        since: '2026-09-01T00:00:00Z', basis: '最后修改', detail: '进行中', destination: '/projects' }],
      total: 1, as_of: '2026-11-16T00:00:00Z', thresholds: {}, notice: '按最后变动时间计算。',
    }
    if (path.startsWith('/reviews/')) { writes.push({ path, body: JSON.parse(options.body) }); return { review: {}, resource: {} } }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/dashboard/StalledWidget.vue', { enabled: true })
  await flush(); await flush()

  const archive = view.nodes().find(n => n.tag === 'button' && n.text.trim() === '归档')
  await archive.props.onClick()
  await flush()
  assert.equal(writes.length, 0, 'the first click only asks')
  assert.equal(view.text().includes('旧项目'), true)

  const yes = view.nodes().find(n => n.tag === 'button' && n.text.trim() === '确定')
  assert.ok(yes, 'the confirmation names its consequence')
  await yes.props.onClick()
  await flush(); await flush()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].body.decision, 'drop')
  view.app.unmount()
})
