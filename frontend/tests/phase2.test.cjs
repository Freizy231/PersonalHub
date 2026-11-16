const { assert, test, load } = require('./harness.cjs')
const loader = require('./loader.cjs')

// Pure modules: no mounting, no DOM. These assert the contracts the UI relies on.
const markdown = loader.load('shared/markdown.ts', {})
const resource = loader.load('shared/resource.ts', {})
const dashboard = loader.load('modules/dashboard/types.ts', {})

test('markdown parsing keeps the presented block vocabulary small and predictable', () => {
  const blocks = markdown.blocks('# A\n\npara\n\n- x\n\n```\ncode\n```')
  assert.deepEqual(blocks.map(b => b.kind), ['heading', 'paragraph', 'ul', 'code'])
  const inline = markdown.inline('a **b** `c` *d*')
  assert.deepEqual(inline.map(p => p.kind), ['text', 'strong', 'text', 'code', 'text', 'em'])
})

test('resource kind labels cover every kind the pickers can request', () => {
  const kinds = ['note', 'document', 'book', 'bookmark', 'course', 'task', 'event', 'project', 'learning_goal', 'learning_path', 'knowledge_node', 'collection', 'recipe']
  for (const kind of kinds) assert.ok(resource.resourceKinds[kind], `missing label for ${kind}`)
})

test('dashboard targets only allow known internal routes and safe external URLs', () => {
  assert.ok(dashboard.safeTarget('internal', '/'))
  assert.equal(dashboard.safeTarget('internal', '/not-a-route'), null)
  assert.equal(dashboard.safeTarget('web', 'https://example.org/docs'), 'https://example.org/docs')
  assert.equal(dashboard.safeTarget('web', 'javascript:alert(1)'), null)
  assert.equal(dashboard.safeTarget('web', 'https://user:pass@example.org'), null)
})

test('default widget set is usable and keeps the required widgets', () => {
  const value = dashboard.defaults()
  assert.equal(dashboard.usable(value), true)
  for (const required of ['capture', 'tasks', 'events', 'reminders', 'shortcuts']) {
    assert.ok(value.widgets.some(w => w.id === required), `required widget ${required} is present`)
  }
})

test('a malformed dashboard payload is rejected instead of silently applied', () => {
  const value = { ...dashboard.defaults(), schema_version: 2 }
  assert.equal(dashboard.usable(value), false)
  const missing = { ...dashboard.defaults(), widgets: dashboard.defaults().widgets.filter(w => w.id !== 'tasks') }
  assert.equal(dashboard.usable(missing), false)
})
