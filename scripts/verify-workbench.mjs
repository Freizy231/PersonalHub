#!/usr/bin/env node
/**
 * 迭代 1 浏览器走查（真实 Edge/Chrome + CDP）。
 *
 * 检查项（每个页面 × 桌面 1280 / 窄屏 390）：
 *   1. 控制台错误与未捕获异常
 *   2. 失败的网络请求（HTTP ≥ 400 或 requestfailed）
 *   3. 横向溢出（document.scrollWidth > 视口宽度）
 *   4. 可点击控件的尺寸下限（< 24×24 记为进入 hover 高度已经不是 AA 目标，
 *      这里只报告，不当作失败）
 *   5. 截图（桌面全页 + 窄屏全页）
 *
 * 用法：
 *   node scripts/verify-workbench.mjs --origin http://127.0.0.1:5199 --out /tmp/shots
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5199').replace(/\/$/, '')
const OUT = args.get('out') ?? '/tmp/ph-shots'
const PORT = Number(args.get('port') ?? 9444)

const PAGES = [
  { path: '/', name: 'home' },
  { path: '/tasks', name: 'tasks' },
  { path: '/jobs', name: 'jobs' },
  { path: '/activity', name: 'activity' },
]

const VIEWPORTS = [
  { name: 'desktop', width: 1280, height: 900 },
  { name: 'mobile', width: 390, height: 844 },
]

const BROWSERS = [
  process.env.PH_BROWSER,
  '/usr/bin/microsoft-edge',
  '/usr/bin/google-chrome',
  '/usr/bin/chromium',
  '/usr/bin/chromium-browser',
].filter(Boolean)
const browserPath = BROWSERS.find((path) => existsSync(path))
if (!browserPath) {
  console.error('找不到可用的无头浏览器。可用 PH_BROWSER 指定。')
  process.exit(2)
}

mkdirSync(OUT, { recursive: true })
const profile = mkdtempSync(join(tmpdir(), 'ph-walk-'))
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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function cdpJson(path) {
  const response = await fetch(`http://127.0.0.1:${PORT}${path}`)
  return response.json()
}

async function connect(target) {
  const socket = new WebSocket(target.webSocketDebuggerUrl)
  await new Promise((resolve, reject) => {
    socket.addEventListener('open', resolve)
    socket.addEventListener('error', reject)
  })
  let nextId = 0
  const pending = new Map()
  const listeners = []
  socket.addEventListener('message', (event) => {
    const message = JSON.parse(event.data)
    if (message.id && pending.has(message.id)) {
      const { resolve: ok, reject: bad } = pending.get(message.id)
      pending.delete(message.id)
      if (message.error) bad(new Error(JSON.stringify(message.error)))
      else ok(message.result)
      return
    }
    for (const listener of listeners) listener(message)
  })
  const call = (method, params = {}) =>
    new Promise((resolve, reject) => {
      const id = ++nextId
      pending.set(id, { resolve, reject })
      socket.send(JSON.stringify({ id, method, params }))
    })
  return { socket, call, on: (fn) => listeners.push(fn) }
}

async function waitForBrowser() {
  const deadline = Date.now() + 20_000
  while (Date.now() < deadline) {
    try {
      const targets = await cdpJson('/json/list')
      const page = targets.find((item) => item.type === 'page')
      if (page) return page
    } catch {
      /* 还没起来 */
    }
    await sleep(250)
  }
  throw new Error('浏览器没有就绪')
}

const results = []
let ws

