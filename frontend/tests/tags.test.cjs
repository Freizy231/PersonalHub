const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts, toastText } = require('./harness.cjs')

const rid = '33333333-3333-4333-8333-333333333333'
const tid = '44444444-4444-4444-8444-444444444444'
const tag = { id: tid, name: 'AI', version: 1, usage_count: 2 }
const assignment = (tags = []) => ({ resource_id: rid, resource_version: 1, tags })

function mountPanel() { resetToasts(); return mountComponent('modules/tags/TagPanel.vue', { resourceId: rid }) }

async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }
async function submit(view) {
  const forms = view.nodes().filter(n => n.tag === 'form')
  assert.ok(forms.length)
  await forms[0].props.onSubmit({ preventDefault() {} }); await flush()
}

test('saving tags sends the resource version and every current tag version', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (!options) return assignment([tag])
    writes.push({ path, options })
    return assignment([tag, { id: 'new', name: '安全', version: 1, usage_count: 1 }])
  })
  const view = mountPanel(); await flush()

  await typeInto(view.textarea(), 'AI\n安全')
  await submit(view)

  assert.equal(writes[0].path, `/resources/${rid}/tags`)
  const body = JSON.parse(writes[0].options.body)
  assert.deepEqual(body.names, ['AI', '安全'])
  assert.equal(body.expected_version, 1)
  assert.deepEqual(body.expected_tag_versions, { [tid]: 1 })
  view.app.unmount()
})

test('a tag name collision is explained instead of silently merging', async () => {
  setHandler(async (path, options) => {
    if (!options) return assignment([tag])
    throw new ApiError('TAG_NAME_CONFLICT', 409)
  })
  const view = mountPanel(); await flush()
  await typeInto(view.textarea(), 'AI\n另一个')
  await submit(view)
  assert.match(view.text(), /TAG_NAME_CONFLICT|已被另一标签使用|未合并/)
  view.app.unmount()
})

test('renaming a shared tag warns about its impact and keeps the draft on failure', async () => {
  setHandler(async (path, options) => {
    if (!options) return assignment([tag])
    throw new Error('rename lost')
  })
  const view = mountPanel(); await flush()
  const rename = view.anyButton('改名')
  assert.ok(rename)
  await rename.props.onClick(); await flush()

  const input = view.input(n => n.props.maxlength === '256')
  await typeInto(input, '人工智能')
  assert.match(view.text(), /会影响所有引用/)
  await submit(view)

  assert.equal(view.input(n => n.props.maxlength === '256').value, '人工智能')
  view.app.unmount()
})

test('more than 20 tags is rejected before any request is sent', async () => {
  let writes = 0
  setHandler(async (path, options) => {
    if (options) writes++
    return assignment([tag])
  })
  const view = mountPanel(); await flush()
  await typeInto(view.textarea(), Array.from({ length: 21 }, (_, i) => `t${i}`).join('\n'))
  await submit(view)
  assert.equal(writes, 0)
  assert.match(view.text(), /最多 20 个标签/)
  view.app.unmount()
})

test('the tag filter only queries its catalogue when first focused', async () => {
  let reads = 0
  setHandler(async () => { reads++; return { items: [tag], total: 1 } })
  const view = mountComponent('modules/tags/TagFilter.vue', { modelValue: '' })
  await flush()
  assert.equal(reads, 0, 'no eager fetch')
  const select = flatten(view.root).find(n => n.tag === 'select')
  select.props.onFocus?.(); await flush()
  assert.equal(reads, 1)
  view.app.unmount()
})
