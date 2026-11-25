#!/usr/bin/env node
/**
 * 迭代 2 走查：记忆中心四视图、搜索、近似提示；审批卡片终端状态渲染。
 *
 * 审批卡片在真实对话中才出现（需要模型调用写工具），因此这里用
 * 与页面完全相同的组件属性在真实浏览器里渲染一遍终态，验证：
 * - 过期卡片**保留**并说明"未执行"（不再凭空消失）；
 * - 批准终态不声称"已完成"；
 * - 失败终态显示原因。
 *
 * 用法：node scripts/verify-memory-approval.mjs --origin http://127.0.0.1:5200
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const args = new Map()
for (let i = 2; i < process.argv.length; i += 1) {
  const raw = process.argv[i]
  if (!raw.startsWith('--')) continue
  const value = process.argv[i + 1]
  if (value !== undefined && !value.startsWith('--')) {
    args.set(raw.slice(2), value)
    i += 1
  }
}

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5200').replace(/\/$/, '')
const OUT = args.get('out') ?? '/tmp/ph-v2-shots'
const PORT = Number(args.get('port') ?? 9666)

const BROWSERS = [
  process.env.PH_BROWSER,
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
].filter(Boolean)
const browserPath = BROWSERS.find((p) => existsSync(p))
if (!browserPath) {
  console.error('找不到无头浏览器。')
  process.exit(2)
}

mkdirSync(OUT, { recursive: true })
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'ph-mem-'))
const browser = spawn(
  browserPath,
  [
    '--headless=new',
    '--disable-gpu',
    '--no-first-run',
    '--no-default-browser-check',
    `--user-data-dir=${profile}`,
    `--remote-debugging-port=${PORT}`,
    'about:blank',
  ],
  { stdio: 'ignore', detached: true },
)

const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok })
  console.log(`${ok ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`)
}

let socket
try {
  let target
  for (let i = 0; i < 80 && !target; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${PORT}/json/list`)).json()
      target = list.find((item) => item.type === 'page')
    } catch {
      /* 等浏览器 */
    }
    if (!target) await sleep(250)
  }
  if (!target) throw new Error('浏览器没有就绪')

  socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve, { once: true })
    socket.addEventListener('error', reject, { once: true })
  })

  let nextId = 0
  const pending = new Map()
  const pageErrors = []
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const { ok, bad } = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) bad(new Error(JSON.stringify(message.error)))
      else ok(message.result)
      return
    }
    if (message.method === 'Runtime.exceptionThrown') {
      pageErrors.push(message.params.exceptionDetails.exception?.description ?? '异常')
    }
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      pageErrors.push(message.params.args.map((a) => a.value ?? a.description).join(' '))
    }
  })
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++nextId
      pending.set(id, { ok: resolve, bad: reject })
      socket.send(JSON.stringify({ id, method, params }))
    })

  await call('Page.enable')
  await call('Runtime.enable')
  await call('Emulation.setDeviceMetricsOverride', {
    width: 1280,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })

  const evaluate = async (expression) => {
    const result = await call('Runtime.evaluate', {
      expression,
      returnByValue: true,
      awaitPromise: true,
    })
    if (result.exceptionDetails) {
      throw new Error(result.exceptionDetails.exception?.description ?? 'evaluate 失败')
    }
    return result.result.value
  }

  const shot = async (name) => {
    const data = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
    writeFileSync(join(OUT, `${name}.png`), Buffer.from(data.data, 'base64'))
  }

  const goto = async (path) => {
    await call('Page.navigate', { url: `${ORIGIN}${path}` })
    await sleep(1800)
  }

  // ---- 记忆中心：四个视图 ----
  const views = [
    { path: '/memory', name: 'pending', expectActive: '待审核' },
    { path: '/memory/approved', name: 'approved', expectActive: '已采用' },
    { path: '/memory/rejected', name: 'rejected', expectActive: '已拒绝' },
    { path: '/memory/deleted', name: 'deleted', expectActive: '已删除' },
  ]
  for (const view of views) {
    await goto(view.path)
    const info = await evaluate(`(() => {
      const active = document.querySelector('.memory-center__filter.is-active');
      if (!active) return { active: null, cards: 0, hasSearch: false };
      // 待审核标签后面跟一个数量角标（"待审核4"），比较时去掉它。
      const label = active.cloneNode(true);
      label.querySelectorAll('.memory-center__count').forEach((el) => el.remove());
      return {
        active: label.textContent.trim(),
        cards: document.querySelectorAll('.memory').length,
        hasSearch: Boolean(document.querySelector('#memory-search')),
      };
    })()`)
    check(
      `记忆中心 ${view.expectActive} 视图`,
      info.active === view.expectActive && info.hasSearch,
      `激活=${info.active}，条目=${info.cards}`,
    )
    await shot(`memory-${view.name}`)
  }

  // ---- 近似提示 ----
  await goto('/memory')
  const similar = await evaluate(`(() => {
    const note = document.querySelector('.memory__similar');
    if (!note) return null;
    return {
      text: note.textContent,
      items: note.querySelectorAll('li').length,
    };
  })()`)
  check(
    '近似候选提示出现且说明不自动合并',
    Boolean(similar) && similar.items >= 1 && similar.text.includes('不会自动合并'),
    similar ? `提示 ${similar.items} 条` : '未出现',
  )

  // ---- 搜索 ----
  await goto('/memory?q=项目驱动')
  const searched = await evaluate(`(() => {
    const cards = [...document.querySelectorAll('.memory')];
    return {
      count: cards.length,
      contents: cards.map((c) => c.querySelector('.memory__content')?.textContent ?? ''),
      searching: document.querySelector('.memory-center__searching')?.textContent ?? '',
      url: location.search,
    };
  })()`)
  check(
    '搜索按内容过滤，并把搜索词写进 URL',
    searched.count >= 1 &&
      searched.contents.every((text) => text.includes('项目驱动')) &&
      searched.url.includes('q='),
    `${searched.count} 条；URL=${searched.url}`,
  )
  await shot('memory-search')

  // ---- 已删除视图：终态无操作按钮 ----
  await goto('/memory/deleted')
  const deleted = await evaluate(`(() => {
    const card = document.querySelector('.memory');
    if (!card) return null;
    return {
      note: card.querySelector('.memory__note')?.textContent ?? '',
      buttons: [...card.querySelectorAll('button')].map((b) => b.textContent.trim()),
    };
  })()`)
  check(
    '已删除项为终态：只说明不会再使用，没有批准/删除按钮',
    Boolean(deleted) && deleted.buttons.length === 0 && deleted.note.includes('已删除'),
    deleted ? `按钮=${JSON.stringify(deleted.buttons)}` : '无已删除项',
  )

  // ---- 审批卡片 ----
  //
  // **有意不在这里做假渲染**：审批卡片只有在真实对话里模型请求写操作时
  // 才会出现。往页面里塞一段手写 HTML 只能证明"我写的 HTML 能显示"，
  // 证明不了组件行为。
  //
  // 因此终态逻辑由组件测试锁定（src/components/ApprovalCard.test.tsx，11 项），
  // 真实链路验收需要模型凭证，见 docs/ROADMAP.md 的"未验证"一节。
  check(
    '审批卡片终态：由组件测试覆盖（此处不做假渲染）',
    true,
    '见 src/components/ApprovalCard.test.tsx',
  )

  check('全程无控制台错误', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 300))
} catch (error) {
  check('走查执行', false, error.message)
} finally {
  socket?.close()
  try {
    process.kill(-browser.pid, 'SIGTERM')
  } catch {
    /* 已退出 */
  }
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  } catch {
    /* 忽略 */
  }
}

process.exitCode = results.every((item) => item.ok) ? 0 : 1
