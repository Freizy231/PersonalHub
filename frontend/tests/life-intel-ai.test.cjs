const { assert, test, mountComponent, flatten, text, flush, ApiError, setHandler, resetToasts, toastText } = require('./harness.cjs')

function mountFinance() { resetToasts(); return mountComponent('modules/life/finance/FinancePanel.vue', { enabled: true }) }
function mountExercise() { resetToasts(); return mountComponent('modules/life/exercise/ExercisePanel.vue', { enabled: true }) }
function mountIntel() { resetToasts(); return mountComponent('modules/intelligence/IntelligencePanel.vue', { enabled: true }) }
function mountAssistant(props = {}) { resetToasts(); return mountComponent('modules/assistant/AssistantPanel.vue', { enabled: true, ...props }) }

async function typeInto(node, value) { node.value = value; node.events.input?.({ target: node }); await flush() }

test('the ledger loads read-only and keeps the amount as decimal text', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push(path); return {} }
    if (path === '/finance/categories') return { items: [{ id: 'c1', name: '餐饮', direction: 'expense' }] }
    if (path.startsWith('/finance/entries')) return { items: [{ id: 'e1', local_date: '2026-11-13', category_id: 'c1', category_name: '餐饮', direction: 'expense', currency: 'CNY', amount: '12.30', memo: '', version: 1 }], total: 1 }
    if (path.startsWith('/finance/report')) return { month: '2026-11', totals: [{ currency: 'CNY', income: '0.00', expense: '12.30', balance: '-12.30' }], categories: [], budgets: [] }
    return { items: [] }
  })
  const view = mountFinance(); await flush()
  assert.equal(writes.length, 0, 'opening the ledger performs no writes')
  assert.match(view.text(), /12\.30/)

  await view.button('＋ 记一笔').props.onClick(); await flush()
  const amount = view.input(n => n.props.inputmode === 'decimal')
  await typeInto(amount, '1.20')
  assert.equal(view.input(n => n.props.inputmode === 'decimal').value, '1.20', 'decimal text is preserved')
  view.app.unmount()
})

test('a failed ledger save keeps the exact idempotency key and payload on retry', async () => {
  const writes = []
  let attempt = 0
  setHandler(async (path, options) => {
    if (options) {
      writes.push({ path, options }); attempt++
      if (attempt === 1) throw new Error('lost response')
      return {}
    }
    if (path === '/finance/categories') return { items: [{ id: 'c1', name: '餐饮', direction: 'expense' }] }
    if (path.startsWith('/finance/entries')) return { items: [], total: 0 }
    if (path.startsWith('/finance/report')) return { month: '2026-11', totals: [], categories: [], budgets: [] }
    return { items: [] }
  })
  const view = mountFinance(); await flush()
  await view.button('＋ 记一笔').props.onClick(); await flush()
  await typeInto(view.input(n => n.props.inputmode === 'decimal'), '1.20')

  const form = view.nodes().filter(n => n.tag === 'form').pop()
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  assert.ok(toastText().some(m => m.includes('lost response')))

  const formAgain = view.nodes().filter(n => n.tag === 'form').pop()
  await formAgain.props.onSubmit({ preventDefault() {} }); await flush()

  assert.equal(writes.length, 2)
  assert.equal(writes[0].options.headers['Idempotency-Key'], writes[1].options.headers['Idempotency-Key'])
  assert.equal(writes[0].options.body, writes[1].options.body)
  view.app.unmount()
})

test('exercise statistics use only actual records and stay separate from plans', async () => {
  setHandler(async (path) => {
    if (path.startsWith('/exercise/month')) {
      return {
        month: '2026-11',
        plans: [{ id: 'p1', local_date: '2026-11-13', title: '晨跑', kind: 'run', duration_seconds: 1800, distance_meters: 5000, cancelled: 0, version: 1 }],
        records: [],
        statistics: [],
      }
    }
    return { items: [] }
  })
  const view = mountExercise(); await flush()
  assert.match(view.text(), /晨跑/)
  assert.match(view.text(), /本月还没有实际运动记录/)
  view.app.unmount()
})

