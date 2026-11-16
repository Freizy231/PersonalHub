// Memory renderer + real component loading. This verifies component behaviour,
// not browser layout: no CSS, no real network.
const assert = require('node:assert/strict')
const test = require('node:test')
const Vue = require('vue')
const { createRenderer, defineComponent, h, nextTick } = Vue
const { load } = require('./loader.cjs')

class ApiError extends Error { constructor(code, status) { super(code); this.code = code; this.status = status } }

let handler = async () => ({})

const transport = {
  ApiError,
  api: (...args) => handler(...args),
  apiBlob: async (...args) => handler(...args),
  connectSession: async () => {},
}

if (!globalThis.window) globalThis.window = { addEventListener() {}, removeEventListener() {}, localStorage: { getItem: () => null, setItem() {} } }
if (!globalThis.window.setTimeout) globalThis.window.setTimeout = (fn, ms) => setTimeout(fn, ms)
if (!globalThis.window.clearTimeout) globalThis.window.clearTimeout = id => clearTimeout(id)
if (!globalThis.window.matchMedia) globalThis.window.matchMedia = () => ({ matches: false, addEventListener() {}, removeEventListener() {} })
if (!globalThis.window.confirm) globalThis.window.confirm = () => true
if (!globalThis.window.location) globalThis.window.location = { port: '8765', hash: '', pathname: '/', search: '', href: 'http://127.0.0.1:8765/' }
if (!globalThis.window.history) globalThis.window.history = { replaceState() {}, pushState() {} }
if (!globalThis.history) globalThis.history = globalThis.window.history
if (!globalThis.location) globalThis.location = globalThis.window.location
if (!globalThis.window.scrollTo) globalThis.window.scrollTo = () => {}
if (!globalThis.localStorage) globalThis.localStorage = window.localStorage
if (!globalThis.HTMLElement) globalThis.HTMLElement = class HTMLElement {}
if (!globalThis.document) {
  globalThis.Document = class Document {}
  globalThis.document = Object.assign(new globalThis.Document(), {
    activeElement: null,
    body: { style: {} },
    addEventListener() {}, removeEventListener() {},
    hidden: false,
    querySelector: () => null,
    querySelectorAll: () => [],
  })
}
// Real timers would keep the process alive after a component unmounts.
// Components must clear their own intervals; the tests assert that behaviour.
const realSetInterval = global.setInterval
global.setInterval = (fn, ms) => ({ __testInterval: true, fn, ms })
global.clearInterval = () => {}
if (!globalThis.crypto || !globalThis.crypto.randomUUID) {
  let n = 0
  globalThis.crypto = { randomUUID: () => `00000000-0000-4000-8000-${String(++n).padStart(12, '0')}` }
}

const ui = {}

function node(tag, text = '') {
  return {
    tag, text, props: {}, children: [], parent: null, events: {}, value: '', type: '',
    checked: false,
    getRootNode() { return globalThis.document },
    focus() { globalThis.document.activeElement = this },
    addEventListener(name, fn) { this.events[name] = fn },
    removeEventListener(name) { delete this.events[name] },
    setAttribute(name, value) { this.props[name] = value },
    removeAttribute(name) { delete this.props[name] },
    querySelector() { return null },
    querySelectorAll() { return [] },
    get options() { return this.children.filter(n => n.tag === 'option') },
  }
}

const bodyNode = node('body')
const renderer = createRenderer({
  querySelector: selector => (selector === 'body' || selector === '#body' ? bodyNode : null),
  createElement: tag => node(tag),
  createText: text => node('#text', text),
  createComment: text => node('#comment', text),
  insert(child, parent, anchor = null) {
    if (child.parent) {
      const i = child.parent.children.indexOf(child)
      if (i >= 0) child.parent.children.splice(i, 1)
    }
    child.parent = parent
    const i = anchor ? parent.children.indexOf(anchor) : -1
    if (i < 0) parent.children.push(child)
    else parent.children.splice(i, 0, child)
  },
  remove(child) {
    if (child.parent) {
      const i = child.parent.children.indexOf(child)
      if (i >= 0) child.parent.children.splice(i, 1)
    }
  },
  setText: (n, text) => { n.text = text },
  setElementText(n, text) { n.text = text; n.children = [] },
  parentNode: n => n.parent,
  nextSibling: n => n.parent?.children[n.parent.children.indexOf(n) + 1] || null,
  patchProp(n, k, old, value) {
    n.props[k] = value
    if (k === 'value') n.value = value
    if (k === 'checked') n.checked = value
    if (k === 'type') n.type = value
  },
})

function flatten(n) { return [n, ...n.children.flatMap(flatten)] }
function allNodes(root) { return [...flatten(root), ...flatten(bodyNode)] }
globalThis.__hubBody = bodyNode
function text(n) { return (n.tag === '#comment' ? '' : n.text) + n.children.map(text).join('') }

const flush = async () => {
  await nextTick()
  await new Promise(resolve => setImmediate(resolve))
  await nextTick()
}

const sample = {
  id: '11111111-1111-4111-8111-111111111111', title: '已有任务', description: '原说明',
  status: 'todo', priority: 2, planned_date: null, due_date: null, due_at: null,
  timezone: 'Asia/Shanghai', version: 1, deleted_at: null, is_overdue: false,
}

/**
 * Mounts a real component path (relative to src) with the shared api
 * transport wired in. Overrides may replace any import specifier.
 */
function mountComponent(path, props = {}, overrides = {}, plugins = []) {
  const component = load(path, { '../../../shared/api': transport, '../../shared/api': transport, './api': transport, ...overrides }).default
  bodyNode.children = []
  const root = node('root')
  const app = renderer.createApp(component, props)
  for (const plugin of plugins) app.use(plugin)
  app.component('router-link', defineComponent({
    props: ['to'],
    setup(p, { slots }) { return () => h('a', { href: typeof p.to === 'string' ? p.to : '#' }, slots.default?.()) },
  }))
  app.mount(root)
  const find = (tag, label) => allNodes(root).find(n => n.tag === tag && text(n).trim() === label)
  return {
    root, app, find, nodes: () => allNodes(root),
    button(label) { const n = find('button', label); assert.ok(n, `Missing button: ${label}`); return n },
    anyButton(label) { return allNodes(root).find(n => n.tag === 'button' && text(n).includes(label)) },
    input(predicate) { return allNodes(root).find(n => n.tag === 'input' && predicate(n)) },
    textarea() { return allNodes(root).find(n => n.tag === 'textarea') },
    text() { return allNodes(root).map(text).join('') },
  }
}

module.exports = {
  assert, test, Vue, ui, mountComponent, mount: (Component, props) => {
    const root = node('root')
    const app = renderer.createApp(Component, props)
    app.mount(root)
    const find = (tag, label) => flatten(root).find(n => n.tag === tag && text(n).trim() === label)
    return { root, app, find, button: label => { const n = find('button', label); assert.ok(n, `Missing button: ${label}`); return n }, text: () => text(root) }
  },
  flatten, text, flush, sample, transport, ApiError,
  setHandler: fn => { handler = fn },
  getHandler: () => handler,
  resetBody: () => { bodyNode.children = [] },
  toastText: () => {
    const mod = load('shared/toast.ts', {})
    return mod.useToast().items.value.map(t => t.message)
  },
  resetToasts: () => { load('shared/toast.ts', {}).clearToasts() },
}
