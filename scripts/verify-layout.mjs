#!/usr/bin/env node
/**
 * 布局验收（《PersonalHub_前端风格开发说明.md》§7）。
 *
 * 在 6 个验收视口下逐页测量真实渲染结果，而不是看代码推断：
 *   - 侧栏形态（完整 / 轨道 / 抽屉）与宽度
 *   - 主内容区实际宽度、内边距、是否仍有全局窄列限制
 *   - 导航标签是否可见（轨道模式必须保留文字，不能只剩图标）
 *   - 横向溢出、控制台错误
 *
 * 用法：
 *   node scripts/verify-layout.mjs --origin http://127.0.0.1:5173
 */

import { spawn } from 'node:child_process'
import { existsSync, mkdtempSync, rmSync } from 'node:fs'
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5173').replace(/\/$/, '')
const PORT = Number(args.get('port') ?? 9970)

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

/** 验收视口（规范 §7）。1440/1024 是规范里的断点边界，额外加上作为回归。 */
const VIEWPORTS = [
  { name: '1907px 超宽', width: 1907, height: 1000, expect: 'full' },
  { name: '1440px 桌面', width: 1440, height: 900, expect: 'full' },
  { name: '1366px 笔记本', width: 1366, height: 768, expect: 'full' },
  { name: '1024px 小桌面', width: 1024, height: 768, expect: 'full' },
  { name: '768px 平板', width: 768, height: 1024, expect: 'rail' },
  { name: '390px 手机', width: 390, height: 844, expect: 'drawer' },
]

const PAGES = ['/', '/conversations', '/memory', '/tasks', '/jobs', '/capabilities', '/apps', '/settings']

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'ph-layout-'))
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

  for (const vp of VIEWPORTS) {
    await call('Emulation.setDeviceMetricsOverride', {
      width: vp.width,
      height: vp.height,
      deviceScaleFactor: 1,
      mobile: vp.expect === 'drawer',
    })

    const rows = []
    for (const path of PAGES) {
      await call('Page.navigate', { url: `${ORIGIN}${path}` })
      await sleep(1300)
      rows.push(
        await evaluate(`(() => {
          const doc = document.documentElement;
          const sidebar = document.querySelector('.app__sidebar');
          const content = document.querySelector('.app__content');
          const toggle = document.querySelector('.app__menu-button');
          const label = document.querySelector('.app__nav-label');
          const rect = (el) => { if (!el) return null; const r = el.getBoundingClientRect(); return { w: Math.round(r.width), x: Math.round(r.x) }; };
          const shown = (el) => { if (!el) return false; const r = el.getBoundingClientRect(); return r.width > 0 && r.height > 0 && getComputedStyle(el).visibility !== 'hidden'; };
          return {
            overflowX: doc.scrollWidth > doc.clientWidth + 1,
            sidebarShown: shown(sidebar),
            sidebarWidth: rect(sidebar)?.w ?? 0,
            sidebarX: rect(sidebar)?.x ?? 0,
            menuShown: shown(toggle),
            contentWidth: rect(content)?.w ?? 0,
            contentPad: content ? getComputedStyle(content).paddingLeft : null,
            labelHidden: label ? getComputedStyle(label).display === 'none' : null,
            legacyNav: Boolean(document.querySelector('.app__header')),
          };
        })()`),
      )

      const row = rows[rows.length - 1]
      const label = `${vp.name} ${path}`
      if (vp.expect === 'full') {
        check(
          `${label}：完整侧栏且主区铺开`,
          row.sidebarShown && !row.menuShown && row.contentWidth > 600 && !row.legacyNav,
          `侧栏=${row.sidebarWidth}px 内容=${row.contentWidth}px 内边距=${row.contentPad}`,
        )
      } else if (vp.expect === 'rail') {
        check(
          `${label}：轨道侧栏且标签保留`,
          row.sidebarShown && !row.menuShown && row.labelHidden === false,
          `侧栏=${row.sidebarWidth}px 标签隐藏=${row.labelHidden}`,
        )
      } else {
        check(
          `${label}：抽屉（默认收起）且出现菜单按钮`,
          row.sidebarX < 0 && row.menuShown,
          `侧栏x=${row.sidebarX} 菜单=${row.menuShown}`,
        )
      }
      check(`${label}：无横向溢出`, !row.overflowX, `内容=${row.contentWidth}px`)
    }
  }

  // 规范 §7 的核心断言：1907px 下主区不再锁定在约 850px 居中窄列。
  await call('Emulation.setDeviceMetricsOverride', {
    width: 1907,
    height: 1000,
    deviceScaleFactor: 1,
    mobile: false,
  })
  await call('Page.navigate', { url: `${ORIGIN}/` })
  await sleep(1500)
  const wide = await evaluate(
    `document.querySelector('.app__content').getBoundingClientRect().width`,
  )
  check('1907px 下主内容区远超 850px（不再居中窄列）', wide > 1200, `实际 ${Math.round(wide)}px`)

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
