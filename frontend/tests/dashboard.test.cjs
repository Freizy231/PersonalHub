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