test('intelligence lists unconnected sources explicitly and never collects implicitly', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push(path); return { status: 'success', saved: 0 } }
    if (path.startsWith('/intelligence/items')) return { items: [], total: 0 }
    if (path === '/intelligence/sources') return { items: [], unconnected: [{ kind: 'reddit', reason: '需独立接口权限' }], github: '' }
    if (path === '/intelligence/subscriptions') return { items: [] }
    if (path === '/intelligence/runs') return { items: [] }
    return { items: [] }
  })
  const view = mountIntel(); await flush()
  assert.equal(writes.length, 0, 'opening the panel never starts a collection')
  assert.match(view.text(), /暂无情报/)

  await view.button('来源').props.onClick(); await flush()
  assert.match(view.text(), /reddit/)
  assert.equal(writes.length, 0)
  view.app.unmount()
})

test('the assistant previews context before a separate explicit send', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) writes.push({ path, options })
    if (path === '/assistant/config') return { endpoint: 'https://api.example.com/v1/chat/completions', model: 'm', enabled: true, version: 1, has_token: true, needs_rebind: false }
    if (path === '/assistant/preview') return { id: 'p1', digest: 'd'.repeat(64), endpoint: 'https://api.example.com', model: 'm', question: 'q', sources: [{ id: 's1', kind: 'note', title: '第一份', version: 1, citation: 1 }], expires_in_seconds: 300, notice: '将发送问题与资料正文' }
    if (path === '/assistant/confirm') return { id: 'r1', status: 'starting' }
    if (path.startsWith('/assistant/runs/')) return { id: 'r1', status: 'success', answer: '回答' }
    return { items: [] }
  })
  const view = mountAssistant(); await flush()

  await typeInto(view.textarea(), '请总结')
  await view.button('预览并确认发送').props.onClick(); await flush()

  assert.equal(writes.length, 1, 'preview alone sends nothing to the model')
  assert.equal(writes[0].path, '/assistant/preview')
  assert.match(view.text(), /将发送问题与资料正文/)

  await view.button('确认发送').props.onClick(); await flush()
  const confirm = writes.find(w => w.path === '/assistant/confirm')
  assert.ok(confirm)
  assert.equal(JSON.parse(confirm.options.body).preview_id, 'p1')
  view.app.unmount()
})

test('assistant config clears the secret field and never echoes the token', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, options }); return { endpoint: 'https://api.example.com/v1/chat/completions', model: 'm', enabled: true, version: 2, has_token: true, needs_rebind: false } }
    return { endpoint: '', model: '', enabled: false, version: 1, has_token: false, needs_rebind: false }
  })
  const view = mountAssistant({ mode: 'settings' }); await flush()
  await typeInto(view.input(n => n.type === 'password'), 'TEST_FAKE_TOKEN')

  assert.equal(writes.length, 0, 'typing alone never saves')
  await view.button('保存配置').props.onClick(); await flush()
  const save = writes.find(w => w.path === '/assistant/config')
  assert.ok(save)
  assert.equal(JSON.parse(save.options.body).token, 'TEST_FAKE_TOKEN')
  assert.equal(view.input(n => n.type === 'password').value, '', 'the secret is cleared after saving')
  assert.doesNotMatch(view.text(), /TEST_FAKE_TOKEN/)
  view.app.unmount()
})

test('a preset question asks before replacing typed text', async () => {
  setHandler(async (path) => path === '/assistant/config'
    ? { endpoint: 'https://api.example.com/v1/chat/completions', model: 'm', enabled: true, version: 1, has_token: true, needs_rebind: false }
    : { items: [] })
  const view = mountAssistant(); await flush()
  await typeInto(view.textarea(), '我正在写的问题')

  const original = window.confirm
  window.confirm = () => false
  const preset = view.nodes().find(n => n.tag === 'button' && text(n).includes('核心结论'))
  assert.ok(preset)
  await preset.props.onClick(); await flush()
  assert.equal(view.textarea().value, '我正在写的问题', 'declined replacement keeps the draft')
  window.confirm = original
  view.app.unmount()
})

