#!/usr/bin/env node
/**
 * 沙箱边界验证（M9 第 6 步）。
 *
 * 为什么用真实浏览器：应用的安全边界**不在后端**，而在浏览器的
 * iframe sandbox 与 CSP。单元测试只能验证"我们下发了正确的响应头"，
 * 无法回答"应用到底能不能读到父页面"——那必须让浏览器真的跑一遍。
 *
 * 用法（后端已启动，且应用库里至少有一个应用）：
 *
 *   node scripts/verify-app-sandbox.mjs \
 *     --origin http://127.0.0.1:5173 --app-id app-xxxx
 *
 * 两种使用方式：
 *
 * - **默认**：把 `--origin` 当作已跑起来的前端（`--page`，默认 /apps），
 *   在页面里找那个应用的 iframe。这是我实际验收界面的方式。
 * - **`--embed`**：在 `--origin` 的任意同源页面上注入一个与应用页
 *   完全相同的 iframe（`sandbox="allow-scripts"`，src 指向应用内容）。
 *   不依赖前端构建产物，因此可以放进自动化测试里跑。
 *
 * 它会：
 *   1. 启动无头 Edge/Chrome；
 *   2. 打开 `--page`（默认 /apps），等待应用 iframe 加载；
 *   3. 通过 CDP 进入 iframe 的执行上下文，读取探针应用写下的结果；
 *   4. 断言所有越界尝试都被拦下，并打印实测结果。
 *
 * 退出码：0 全部通过；1 有断言失败；2 环境不满足（无 Edge / 无应用）。
 *
 * 依赖：只用 Node 内置的 fetch 与 WebSocket（Node ≥ 22），不新增任何依赖。
 */

import { spawn } from 'node:child_process'
import { mkdtempSync, rmSync, existsSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

// 参数解析：`--key value`；无值的开关（如 --embed）不吞掉下一个参数。
// 用 Map 而不是手写循环，是为了让 `--embed --app-id x` 这种写法也正确。
const BOOLEAN_FLAGS = new Set(['embed'])
const args = new Map()
for (let i = 2; i < process.argv.length; i += 1) {
  const raw = process.argv[i]
  if (!raw.startsWith('--')) continue
  const key = raw.slice(2)
  if (BOOLEAN_FLAGS.has(key)) {
    args.set(key, true)
    continue
  }
  const value = process.argv[i + 1]
  if (value !== undefined && !value.startsWith('--')) {
    args.set(key, value)
    i += 1
  }
}

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5173').replace(/\/$/, '')
const PAGE = args.get('page') ?? '/apps'
const APP_ID = args.get('app-id')
const EMBED = args.has('embed')
const PORT = Number(args.get('port') ?? 9334)

const BROWSERS = [
  process.env.PH_BROWSER,
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean)

const browserPath = BROWSERS.find((path) => existsSync(path))
if (!browserPath) {
  console.error('找不到可用的无头浏览器（Edge/Chrome/Chromium）。可用 PH_BROWSER 指定。')
  process.exit(2)
}
if (!APP_ID) {
  console.error('必须提供 --app-id（被验证的应用 id）。')
  process.exit(2)
}

const profile = mkdtempSync(join(tmpdir(), 'ph-sandbox-'))
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

let ws
try {
  const target = await waitForIframe()
  ws = await connect(target)
  const probe = await inFrame(ws)
  report(probe)
} catch (error) {
  console.error(`验证失败：${error.message}`)
  process.exitCode = 1
} finally {
  ws?.close()
  try {
    process.kill(-browser.pid, 'SIGTERM')
  } catch {
    /* 已退出 */
  }
  // 浏览器刚收到 SIGTERM，profile 里可能还有正在写入的文件；
  // 删不掉只影响临时目录残留，不该让验证结果变成"崩溃"。
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  } catch {
    /* 临时目录残留可接受 */
  }
}

