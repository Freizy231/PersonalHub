const { assert, test, mountComponent, flatten, text, flush, setHandler, resetToasts, toastText } = require('./harness.cjs')

const project = { id: '11111111-1111-4111-8111-111111111111', kind: 'project', title: '实验项目', status: 'active', local_directory: '/tmp/lab', version: 1 }

function mountLauncher() { resetToasts(); return mountComponent('modules/projects/ToolLauncher.vue', { project }) }
function mountSettings() { resetToasts(); return mountComponent('modules/projects/ToolSettings.vue', {}) }

test('a tool launch always requires an explicit preview before confirmation', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) {
      writes.push({ path, options })
      if (path === '/project-tools/preview') return { id: 'pv1', title: project.title, profile_name: 'VS Code', arguments: ['/usr/bin/code', '/tmp/lab'], directory: '/tmp/lab', expires_at: '2026-11-13T12:00:00Z', warning: '将启动本机程序' }
      return { id: 'h1', profile_name: 'VS Code', status: 'started', error_code: null, created_at: '2026-11-13' }
    }
    if (path === '/project-tools') return { items: [{ id: 't1', name: 'VS Code', enabled: 1, available: true }] }
    if (path.startsWith('/project-tools/history')) return { items: [], total: 0, limit: 10, offset: 0 }
    return { items: [] }
  })
  const view = mountLauncher(); await flush()
  assert.equal(writes.length, 0, 'opening the panel launches nothing')

  const select = view.nodes().find(n => n.tag === 'select')
  select.props['onUpdate:modelValue']('t1'); await flush()

  await view.button('预览启动').props.onClick(); await flush()
  assert.equal(writes.length, 1)
  assert.equal(writes[0].path, '/project-tools/preview')
  assert.equal(writes.length, 1, 'preview never launches by itself')
  assert.match(view.text(), /将启动本机程序/)

  await view.button('确认打开').props.onClick(); await flush()
  assert.equal(writes[1].path, '/project-tools/confirm/pv1')
  view.app.unmount()
})

test('a project without a directory explains why tools are unavailable', async () => {
  setHandler(async () => ({ items: [] }))
  const view = mountComponent('modules/projects/ToolLauncher.vue', { project: { ...project, local_directory: '' } })
  await flush()
  assert.match(view.text(), /先在项目基本信息中登记本地目录/)
  view.app.unmount()
})

test('tool settings binds an installation and never offers a free-form command', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, options }); return {} }
    return {
      items: [],
      installations: [{ installation_id: 'i1', adapter: 'vscode', label: 'VS Code', executables: ['/usr/bin/code'] }],
      adapters: [{ id: 'vscode', label: 'VS Code', available: true }],
    }
  })
  const view = mountSettings(); await flush()

  const fields = view.nodes().filter(n => n.tag === 'input').map(n => n.props.placeholder || '')
  assert.equal(fields.includes('命令'), false, 'there is no free-form command field')

  const select = view.nodes().find(n => n.tag === 'select')
  select.props['onUpdate:modelValue']('i1'); await flush()
  const form = view.nodes().find(n => n.tag === 'form')
  assert.ok(form)
  await form.props.onSubmit({ preventDefault() {} }); await flush()

  const body = JSON.parse(writes[0].options.body)
  assert.equal(body.adapter, 'vscode')
  assert.equal(body.installation_id, 'i1')
  view.app.unmount()
})

test('a tool name survives a failed save so the user can retry', async () => {
  let attempt = 0
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push(options); attempt++; if (attempt === 1) throw new Error('save failed'); return {} }
    return {
      items: [],
      installations: [{ installation_id: 'i1', adapter: 'vscode', label: 'VS Code', executables: ['/usr/bin/code'] }],
      adapters: [],
    }
  })
  const view = mountSettings(); await flush()
  const nameInput = view.nodes().find(n => n.tag === 'input' && n.props.maxlength === '100')
  nameInput.value = '我的编辑器'; nameInput.events.input?.({ target: nameInput }); await flush()

  const select = view.nodes().find(n => n.tag === 'select')
  select.props['onUpdate:modelValue']('i1'); await flush()
  const form = view.nodes().find(n => n.tag === 'form')
  assert.ok(form)
  await form.props.onSubmit({ preventDefault() {} }); await flush()

  assert.ok(toastText().some(m => m.includes('save failed')))
  assert.equal(view.nodes().find(n => n.tag === 'input' && n.props.maxlength === '100').value, '我的编辑器')
  view.app.unmount()
})
