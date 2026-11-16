const { assert, test, mountComponent, flatten, text, flush, setHandler, resetToasts } = require('./harness.cjs')
const Router = require('vue-router')

function makeRouter() {
  return Router.createRouter({
    history: Router.createMemoryHistory(),
    routes: [{ path: '/:pathMatch(.*)*', component: { template: '<span />' } }],
  })
}

function mountShell() {
  resetToasts()
  const router = makeRouter()
  setHandler(async (path) => {
    if (path === '/system') return { version: '0.14.0', phase: 4, database: 'ready', ai: 'optional_api', legacy_intelligence: 'preserved', private_vault: 'encrypted', backup_scope: 'local', reminders: 'running' }
    if (path === '/dashboard/preferences') return { schema_version: 1, widgets: [], shortcuts: [], version: 0, state: 'default', updated_at: null }
    return { items: [], total: 0 }
  })
  const view = mountComponent('App.vue', {}, {}, [router])
  return { view, router }
}

test('the sidebar exposes each life area directly instead of nesting tabs', async () => {
  const { view, router } = mountShell()
  await router.isReady(); await flush(); await flush()

  const navLinks = view.nodes().filter(n => n.tag === 'a' && n.props.href)
  const hrefs = navLinks.map(n => n.props.href)
  for (const path of ['/', '/tasks', '/events', '/notes', '/diet', '/finance', '/exercise', '/vault', '/learning', '/library', '/projects', '/intelligence', '/ai']) {
    assert.ok(hrefs.includes(path), `sidebar links to ${path}`)
  }
  assert.equal(hrefs.includes('/life'), false, 'the old nested "life" page is gone')
  view.app.unmount()
})

test('the active route is marked for assistive technology', async () => {
  const { view, router } = mountShell()
  await router.push('/tasks')
  await router.isReady(); await flush(); await flush()

  const active = view.nodes().filter(n => n.props && n.props['aria-current'] === 'page')
  assert.equal(active.length, 1)
  assert.match(text(active[0]), /任务/)
  view.app.unmount()
})

test('the global search submits to the unified search route', async () => {
  const { view, router } = mountShell()
  await router.isReady(); await flush(); await flush()

  const input = view.input(n => n.props.type === 'search')
  assert.ok(input, 'search field is present')
  input.value = '注入'
  input.events.input?.({ target: input })
  await flush()

  const form = view.nodes().find(n => n.tag === 'form')
  assert.ok(form)
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  view.app.unmount()
})

test('the shell never hardcodes a version and reads it from server state', async () => {
  const source = require('node:fs').readFileSync(require('node:path').join(__dirname, '../src/App.vue'), 'utf8')
  assert.match(source, /state\?\.version/, 'the footer binds the server-reported version')
  assert.doesNotMatch(source, /Personal Hub 0\.\d+\.\d+/, 'no literal version string in the shell')
})

test('an unconfirmed write to one row does not block the same action on another row', async () => {
  const { load } = require('./loader.cjs')
  const calls = []
  let failFirst = true
  const requests = load('shared/requests.ts', { './api': {
    ApiError: class ApiError extends Error { constructor(code, status) { super(code); this.code = code; this.status = status } },
    api: async (path, options) => { calls.push({ path, key: options.headers['Idempotency-Key'] }); if (failFirst) { failFirst = false; throw new Error('network down') } return {} },
  } })
  const writes = requests.useIdempotent()
  await assert.rejects(writes.send('state', '/diet/meals/a/remove', { expected_version: 1 }), /network down/)
  assert.equal(writes.hasPending(), true, 'row A stays unconfirmed')
  await writes.send('state', '/diet/meals/b/remove', { expected_version: 4 })
  assert.equal(calls.length, 2, 'row B went through')
  assert.notEqual(calls[0].key, calls[1].key, 'a different row gets its own key')
  await writes.send('state', '/diet/meals/a/remove', { expected_version: 1 })
  assert.equal(calls[0].key, calls[2].key, 'retrying row A reuses its original key')
})
