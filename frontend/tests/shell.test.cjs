const { assert, test, mountComponent, flatten, flush, setHandler } = require('./harness.cjs')
const { load } = require('./loader.cjs')

test('dark mode is applied to <html> so teleported dialogs inherit it', () => {
  const classes = new Set()
  const previous = globalThis.document
  globalThis.document = { ...previous, documentElement: { classList: { toggle: (name, on) => (on ? classes.add(name) : classes.delete(name)) } } }
  const theme = load('shared/theme.ts', {})
  theme.applyTheme(true)
  assert.ok(classes.has('dark'))
  theme.applyTheme(false)
  assert.equal(classes.has('dark'), false)
  assert.equal(theme.resolveDark('system', true), true)
  assert.equal(theme.resolveDark('light', true), false)
  assert.equal(theme.resolveDark('dark', false), true)
  globalThis.document = previous
})

test('tokens: dark variables are declared for <html> and muted text meets 4.5:1 on every surface', () => {
  const css = require('node:fs').readFileSync(require('node:path').join(__dirname, '../src/tokens.css'), 'utf8')
  assert.match(css, /:root\.dark/, 'dark tokens apply to the document root')
  const light = css.split(':root.dark')[0]
  const hex = name => light.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`))[1]
  const lum = h => { const [r, g, b] = [1, 3, 5].map(i => parseInt(h.slice(i, i + 2), 16) / 255).map(c => c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4); return 0.2126 * r + 0.7152 * g + 0.0722 * b }
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p); return (x + 0.05) / (y + 0.05) }
  for (const surface of ['bg', 'surface', 'surface-2', 'sidebar', 'accent-soft']) {
    assert.ok(ratio(hex('muted'), hex(surface)) >= 4.5, `muted text on ${surface} is below 4.5:1`)
  }
})

test('confirmAction resolves from the dialog host and falls back to the browser without one', async () => {
  const confirmModule = load('shared/confirm.ts', {})
  const original = globalThis.window.confirm
  let asked = ''
  globalThis.window.confirm = message => { asked = message; return true }
  assert.equal(await confirmModule.confirmAction('将删除 3 项'), true)
  assert.equal(asked, '将删除 3 项', 'no host mounted: native fallback keeps callers working')
  globalThis.window.confirm = original

  const unregister = confirmModule.registerConfirmHost()
  const pending = confirmModule.confirmAction('覆盖版本 4', { danger: true, confirmLabel: '覆盖' })
  assert.equal(confirmModule.pendingConfirm.value.body, '覆盖版本 4')
  assert.equal(confirmModule.pendingConfirm.value.danger, true)
  confirmModule.pendingConfirm.value.resolve(false)
  assert.equal(await pending, false)
  assert.equal(confirmModule.pendingConfirm.value, null)

  const first = confirmModule.confirmAction('第一个')
  const second = confirmModule.confirmAction('第二个')
  assert.equal(await first, false, 'a newer request cancels the older one instead of stacking dialogs')
  confirmModule.pendingConfirm.value.resolve(true)
  assert.equal(await second, true)
  unregister()
})

test('the command palette lists page commands and searches resources after two characters', async () => {
  const searched = []
  setHandler(async path => {
    searched.push(path)
    return { items: [{ id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', kind: 'note', title: 'PE 导入表笔记', snippet: '导入表解析', version: 1, deleted_at: null }], total: 1 }
  })
  const opened = []
  const commands = [{ id: 'go:/tasks', label: '任务', hint: '今天', icon: 'check', run: () => opened.push('tasks') }]
  const view = mountComponent('shared/CommandPalette.vue', { open: true, commands }); await flush()
  const options = () => view.nodes().filter(n => n.props && n.props.role === 'option')
  assert.equal(options().length, 1, 'page commands are listed before typing')
  assert.equal(searched.length, 0, 'no request for an empty query')

  const input = view.input(n => n.props.role === 'combobox')
  assert.ok(input)
  input.value = '导入'
  input.events.input?.({ target: input })
  await new Promise(resolve => setTimeout(resolve, 260)); await flush()
  assert.ok(searched.some(p => p.startsWith('/search?q=')), 'resource search runs')
  assert.ok(options().some(n => /PE 导入表笔记/.test(require('./harness.cjs').text(n))), 'a matching resource is offered')
  view.app.unmount()
})
