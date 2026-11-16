const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts } = require('./harness.cjs')

const locked = { initialized: true, unlocked: false, expires_in: 0 }
const unlocked = { initialized: true, unlocked: true, expires_in: 299 }
const sample = {
  id: '33333333-3333-4333-8333-333333333333', title: '虚构私密标题', body: '虚构 PRIVATE 正文',
  note_kind: 'private_journal', local_date: '2026-11-12', version: 1, updated_at: '2026-11-12', deleted_at: null,
  revisions: [{ title: '旧标题', body: '旧正文', note_kind: 'treehole', local_date: null, version: 1, updated_at: '2026-11-11' }],
}

function mount() { resetToasts(); return mountComponent('modules/library/notes/VaultPanel.vue', { enabled: true }) }
function passwordInput(view) { return view.input(n => n.type === 'password') }
async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }
async function submitForm(view, index = 0) {
  const forms = view.nodes().filter(n => n.tag === 'form')
  assert.ok(forms[index], 'form exists')
  await forms[index].props.onSubmit({ preventDefault() {} })
  await flush()
}

test('setup requires an acknowledgement and matching passwords before submit', async () => {
  setHandler(async path => {
    if (path === '/vault/status') return { ...locked, initialized: false }
    if (path === '/vault/lock') return {}
    return {}
  })
  const view = mount(); await flush()

  const submit = view.button('创建私密库')
  assert.equal(submit.props.disabled, true, 'disabled before acknowledgement')

  const passwords = flatten(view.root).filter(n => n.tag === 'input' && n.type === 'password')
  await typeInto(passwords[0], 'fictitious-password')
  await typeInto(passwords[1], 'different-password')
  assert.equal(view.button('创建私密库').props.disabled, true, 'disabled while passwords differ')

  await typeInto(passwords[1], 'fictitious-password')
  const acknowledge = flatten(view.root).find(n => n.tag === 'input' && n.type === 'checkbox')
  acknowledge.props.onChange?.({ target: { checked: true } })
  acknowledge.props['onUpdate:checked']?.(true)
  await flush()
  view.app.unmount()
})

test('unlock clears password fields even when the request fails', async () => {
  let submitted
  setHandler(async (path, options) => {
    if (path === '/vault/status') return locked
    if (path === '/vault/lock') return {}
    submitted = JSON.parse(options.body); throw new ApiError('VAULT_UNLOCK_FAILED', 403)
  })
  const view = mount(); await flush()
  const input = passwordInput(view)
  await typeInto(input, 'fictitious-only-password')
  await submitForm(view)

  assert.equal(submitted.password, 'fictitious-only-password')
  assert.equal(passwordInput(view).value, '', 'password is never retained in the field')
  assert.doesNotMatch(view.text(), /fictitious-only-password/)
  view.app.unmount()
})

test('locking clears every private value from the page', async () => {
  let active = false
  setHandler(async (path, options) => {
    if (path === '/vault/status') return active ? unlocked : locked
    if (path === '/vault/unlock') { active = true; return unlocked }
    if (path === '/vault/lock') { active = false; return { status: 'locked' } }
    if (path.startsWith('/vault/notes?')) return { items: [sample], total: 1 }
    if (path === '/vault/notes/' + sample.id && !options) return sample
    return sample
  })
  const view = mount(); await flush()
  await typeInto(passwordInput(view), 'fictitious-only-password')
  await submitForm(view)
  assert.match(view.text(), /虚构私密标题/)

  await view.button('立即锁定').props.onClick(); await flush()
  assert.doesNotMatch(view.text(), /虚构私密标题/)
  assert.match(view.text(), /设置独立解锁密码|解锁私密库/)
  view.app.unmount()
})

test('a conflict blocks overwrite and offers an explicit copy path', async () => {
  let active = false
  setHandler(async (path, options) => {
    if (path === '/vault/status') return active ? unlocked : locked
    if (path === '/vault/unlock') { active = true; return unlocked }
    if (path === '/vault/lock') return { status: 'locked' }
    if (path.startsWith('/vault/notes?') && !options) return { items: [sample], total: 1 }
    if (path === '/vault/notes/' + sample.id && !options) return sample
    if (options) throw new ApiError('VERSION_CONFLICT', 409)
    return {}
  })
  const view = mount(); await flush()
  await typeInto(passwordInput(view), 'fictitious-only-password')
  await submitForm(view)
  await view.button('虚构私密标题').props.onClick(); await flush()
  assert.match(view.text(), /冲突私密草稿|虚构私密标题/, 'opening shows the private note read-only')
  await view.button('编辑').props.onClick(); await flush()

  const body = view.textarea()
  await typeInto(body, '冲突私密草稿')
  const forms = view.nodes().filter(n => n.tag === 'form')
  const editForm = forms[forms.length - 1]
  assert.ok(editForm)
  await editForm.props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(view.textarea().value, '冲突私密草稿')
  assert.ok(view.anyButton('另存为新笔记'))
  view.app.unmount()
})

test('history selection only fills the draft and never writes on its own', async () => {
  let active = false
  const writes = []
  setHandler(async (path, options) => {
    if (path === '/vault/status') return active ? unlocked : locked
    if (path === '/vault/unlock') { active = true; return unlocked }
    if (path === '/vault/lock') return { status: 'locked' }
    if (path.startsWith('/vault/notes?') && !options) return { items: [sample], total: 1 }
    if (path === '/vault/notes/' + sample.id && !options) return sample
    if (options) writes.push(path)
    return sample
  })
  const view = mount(); await flush()
  await typeInto(passwordInput(view), 'fictitious-only-password')
  await submitForm(view)
  await view.button('虚构私密标题').props.onClick(); await flush()
  await view.button('编辑').props.onClick(); await flush()

  const historySummary = view.nodes().find(n => n.tag === 'summary' && text(n).includes('加密历史修订'))
  assert.ok(historySummary, 'history section is available')
  await historySummary.props.onClick?.(); await flush()
  const restore = view.anyButton('放入草稿')
  assert.ok(restore)
  await restore.props.onClick(); await flush()

  assert.equal(writes.length, 0, 'restoring a revision is a draft, not a write')
  assert.equal(view.input(n => n.props.maxlength === '500').value, '旧标题')
  view.app.unmount()
})

test('a private note opens as a read-only article before it becomes an editor', async () => {
  let active = false
  setHandler(async (path, options) => {
    if (path === '/vault/status') return active ? unlocked : locked
    if (path === '/vault/unlock') { active = true; return unlocked }
    if (path === '/vault/lock') return { status: 'locked' }
    if (path.startsWith('/vault/notes?') && !options) return { items: [sample], total: 1 }
    if (path === '/vault/notes/' + sample.id && !options) return { ...sample, body: '# 标题\n\n正文段落' }
    return {}
  })
  const view = mount(); await flush()
  await typeInto(passwordInput(view), 'fictitious-only-password')
  await submitForm(view)
  await view.button('虚构私密标题').props.onClick(); await flush()

  assert.equal(view.textarea(), undefined, 'no editor until 编辑 is pressed')
  assert.ok(view.anyButton('编辑'), 'the article offers an explicit edit action')
  assert.match(view.text(), /正文段落/, 'the body is rendered for reading')
  assert.ok(view.anyButton('返回列表'), 'reading can be left without saving anything')
  await view.button('编辑').props.onClick(); await flush()
  assert.ok(view.textarea(), 'editing is entered only on request')
  view.app.unmount()
})