test('trashing or restoring a ledger entry or a workout sends an Idempotency-Key, which the server requires', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, options }); return { id: 'x', version: 2 } }
    if (path === '/finance/categories') return { items: [{ id: 'c1', name: '餐饮', direction: 'expense' }] }
    if (path.startsWith('/finance/entries')) return { items: [{ id: 'e1', local_date: '2026-11-13', category_id: 'c1', category_name: '餐饮', direction: 'expense', currency: 'CNY', amount: '12.30', memo: '', version: 1 }], total: 1 }
    if (path.startsWith('/finance/report')) return { month: '2026-11', totals: [], categories: [], budgets: [] }
    return { items: [], total: 0 }
  })
  const view = mountFinance(); await flush()
  const remove = view.anyButton('删除') || view.anyButton('移入回收站')
  assert.ok(remove, 'the entry row offers a delete action')
  await remove.props.onClick(); await flush()
  const call = writes.find(w => /\/finance\/entries\/e1\/trash$/.test(w.path))
  assert.ok(call, 'the trash request was sent')
  assert.match(call.options.headers['Idempotency-Key'], /^[0-9a-f-]{36}$/i)
  view.app.unmount()

  const exercise = []
  setHandler(async (path, options) => {
    if (options) { exercise.push({ path, options }); return { id: 'p1', version: 2 } }
    if (path.startsWith('/exercise/month')) return { month: '2026-11', plans: [{ id: 'p1', title: '晨跑', local_date: '2026-11-13', kind: 'run', duration_seconds: 1800, distance_meters: 0, version: 1, cancelled: 0 }], records: [], statistics: [] }
    return { items: [] }
  })
  const second = mountExercise(); await flush()
  const trashButton = second.nodes().find(n => n.tag === 'button' && n.text.trim() === '删除')
  assert.ok(trashButton, 'the plan row offers 删除')
  await trashButton.props.onClick(); await flush()
  const sent = exercise.find(w => /\/exercise\/plans\/p1\/trash$/.test(w.path))
  assert.ok(sent, 'the workout trash request was sent')
  assert.ok(sent.options.headers['Idempotency-Key'])
  second.app.unmount()
})

test('an exercise plan or record with no link sends null, never an empty string', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, body: JSON.parse(options.body) }); return { id: 'n1', version: 1 } }
    if (path.startsWith('/exercise/month')) return { month: '2026-11', plans: [], records: [], statistics: [] }
    return { items: [] }
  })
  const view = mountExercise(); await flush()
  await view.button('＋ 计划').props.onClick(); await flush()
  const title = view.input(n => n.props.maxlength === '200') || view.input(n => /计划|名称/.test(n.props['aria-label'] || n.props.placeholder || ''))
  if (title) { title.value = '晨跑'; title.events.input?.({ target: title }); await flush() }
  await view.button('保存').props.onClick(); await flush()
  const plan = writes.find(w => w.path === '/exercise/plans')
  assert.ok(plan, 'the plan was submitted')
  assert.strictEqual(plan.body.task_id, null)
  assert.strictEqual(plan.body.event_id, null)
  view.app.unmount()
})

test('intelligence editors send exactly the fields the API accepts, with real booleans', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, method: options.method, body: JSON.parse(options.body) }); return { id: 's1', version: 1 } }
    if (path.startsWith('/intelligence/items')) return { items: [], total: 0 }
    if (path === '/intelligence/sources') return []
    if (path === '/intelligence/subscriptions') return { items: [] }
    if (path === '/intelligence/runs') return { items: [] }
    return { items: [] }
  })
  const view = mountIntel(); await flush()
  await view.button('订阅').props.onClick(); await flush()
  const addKeyword = view.anyButton('关键词')
  assert.ok(addKeyword, 'the subscriptions tab offers adding a keyword')
  await addKeyword.props.onClick(); await flush()
  const keyword = view.nodes().filter(n => n.tag === 'input')[0]
  keyword.value = 'rust'; keyword.events.input?.({ target: keyword }); await flush()
  const form = view.nodes().find(n => n.tag === 'form' && n.props.class === 'record-editor')
  assert.ok(form, 'the editor form is shown')
  await form.props.onSubmit({ preventDefault() {} }); await flush()
  const saved = writes.find(w => w.path === '/intelligence/subscriptions')
  assert.ok(saved, 'the keyword was submitted')
  assert.deepEqual(Object.keys(saved.body).sort(), ['enabled', 'expected_version', 'keyword'], 'no stray title field: the API forbids extra fields')
  assert.strictEqual(saved.body.enabled, true, 'enabled is a real boolean, not the string "true"')
  view.app.unmount()
})

