const { assert, test, mount, flatten, text, flush } = require('./harness.cjs')
const { load } = require('./loader.cjs')

const markdown = load('shared/markdown.ts', {})
const View = load('shared/MarkdownView.vue', {}).default

test('Markdown reading renders real headings, lists, quotes and code', () => {
  const view = mount(View, { body: '# 标题\n\n一个 **重点** 与 `x`。\n\n- one\n- two\n\n> quote\n\n```py\nprint("hello")\n```' })
  const nodes = flatten(view.root)
  assert.ok(nodes.some(n => n.tag === 'h2' && text(n) === '标题'))
  assert.ok(nodes.some(n => n.tag === 'strong' && text(n) === '重点'))
  assert.equal(nodes.filter(n => n.tag === 'li').length, 2)
  assert.ok(nodes.some(n => n.tag === 'blockquote'))
  assert.ok(nodes.some(n => n.tag === 'pre' && text(n).includes('print')))
  view.app.unmount()
})

test('Markdown never renders raw HTML, images or executable links', () => {
  const view = mount(View, { body: '<script>alert(1)</script>\n\n![track](https://example.org/pixel)\n\n[bad](javascript:alert) [secret](https://user:pass@example.org) [ok](https://example.org/a)' })
  const nodes = flatten(view.root)
  assert.equal(nodes.some(n => ['script', 'img', 'iframe'].includes(n.tag)), false)
  const links = nodes.filter(n => n.tag === 'a')
  assert.equal(links.length, 1)
  assert.equal(links[0].props.href, 'https://example.org/a')
  assert.equal(links[0].props.rel, 'noopener noreferrer')
  view.app.unmount()
})

test('safeLink only allows hub resources and credential-free http(s) URLs', () => {
  assert.equal(markdown.safeLink('hub://resource/33333333-3333-4333-8333-333333333333'), '/resource/33333333-3333-4333-8333-333333333333')
  assert.equal(markdown.safeLink('file:///etc/passwd'), null)
  assert.equal(markdown.safeLink('data:text/html,hi'), null)
  assert.equal(markdown.safeLink('javascript:alert(1)'), null)
  assert.equal(markdown.safeLink('https://user:pass@example.org'), null)
  assert.equal(markdown.safeLink('https://example.org/a'), 'https://example.org/a')
})

test('the document panel offers both PDF and EPUB and labels each format', async () => {
  const { mountComponent, setHandler, resetToasts } = require('./harness.cjs')
  resetToasts()
  setHandler(async (path) => {
    if (path.startsWith('/documents/jobs')) return { items: [] }
    if (path.startsWith('/readers')) return { items: [] }
    if (path.startsWith('/documents?')) {
      return {
        items: [
          { id: 'd1', kind: 'document', title: '论文', version: 1, deleted_at: null, format: 'pdf', read_state: 'unread', extraction_status: 'ready', current_page: 0, page_count: 12, size: 2048 },
          { id: 'd2', kind: 'document', title: '电子书', version: 1, deleted_at: null, format: 'epub', read_state: 'reading', extraction_status: 'ready', current_page: 0, page_count: 7, size: 4096 },
        ],
        total: 2, limit: 30, offset: 0,
      }
    }
    return { items: [], total: 0 }
  })
  const view = mountComponent('modules/library/documents/DocumentPanel.vue', { enabled: true })
  await flush(); await flush()
  const input = flatten(view.root).find(n => n.tag === 'input' && n.props.type === 'file')
  assert.ok(input, 'file picker exists')
  assert.match(String(input.props.accept), /epub/)
  assert.match(view.text(), /EPUB/)
  assert.match(view.text(), /7 章/, 'EPUB counts chapters, not pages')
  assert.match(view.text(), /12 页/, 'PDF still counts pages')
  view.app.unmount()
})