async function cdpJson(path) {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`)
  return response.json()
}

/** 打开页面，等待目标应用出现在 OOPIF 列表里（不透明来源的 iframe 是独立进程）。 */
async function waitForIframe() {
  const deadline = Date.now() + 30_000
  while (Date.now() < deadline) {
    try {
      const targets = await cdpJson('/json/list')
      const page = targets.find((item) => item.type === 'page')
      if (page) {
        await navigate(page, `${ORIGIN}${PAGE}`)
        if (EMBED) await injectIframe(page, `${ORIGIN}/api/v1/apps/${APP_ID}/content`)
        for (const wait of [1500, 2500, 4000]) {
          await sleep(wait)
          const now = (await cdpJson('/json/list')).find(
            (item) => item.type === 'iframe' && item.url.includes(`/apps/${APP_ID}/content`),
          )
          if (now) return now
        }
        throw new Error('等待应用 iframe 超时（页面里没有该应用的嵌入）')
      }
    } catch (error) {
      if (error.message.includes('超时')) throw error
    }
    await sleep(400)
  }
  throw new Error('浏览器没有就绪')
}

async function navigate(page, url) {
  const socket = new WebSocket(page.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve)
    socket.addEventListener('error', reject)
  })
  socket.send(JSON.stringify({ id: 1, method: 'Page.navigate', params: { url } }))
  await sleep(300)
  socket.close()
}

/**
 * 在页面里插入与 AppsPage 完全相同的 iframe。
 *
 * 属性必须与前端保持一致（sandbox、referrerPolicy），否则测的就不是同一个东西。
 * 如果这里放宽了 sandbox，测试会"通过"但界面仍然不安全——所以此处刻意写死。
 */
async function injectIframe(page, src) {
  const socket = await connect(page)
  let id = 0
  const done = new Promise((resolve) => setTimeout(resolve, 500))
  socket.send(
    JSON.stringify({
      id: ++id,
      method: 'Runtime.evaluate',
      params: {
        expression: `(() => {
          document.querySelectorAll('iframe').forEach((f) => f.remove());
          const frame = document.createElement('iframe');
          frame.className = 'app-frame';
          frame.setAttribute('sandbox', 'allow-scripts');
          frame.setAttribute('referrerpolicy', 'no-referrer');
          frame.src = ${JSON.stringify(src)};
          document.body.appendChild(frame);
        })()`,
      },
    }),
  )
  await done
  socket.close()
}

async function connect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve)
    socket.addEventListener('error', reject)
  })
  return socket
}

/** 在 iframe 内部读探针应用写下的结果，并自己再尝试一次越界。 */
async function inFrame(socket) {
  let nextId = 0
  const pending = new Map()
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message)
      pending.delete(message.id)
    }
  })
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++nextId
      pending.set(id, (message) =>
        message.error ? reject(new Error(JSON.stringify(message.error))) : resolve(message.result),
      )
      socket.send(JSON.stringify({ id, method, params }))
    })

  await call('Runtime.enable')
  // awaitPromise：fetch 的探针返回 Promise，不等待就会拿到一个空对象。
  const evaluate = async (expression) =>
    (await call('Runtime.evaluate', { expression, returnByValue: true, awaitPromise: true })).result
      .value

  return {
    origin: await evaluate('String(window.origin)'),
    parentDocument: await evaluate(
      '(() => { try { return "可读:" + window.parent.document.title } catch (e) { return "被拦:" + e.name } })()',
    ),
    localStorage: await evaluate(
      '(() => { try { return "可读:" + String(window.localStorage.getItem("x")) } catch (e) { return "被拦:" + e.name } })()',
    ),
    cookie: await evaluate(
      '(() => { try { return "可读:" + String(document.cookie) } catch (e) { return "被拦:" + e.name } })()',
    ),
    // 用动态属性访问，绕开静态检查的文本匹配——正是 D-019 记录的绕过手法。
    fetch: await evaluate(
      `(async () => {
        try {
          const name = "fe" + "tch";
          await window[name]("http://example.com/secret");
          return "成功（缺陷）";
        } catch (e) { return "被拦:" + e.name; }
      })()`,
    ),
  }
}

function report(probe) {
  const checks = [
    ['不透明来源（origin 为 null）', probe.origin === 'null', probe.origin],
    ['父页面不可读', probe.parentDocument.startsWith('被拦'), probe.parentDocument],
    ['localStorage 不可用', probe.localStorage.startsWith('被拦'), probe.localStorage],
    ['cookie 不可读', probe.cookie.startsWith('被拦'), probe.cookie],
    ['网络请求被阻断', probe.fetch.startsWith('被拦'), probe.fetch],
  ]

  let failed = 0
  for (const [label, ok, actual] of checks) {
    console.log(`${ok ? '✅' : '❌'} ${label} —— ${actual}`)
    if (!ok) failed += 1
  }
  if (failed) {
    console.error(`\n${failed} 项断言失败：沙箱边界被突破，不能发布。`)
    process.exitCode = 1
  } else {
    console.log('\n全部通过：应用在浏览器中确实无法越界。')
  }
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms))
}