test('intelligence shows a card per source, trends inline and both language summaries', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) { writes.push({ path, body: options.body }); return { status: 'success', received: 3, saved: 2 } }
    if (path.startsWith('/intelligence/items')) return { items: [{
      id: 'i1', title: 'rust-analyzer', url: 'https://example.com/r', source_key: 'github:legacy', domain: 'Rust',
      read_state: 'new', favorite: 0, summary: 'A Rust compiler front-end for IDEs', summary_zh: '一个面向 IDE 的 Rust 编译器前端',
      version: 1, updated_at: '2026-11-16T00:00:00Z',
      trend: { stars: [10, 21], forks: [2, 5], score: [], series: 3 },
    }], total: 1 }
    if (path === '/intelligence/sources') return { items: [{ id: 's1', name: 'arXiv 安全', kind: 'arxiv', endpoint: '', query: 'cat:cs.CR', enabled: 1, version: 1 }], unconnected: [], github: '' }
    if (path === '/intelligence/subscriptions') return { items: [] }
    if (path === '/intelligence/runs') return { items: [] }
    if (path === '/intelligence/schedule') return { enabled: false, runs_per_day: 2, local_hours: ['09:00', '21:00'], last_run_at: null, next_run_at: null, version: 0 }
    return { items: [] }
  })
  const view = mountIntel(); await flush()

  assert.equal(view.anyButton('趋势'), undefined, 'the trend is no longer hidden behind a button')
  assert.match(view.text(), /一个面向 IDE 的 Rust 编译器前端/, 'the Chinese summary is shown')
  assert.match(view.text(), /A Rust compiler front-end/, 'the original summary stays visible beside it')
  assert.ok(view.nodes().some(n => n.tag === 'polyline'), 'a star/fork sparkline is drawn in the row')

  await view.button('来源').props.onClick(); await flush()
  assert.match(view.text(), /arXiv 安全/, 'one card per source')
  assert.match(view.text(), /cat:cs\.CR/, 'the card shows what it pulls')
  assert.match(view.text(), /定时采集/, 'the schedule section is present and explicit')
  assert.equal(writes.length, 0, 'nothing is collected just by looking')

  await view.button('采集').props.onClick(); await flush()
  assert.ok(writes.some(w => w.path === '/intelligence/sources/s1/collect'), 'an explicit collect button sends the request')
  view.app.unmount()
})

