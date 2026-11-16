const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts } = require('./harness.cjs')

const note = {
  id: '33333333-3333-4333-8333-333333333333', title: '原笔记', body: '原正文', note_kind: 'quick',
  local_date: null, body_path: 'notes/x/r.md', content_sha256: 'a'.repeat(64), version: 1,
  updated_at: '2026-11-12', deleted_at: null, storage_state: 'ready', tags: [],
}
const page = (items) => ({ items, total: items.length, limit: 25, offset: 0 })

function mount(props = {}) { resetToasts(); return mountComponent('modules/library/notes/NotePanel.vue', { enabled: true, ...props }) }
async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }
function editForm(view) {
  const forms = view.nodes().filter(n => n.tag === 'form')
  return forms[forms.length - 1]
}

test('quick capture keeps a failed draft and retries the same create request', async () => {
  const writes = []
  let attempts = 0
  setHandler(async (path, options) => {
    if (!options) return page([])
    writes.push({ path, options }); attempts++
    if (attempts === 1) throw new Error('connection lost')
    return { ...note, title: JSON.parse(options.body).title }
  })
  const view = mount(); await flush()

  await view.button('＋ 写笔记').props.onClick(); await flush()
  await typeInto(view.input(n => n.props.maxlength === '500'), '安全重试草稿')
  await typeInto(view.textarea(), '第一版正文')
  await editForm(view).props.onSubmit({ preventDefault() {} }); await flush()

  assert.match(view.text(), /connection lost/)
  assert.equal(view.input(n => n.props.maxlength === '500').value, '安全重试草稿')

  await editForm(view).props.onSubmit({ preventDefault() {} }); await flush()
  const creates = writes.filter(w => w.path === '/notes')
  assert.equal(creates.length, 2)
  assert.equal(creates[0].options.headers['Idempotency-Key'], creates[1].options.headers['Idempotency-Key'])
  assert.equal(creates[0].options.body, creates[1].options.body)
  view.app.unmount()
})

test('editing sends the version and content hash so a lost write cannot silently duplicate', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (!options) return path === `/notes/${note.id}` ? note : page([note])
    writes.push({ path, options })
    return { ...note, version: 2, body: JSON.parse(options.body).body }
  })
  const view = mount(); await flush()
  await view.button('原笔记').props.onClick(); await flush()
  await view.button('编辑').props.onClick(); await flush()

  await typeInto(view.textarea(), '新版草稿')
  await editForm(view).props.onSubmit({ preventDefault() {} }); await flush()

  const body = JSON.parse(writes[0].options.body)
  assert.equal(body.expected_version, 1)
  assert.equal(body.expected_hash, note.content_sha256)
  assert.equal(body.body, '新版草稿')
  view.app.unmount()
})

test('a hash conflict blocks overwrite and offers saving as a new note', async () => {
  setHandler(async (path, options) => {
    if (!options) return path === `/notes/${note.id}` ? note : page([note])
    throw new ApiError('NOTE_HASH_CONFLICT', 409)
  })
  const view = mount(); await flush()
  await view.button('原笔记').props.onClick(); await flush()
  await view.button('编辑').props.onClick(); await flush()

  await typeInto(view.textarea(), '冲突草稿')
  await editForm(view).props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(view.textarea().value, '冲突草稿')
  assert.ok(view.anyButton('另存为新笔记'), 'explicit copy path is offered')
  view.app.unmount()
})

test('a restored revision only fills the draft and never writes on its own', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (!options) {
      if (path === `/notes/${note.id}`) return note
      if (path.startsWith(`/notes/${note.id}/revisions?`)) return { items: [{ id: 'rev1', version: 1, title: '旧标题', note_kind: 'treehole', local_date: null, created_at: '2026-11-11' }], total: 1, limit: 20, offset: 0 }
      if (path === `/notes/${note.id}/revisions/rev1`) return { id: 'rev1', version: 1, title: '旧标题', body: '旧正文', note_kind: 'treehole', local_date: null, created_at: '2026-11-11' }
      return page([note])
    }
    writes.push(path)
    return note
  })
  const view = mount(); await flush()
  await view.button('原笔记').props.onClick(); await flush()
  await view.button('编辑').props.onClick(); await flush()

  await view.anyButton('历史').props.onClick(); await flush()
  const revision = view.anyButton('v1')
  assert.ok(revision, 'revision is listed')
  await revision.props.onClick(); await flush()
  await view.anyButton('放入草稿').props.onClick(); await flush()

  assert.equal(writes.length, 0, 'selecting a revision performs no write')
  assert.equal(view.input(n => n.props.maxlength === '500').value, '旧标题')
  view.app.unmount()
})

test('closing an unsaved editor asks first, and declining keeps the draft', async () => {
  setHandler(async () => page([]))
  const view = mount(); await flush()
  await view.button('＋ 写笔记').props.onClick(); await flush()
  await typeInto(view.textarea(), '未保存草稿')

  const original = window.confirm
  window.confirm = () => false
  await view.button('关闭').props.onClick(); await flush()
  assert.equal(view.textarea().value, '未保存草稿')

  window.confirm = () => true
  await view.button('关闭').props.onClick(); await flush()
  assert.equal(view.textarea(), undefined, 'editor closes after confirmation')
  window.confirm = original
  view.app.unmount()
})

test('deleting a note is immediate with an undo action and no blocking confirm', async () => {
  let confirmed = false
  const original = window.confirm
  window.confirm = () => { confirmed = true; return true }
  setHandler(async (path, options) => {
    if (!options) return page([note])
    return { ...note, version: 2, deleted_at: '2026-11-13' }
  })
  const view = mount(); await flush()
  const row = view.nodes().find(n => n.tag === 'li' && text(n).includes('原笔记'))
  const remove = flatten(row).find(n => n.tag === 'button' && n.text === '删除')
  assert.ok(remove)
  await remove.props.onClick(); await flush()

  assert.equal(confirmed, false)
  assert.doesNotMatch(view.text(), /原笔记/, 'the row disappears immediately')
  window.confirm = original
  view.app.unmount()
})

test('a missing backing file is reported instead of pretending the note loaded', async () => {
  setHandler(async () => page([{ ...note, storage_state: 'missing' }]))
  const view = mount(); await flush()
  assert.match(view.text(), /文件缺失/)
  view.app.unmount()
})