try {
  const page = await waitForBrowser()
  const client = await connect(page)
  ws = client.socket

  await client.call('Page.enable')
  await client.call('Runtime.enable')
  await client.call('Network.enable')
  await client.call('Log.enable')

  let issues = { console: [], network: [] }
  client.on((message) => {
    if (message.method === 'Runtime.consoleAPICalled' && message.params.type === 'error') {
      issues.console.push(
        message.params.args.map((a) => a.value ?? a.description ?? a.type).join(' '),
      )
    }
    if (message.method === 'Runtime.exceptionThrown') {
      const detail = message.params.exceptionDetails
      issues.console.push(`未捕获异常：${detail.exception?.description ?? detail.text}`)
    }
    if (message.method === 'Log.entryAdded' && message.params.entry.level === 'error') {
      issues.console.push(`日志：${message.params.entry.text}`)
    }
    if (message.method === 'Network.responseReceived') {
      const { response } = message.params
      if (response.status >= 400) {
        issues.network.push(`${response.status} ${response.url}`)
      }
    }
    if (message.method === 'Network.loadingFailed' && !message.params.canceled) {
      issues.network.push(`failed ${message.params.errorText}`)
    }
  })

  for (const viewport of VIEWPORTS) {
    await client.call('Emulation.setDeviceMetricsOverride', {
      width: viewport.width,
      height: viewport.height,
      deviceScaleFactor: 1,
      mobile: viewport.name === 'mobile',
    })

    for (const target of PAGES) {
      issues = { console: [], network: [] }
      await client.call('Page.navigate', { url: `${ORIGIN}${target.path}` })
      await sleep(2200)

      const metrics = await client.call('Runtime.evaluate', {
        returnByValue: true,
        expression: `(() => {
          const doc = document.documentElement;
          const smallTargets = [...document.querySelectorAll('button, a, input')]
            .map((el) => {
              const r = el.getBoundingClientRect();
              return {
                text: (el.textContent || el.getAttribute('aria-label') || el.tagName).trim().slice(0, 30),
                w: Math.round(r.width),
                h: Math.round(r.height),
                visible: r.width > 0 && r.height > 0,
              };
            })
            .filter((t) => t.visible && (t.w < 24 || t.h < 24));
          return {
            title: document.title,
            scrollWidth: doc.scrollWidth,
            clientWidth: doc.clientWidth,
            bodyText: (document.body.innerText || '').slice(0, 4000),
            smallTargets,
          };
        })()`,
      })

      const value = metrics.result.value
      const screenshot = await client.call('Page.captureScreenshot', {
        format: 'png',
        captureBeyondViewport: true,
      })
      const file = join(OUT, `${target.name}-${viewport.name}.png`)
      writeFileSync(file, Buffer.from(screenshot.data, 'base64'))

      results.push({
        page: target.path,
        viewport: viewport.name,
        width: viewport.width,
        overflow: value.scrollWidth > value.clientWidth + 1,
        scrollWidth: value.scrollWidth,
        clientWidth: value.clientWidth,
        smallTargets: value.smallTargets,
        consoleErrors: issues.console,
        networkFailures: issues.network,
        textSample: value.bodyText.slice(0, 220).replace(/\s+/g, ' '),
        screenshot: file,
      })
    }
  }
} catch (error) {
  console.error(`走查失败：${error.message}`)
  process.exitCode = 1
} finally {
  ws?.close()
  try {
    process.kill(-browser.pid, 'SIGTERM')
  } catch {
    /* 已退出 */
  }
  try {
    rmSync(profile, { recursive: true, force: true, maxRetries: 5, retryDelay: 200 })
  } catch {
    /* 临时目录残留可接受 */
  }
}

writeFileSync(join(OUT, 'report.json'), JSON.stringify(results, null, 2))

let failed = false
for (const item of results) {
  const flags = []
  if (item.overflow) flags.push(`横向溢出 ${item.scrollWidth}>${item.clientWidth}`)
  if (item.consoleErrors.length) flags.push(`控制台错误 ${item.consoleErrors.length}`)
  if (item.networkFailures.length) flags.push(`网络失败 ${item.networkFailures.length}`)
  if (flags.length) failed = true
  console.log(
    `${flags.length ? '✗' : '✓'} ${item.page} @ ${item.viewport} (${item.width}px)` +
      (flags.length ? ` — ${flags.join('；')}` : ''),
  )
  if (item.smallTargets.length) {
    console.log(
      `    小控件：${item.smallTargets
        .map((t) => `${t.text}(${t.w}×${t.h})`)
        .slice(0, 6)
        .join('、')}`,
    )
  }
  for (const error of item.consoleErrors) console.log(`    控制台：${error}`)
  for (const failure of item.networkFailures) console.log(`    网络：${failure}`)
  console.log(`    文本：${item.textSample}`)
}

process.exitCode = failed ? 1 : 0
