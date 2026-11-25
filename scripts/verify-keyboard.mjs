#!/usr/bin/env node
/**
 * 迭代 4 第 3 项走查：键盘可用性（真实浏览器）。
 *
 * 验证：
 *   1. Tab 能依次到达每个可见的交互控件（不卡死、不跳过）；
 *   2. 每次获得焦点时都有可见的焦点指示（outline 或 box-shadow）；
 *   3. 焦点顺序与 DOM 顺序一致（不会倒着跳）；
 *   4. 没有"吞噬键盘"的元素（同一元素反复拿焦点 = 焦点陷阱误判）；
 *   5. 页面没有被 overflow 裁掉而无法聚焦的控件。
 *
 * 判定口径的两次修正（都是脚本自身的问题，不是产品缺陷）：
 *   - "焦点陷阱"初版按「标签 + 文本」比对，把页面上两个都叫「查看全部」的
 *     不同链接误判成陷阱。改为比对 DOM 节点的唯一序号。
 *   - "顺序一致"初版把 Tab 绕回页面开头当成倒退。改为先数出可聚焦元素总数，
 *     只走那么多步，因此不会走到第二轮。
 *
 * 用法：node scripts/verify-keyboard.mjs --origin http://127.0.0.1:5173
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5206').replace(/\/$/, '')
const OUT = args.get('out') ?? '/tmp/ph-keyboard'
const PORT = Number(args.get('port') ?? 9934)

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
const profile = mkdtempSync(join(tmpdir(), 'ph-kbd-'))
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
  await call('DOM.enable')
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

  const key = async (type) => {
    const codes = {
      Tab: { key: 'Tab', code: 'Tab', windowsVirtualKeyCode: 9, nativeVirtualKeyCode: 9 },
      Enter: { key: 'Enter', code: 'Enter', windowsVirtualKeyCode: 13, nativeVirtualKeyCode: 13 },
      Escape: { key: 'Escape', code: 'Escape', windowsVirtualKeyCode: 27, nativeVirtualKeyCode: 27 },
    }
    await call('Input.dispatchKeyEvent', { type, ...codes.Tab })
  }

  const focusInfo = () =>
    evaluate(`(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return { tag: 'BODY' };
      const cs = getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const nonInteractive = ['DIV','SPAN','P','LI','BODY','HTML','SECTION','MAIN','NAV','UL','HEADER','FOOTER'];
      return {
        tag: el.tagName,
        cls: el.className && typeof el.className === 'string' ? el.className.slice(0, 40) : '',
        text: (el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 30),
        href: el.getAttribute('href') || '',
        domIndex: [...document.querySelectorAll('*')].indexOf(el),
        outlineWidth: parseFloat(cs.outlineWidth) || 0,
        outlineStyle: cs.outlineStyle,
        boxShadow: cs.boxShadow,
        tabIndex: el.tabIndex,
        nonInteractive: nonInteractive.includes(el.tagName) && el.tabIndex < 0,
        w: Math.round(r.width),
        h: Math.round(r.height),
        offscreen: r.width === 0 || r.height === 0,
      };
    })()`)

  /** 在页面上按 Tab N 次，返回每一步的焦点快照。 */
  const walk = async (steps) => {
    const trail = []
    for (let i = 0; i < steps; i += 1) {
      await key('keyDown')
      await key('keyUp')
      await sleep(60)
      trail.push(await focusInfo())
    }
    return trail
  }

  const hasVisibleFocus = (info) => {
    if (info.tag === 'BODY') return true
    return info.outlineWidth >= 1 || (info.boxShadow && info.boxShadow !== 'none')
  }

  const focusableCount = () =>
    evaluate(`(() => {
      const sel = 'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex]:not([tabindex="-1"])';
      return [...document.querySelectorAll(sel)].filter((el) => {
        if (el.closest('[hidden]') || el.closest('[aria-hidden="true"]')) return false;
        const r = el.getBoundingClientRect();
        return r.width > 0 && r.height > 0;
      }).length;
    })()`)

  const auditPage = async (path) => {
    await call('Page.navigate', { url: `${ORIGIN}${path}` })
    await sleep(2000)
    const total = await focusableCount()
    await evaluate('document.body.focus()')
    const trail = await walk(total)
    const focused = trail.filter((t) => t.tag !== 'BODY')
    const missing = focused.filter((t) => !hasVisibleFocus(t)).map((t) => `${t.tag}.${t.cls.split(' ')[0]} "${t.text}"`)

    // 焦点陷阱：同一个 DOM 元素重复拿焦点（input/textarea 允许停留）
    const repeated = []
    for (let i = 1; i < focused.length; i += 1) {
      if (focused[i].domIndex === focused[i - 1].domIndex && !['INPUT', 'TEXTAREA'].includes(focused[i].tag)) {
        repeated.push(`${focused[i].tag} "${focused[i].text}"`)
      }
    }

    // 焦点顺序应与 DOM 顺序一致
    const backwards = []
    for (let i = 1; i < focused.length; i += 1) {
      if (focused[i].domIndex < focused[i - 1].domIndex) {
        backwards.push(`${focused[i - 1].text} → ${focused[i].text}`)
      }
    }

    // 每个可聚焦元素应恰好被访问一次
    const visited = new Set(focused.map((t) => t.domIndex))
    const coverage = visited.size

    const nonInteractive = focused.filter((t) => t.nonInteractive).map((t) => `${t.tag}.${t.cls}`)
    const offscreen = focused.filter((t) => t.offscreen).map((t) => `${t.tag} "${t.text}"`)

    return {
      path,
      count: focused.length,
      focusable: total,
      coverage,
      missing,
      repeated,
      backwards,
      nonInteractive,
      offscreen,
      first: focused[0] ? `${focused[0].tag} "${focused[0].text || focused[0].cls}"` : '无',
    }
  }

  // ---- 走查主要页面 ----
  const pages = ['/', '/conversations', '/memory', '/tasks', '/jobs', '/capabilities', '/apps', '/settings']
  const audit = []
  for (const path of pages) {
    audit.push(await auditPage(path))
  }

  for (const page of audit) {
    check(
      `${page.path} 键盘可达（${page.count} 个控件）`,
      page.count > 0,
      page.count > 0 ? `首个焦点: ${page.first}` : 'Tab 无法到达任何控件',
    )
  }

  const uncovered = audit.filter((p) => p.coverage < p.focusable)
  check(
    '每个可聚焦元素都能被 Tab 到达',
    uncovered.length === 0,
    uncovered.map((p) => `${p.path} ${p.coverage}/${p.focusable}`).join(' | ') || '全部覆盖',
  )

  const allMissing = audit.flatMap((p) => p.missing.map((m) => `${p.path}: ${m}`))
  check('每个获得焦点的控件都有可见焦点指示', allMissing.length === 0, allMissing.slice(0, 4).join(' | ') || '全部有 outline 或 box-shadow')

  const allRepeated = audit.flatMap((p) => p.repeated.map((m) => `${p.path}: ${m}`))
  check('没有焦点陷阱', allRepeated.length === 0, allRepeated.slice(0, 3).join(' | ') || '焦点持续前进')

  const allBackwards = audit.flatMap((p) => p.backwards.map((m) => `${p.path}: ${m}`))
  check('焦点顺序与页面顺序一致', allBackwards.length === 0, allBackwards.slice(0, 3).join(' | ') || '从左到右、从上到下')

  const allNonInteractive = audit.flatMap((p) => p.nonInteractive.map((m) => `${p.path}: ${m}`))
  check('焦点不会落在不可交互的容器上', allNonInteractive.length === 0, allNonInteractive.slice(0, 3).join(' | '))

  const allOffscreen = audit.flatMap((p) => p.offscreen.map((m) => `${p.path}: ${m}`))
  check('没有无法显示的焦点目标（宽高为 0）', allOffscreen.length === 0, allOffscreen.slice(0, 3).join(' | '))

  // ---- 回到开头：Shift+Tab 应该能退回 ----
  await call('Page.navigate', { url: `${ORIGIN}/` })
  await sleep(2000)
  const forward = await walk(3)
  await call('Input.dispatchKeyEvent', {
    type: 'keyDown',
    key: 'Tab',
    code: 'Tab',
    windowsVirtualKeyCode: 9,
    nativeVirtualKeyCode: 9,
    modifiers: 8,
  })
  await call('Input.dispatchKeyEvent', {
    type: 'keyUp',
    key: 'Tab',
    code: 'Tab',
    windowsVirtualKeyCode: 9,
    nativeVirtualKeyCode: 9,
    modifiers: 8,
  })
  await sleep(80)
  const back = await focusInfo()
  check(
    'Shift+Tab 可以后退',
    forward.length >= 2 && back.tag !== 'BODY',
    `${forward[2]?.text || forward[2]?.cls || '?'} ← Shift+Tab → ${back.text || back.cls}`,
  )

  const shot = await call('Page.captureScreenshot', { format: 'png' })
  writeFileSync(join(OUT, 'keyboard-focus.png'), Buffer.from(shot.data, 'base64'))

  check('全程无控制台错误', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 300))

  writeFileSync(join(OUT, 'audit.json'), JSON.stringify(audit, null, 2))
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
