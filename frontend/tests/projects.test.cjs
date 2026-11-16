const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts, toastText } = require('./harness.cjs')

const id = '11111111-1111-4111-8111-111111111111'
const project = {
  id, kind: 'project', title: 'Malware Analysis Lab', version: 1, status: 'active', description: '实验项目',
  objective: '独立分析报告', local_directory: '/tmp/lab', repository_url: '', start_date: null, target_date: null,
  deleted_at: null, progress: { done: 0, total: 0, percent: null, denominator: '未取消的可见任务' },
}
const empty = { items: [], total: 0, limit: 20, offset: 0 }

function mount() { resetToasts(); return mountComponent('modules/projects/ProjectPanel.vue', { enabled: true }) }

function server(write, record = project) {
  setHandler(async (path, options) => {
    if (options) {
      if (path.endsWith('/opened')) return { status: 'recorded' }
      return write(path, options)
    }
    if (path.startsWith('/projects?')) return { ...empty, items: [record], total: 1 }
    if (path === `/projects/${id}`) return record
    if (path.startsWith(`/projects/${id}/milestones`)) return { items: [] }
    if (path.startsWith('/tags')) return { items: [], total: 0 }
    return empty
  })
}

async function open() {
  const view = mount(); await flush()
  await view.button(project.title).props.onClick(); await flush()
  return view
}

async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }

test('opening a project shows its facts and performs no write', async () => {
  const writes = []
  server(async (path, options) => { writes.push({ path, options }); return {} })
  const view = await open()
  assert.match(view.text(), /Malware Analysis Lab/)
  assert.match(view.text(), /独立分析报告/)
  assert.equal(writes.length, 0, 'reading a project never triggers a write except explicit visit tracking')
  view.app.unmount()
})

test('creating a project task sends the project version and the task fields', async () => {
  const writes = []
  server(async (path, options) => { writes.push({ path, options }); return {} })
  const view = await open()

  const summary = view.nodes().find(n => n.tag === 'summary' && text(n).includes('添加任务'))
  assert.ok(summary)
  await summary.props.onClick?.(); await flush()

  await typeInto(view.input(n => n.props.maxlength === '500'), '分析 PE')
  const forms = view.nodes().filter(n => n.tag === 'form')
  const taskForm = forms.find(f => text(f).includes('任务名称'))
  assert.ok(taskForm)
  await taskForm.props.onSubmit({ preventDefault() {} }); await flush()

  const create = writes.find(w => w.path === `/projects/${id}/tasks`)
  assert.ok(create)
  const body = JSON.parse(create.options.body)
  assert.equal(body.title, '分析 PE')
  assert.equal(body.expected_version, 1)
  view.app.unmount()
})

test('a lost project-task response keeps the draft and only retries the original key', async () => {
  const writes = []
  server(async (path, options) => { writes.push({ path, options }); if (path.endsWith('/tasks')) throw new Error('lost response'); return {} })
  const view = await open()
  const summary = view.nodes().find(n => n.tag === 'summary' && text(n).includes('添加任务'))
  await summary.props.onClick?.(); await flush()

  const title = view.input(n => n.props.maxlength === '500')
  await typeInto(title, '保留我')
  const taskForm = view.nodes().filter(n => n.tag === 'form').find(f => text(f).includes('任务名称'))
  await taskForm.props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(view.input(n => n.props.maxlength === '500').value, '保留我')
  assert.ok(toastText().some(m => m.includes('lost response')), 'the failure is reported')
  view.app.unmount()
})

test('a project conflict is reported without discarding the draft', async () => {
  server(async (path, options) => { if (path.endsWith('/tasks')) throw new ApiError('VERSION_CONFLICT', 409); return {} })
  const view = await open()
  const summary = view.nodes().find(n => n.tag === 'summary' && text(n).includes('添加任务'))
  await summary.props.onClick?.(); await flush()

  await typeInto(view.input(n => n.props.maxlength === '500'), '我的任务草稿')
  const taskForm = view.nodes().filter(n => n.tag === 'form').find(f => text(f).includes('任务名称'))
  await taskForm.props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(view.input(n => n.props.maxlength === '500').value, '我的任务草稿')
  view.app.unmount()
})

test('an archived project explains itself and hides creation forms', async () => {
  server(async () => ({}), { ...project, status: 'archived' })
  const view = await open()
  assert.match(view.text(), /项目已归档/)
  assert.equal(view.nodes().find(n => n.tag === 'summary' && text(n).includes('添加任务')), undefined)
  view.app.unmount()
})

test('the directory check is an explicit action and reports only its result', async () => {
  const writes = []
  server(async (path, options) => { writes.push(path); return { status: 'missing' } })
  const view = await open()
  assert.equal(writes.length, 0)
  await view.button('检查').props.onClick(); await flush()
  assert.deepEqual(writes, [`/projects/${id}/directory-check`])
  assert.match(view.text(), /目录不存在/)
  view.app.unmount()
})