test('the AI workspace turns a candidate plan into real records only when adopted', async () => {
  const writes = []
  setHandler(async (path, options) => {
    if (options) {
      writes.push({ path, body: options.body ? JSON.parse(options.body) : null })
      if (path === '/ai/conversations') return { id: 'conv-1' }
      if (path.endsWith('/plan')) return {
        id: 'plan-1', title: '四周 RAG 计划', rationale: '按你的 Python 基础和每天四十分钟',
        rejected: [{ code: 'CAPABILITY_UNKNOWN', detail: '第 3 步：system.shell' }],
        steps: [
          { position: 0, capability_id: 'learning.node.create', arguments: { title: '向量检索' }, state: 'pending' },
          { position: 1, capability_id: 'learning.practice.create', arguments: { title: '写余弦相似度' }, state: 'pending' },
        ], state: 'draft',
      }
      if (path === '/ai/plans/plan-1/adopt') return {
        id: 'run-1', status: 'success',
        steps: [{ position: 0, capability_id: 'learning.node.create', status: 'done',
                  summary: '已创建知识点「向量检索」', resource_ids: ['n1'], error_code: null }],
      }
      return {}
    }
    if (path.startsWith('/learning/learning_goal')) return { items: [{ id: 'g1', title: 'RAG 项目', version: 1 }] }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/assistant/WorkspacePanel.vue', { enabled: true })
  await flush(); await flush()

  assert.equal(writes.length, 0, 'opening the workspace writes nothing')
  const area = view.nodes().find(n => n.tag === 'textarea')
  area.value = '我想四周内做出一个能用的 RAG 小项目'
  area.events.input?.({ target: area })
  await flush()
  await view.button('生成计划草稿').props.onClick(); await flush(); await flush()

  const text = view.text()
  assert.match(text, /四周 RAG 计划/)
  assert.match(text, /创建知识点/)
  assert.match(text, /未通过校验/, 'refused steps are named, not silently dropped')
  assert.equal(writes.some(w => w.path.includes('/adopt')), false, 'previewing does not adopt')

  await view.button('采用计划').props.onClick(); await flush(); await flush()
  const adopt = writes.find(w => w.path === '/ai/plans/plan-1/adopt')
  assert.ok(adopt, 'adopting is an explicit second action')
  assert.match(view.text(), /全部完成/)
  view.app.unmount()
})

test('without a learning goal the workspace says where to start', async () => {
  setHandler(async () => ({ items: [] }))
  resetToasts()
  const view = mountComponent('modules/assistant/WorkspacePanel.vue', { enabled: true })
  await flush(); await flush()
  assert.match(view.text(), /还没有学习目标/)
  assert.match(view.text(), /学习/, 'it points at the page that creates one')
  view.app.unmount()
})

test('a review with no records is reported as an absence, not a judgement', async () => {
  setHandler(async (path, options) => {
    if (options && path === '/ai/review') return {
      observation: '目前没有记录到进展：这个目标下还没有练习任务或验收记录。',
      notice: '这些是系统记录到的数据；系统不会据此判断你拖延或能力不足。',
      next: [{ action: '添加新的知识点', reason: '当前节点都已验收或无待办' }],
    }
    if (path.startsWith('/learning/learning_goal')) return { items: [{ id: 'g1', title: 'RAG 项目', version: 1 }] }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/assistant/WorkspacePanel.vue', { enabled: true })
  await flush(); await flush()
  await view.button('看看进展').props.onClick(); await flush(); await flush()
  assert.match(view.text(), /没有记录到进展/)
  assert.match(view.text(), /不会据此判断你拖延/)
  view.app.unmount()
})

test('the two AI permissions are granted and revoked separately', async () => {
  const writes = []
  const granted = []
  setHandler(async (path, options) => {
    if (options) {
      const body = JSON.parse(options.body)
      writes.push({ path, body })
      granted.push({ id: 'grant-' + granted.length, kind: body.kind, scope_id: body.scope_id,
                     enabled: true, active: true, expires_at: '2027-01-01T00:00:00+08:00',
                     endpoint: '', model: '' })
      return {}
    }
    if (path.startsWith('/learning/learning_goal')) return { items: [{ id: 'g1', title: 'RAG 项目', version: 1 }] }
    if (path === '/ai/grants') return { items: granted }
    if (path === '/ai/templates') return { items: [
      { id: 't1', name: '论文精读（默认）', purpose: '读论文', version: 1, enabled: true, steps: [{ capability_id: 'notes.create', arguments: {} }] },
    ] }
    return { items: [] }
  })
  resetToasts()
  const view = mountComponent('modules/assistant/AiPermissions.vue', { enabled: true })
  await flush(); await flush()
  const text = view.text()
  assert.match(text, /允许在本机创建学习内容/)
  assert.match(text, /允许把资料发送给模型服务/)
  assert.equal(text.includes('JSON'), false, 'implementation details stay out of the product page')

  const grantButtons = () => view.nodes().filter(n => n.tag === 'button' && require('./harness.cjs').text(n).includes('授权 30 天'))
  const local = grantButtons()[0]
  await local.props.onClick(); await flush(); await flush()
  assert.equal(writes[0].body.kind, 'local_write')
  assert.equal(writes[0].body.scope_id, 'g1', 'the local grant is bound to a learning goal')
  assert.equal(writes[0].body.days, 30)

  // The local grant now shows 撤销, so the remaining 授权 button is the outbound one.
  const outbound = grantButtons()[0]
  assert.ok(outbound, 'the outbound permission is still ungranted')
  await outbound.props.onClick(); await flush(); await flush()
  assert.equal(writes[1].body.kind, 'outbound')
  assert.equal(writes[1].body.scope_id, null, 'sending is not tied to a learning goal')
  view.app.unmount()
})
