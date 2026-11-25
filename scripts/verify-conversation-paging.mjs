#!/usr/bin/env node
/**
 * 迭代 2 走查：长会话分页与滚动位置保持（真实浏览器 + CDP）。
 *
 * 验证：
 *   1. 长会话首屏只显示最近一页，并出现「加载更早的消息」；
 *   2. 点一次后消息变多，且**阅读位置不跳变**（滚动补偿生效）；
 *   3. 一直加载到底后显示「已经到对话的开头了」，最早一条可见；
 *   4. 全程没有重复消息、没有缺口；
 *   5. 短会话直接显示「已经到对话的开头了」，不需要加载；
 *   6. 桌面与窄屏都没有横向溢出、没有控制台错误。
 *
 * 用法：
 *   node scripts/verify-conversation-paging.mjs \
 *     --origin http://127.0.0.1:5201 \
 *     --long-id <会话 id> --short-id <会话 id>
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5201').replace(/\/$/, '')
const LONG_ID = args.get('long-id')
const SHORT_ID = args.get('short-id')
const OUT = args.get('out') ?? '/tmp/ph-v3-shots'
const PORT = Number(args.get('port') ?? 9888)
/** 长会话的预期消息总数，与服务端种子数据一致。 */
const LONG_COUNT = Number(args.get('long-count') ?? 60)

if (!LONG_ID || !SHORT_ID) {
  console.error('必须提供 --long-id 与 --short-id。')
  process.exit(2)
}

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
const profile = mkdtempSync(join(tmpdir(), 'ph-paging-'))
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
    height: 900,
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
    await sleep(2000)
  }

  const snapshot = () =>
    evaluate(`(() => {
      const items = [...document.querySelectorAll('.message__content')].map((el) => el.textContent.trim());
      const btn = document.querySelector('.earlier-messages button');
      const end = document.querySelector('.earlier-messages--end');
      return {
        count: items.length,
        items,
        hasButton: Boolean(btn),
        buttonText: btn ? btn.textContent.trim() : null,
        endText: end ? end.textContent.trim() : null,
        scrollY: window.scrollY,
        scrollHeight: document.documentElement.scrollHeight,
      };
    })()`)

  // ---- 1. 长会话首屏 ----
  await goto(`/conversations/${LONG_ID}`)
  const first = await snapshot()
  check(
    '长会话首屏只加载最近一页，并出现「加载更早的消息」',
    first.count === 50 && first.hasButton,
    `消息 ${first.count} 条，按钮=${first.buttonText}`,
  )
  await shot('long-initial')

  // ---- 2. 加载更早 + 滚动位置保持 ----
  const before = await evaluate(`(() => {
    const anchor = [...document.querySelectorAll('.message__content')][0];
    const rect = anchor.getBoundingClientRect();
    return { top: rect.top, text: anchor.textContent.trim(), scrollY: window.scrollY };
  })()`)

  await evaluate(`document.querySelector('.earlier-messages button').click()`)
  await sleep(1600)
  const second = await snapshot()

  const after = await evaluate(`(() => {
    const items = [...document.querySelectorAll('.message__content')];
    const target = items.find((el) => el.textContent.trim() === ${JSON.stringify(before.text)});
    if (!target) return null;
    return { top: target.getBoundingClientRect().top };
  })()`)

  check(
    '加载更早后消息变多',
    second.count > first.count,
    `${first.count} → ${second.count} 条`,
  )
  check(
    '阅读位置不跳变（滚动补偿生效）',
    after !== null && Math.abs(after.top - before.top) <= 4,
    after ? `原 ${Math.round(before.top)}px → 现 ${Math.round(after.top)}px` : '锚点消息丢失',
  )

  const unique = new Set(second.items)
  check(
    '前插后没有重复消息',
    unique.size === second.items.length,
    `${second.items.length} 条，去重后 ${unique.size}`,
  )
  await shot('long-after-load')

  // ---- 3. 一直加载到底 ----
  let current = second
  let guard = 0
  while (current.hasButton && guard < 20) {
    await evaluate(`document.querySelector('.earlier-messages button').click()`)
    await sleep(1200)
    current = await snapshot()
    guard += 1
  }
  check(
    '加载到底后显示「已经到对话的开头了」',
    !current.hasButton && (current.endText ?? '').includes('已经到对话的开头了'),
    current.endText ?? '没有提示',
  )
  check(
    '最早一条消息可见（第 1 条）',
    current.items.some((text) => text.includes('第 1 条消息')),
    `首条=${current.items[0]}`,
  )
  check(
    `全部 ${LONG_COUNT} 条都在，且顺序正确`,
    current.count === LONG_COUNT &&
      current.items[0]?.includes('第 1 条消息') === true &&
      current.items[current.count - 1]?.includes(`第 ${LONG_COUNT} 条消息`) === true,
    `${current.count} 条（期望 ${LONG_COUNT}）`,
  )
  await shot('long-all-loaded')

  // ---- 4. 刷新后回到"最近一页"（不重置为全部加载） ----
  await goto(`/conversations/${LONG_ID}`)
  const reloaded = await snapshot()
  check(
    '刷新后回到最近一页，按钮重新出现',
    reloaded.hasButton && reloaded.count < 60,
    `${reloaded.count} 条`,
  )

  // ---- 5. 短会话 ----
  await goto(`/conversations/${SHORT_ID}`)
  const short = await snapshot()
  check(
    '短会话直接显示开头提示，不显示加载按钮',
    !short.hasButton && (short.endText ?? '').includes('已经到对话的开头了'),
    `${short.count} 条`,
  )
  await shot('short-conversation')

  // ---- 6. 窄屏 ----
  await call('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  })
  await goto(`/conversations/${LONG_ID}`)
  const mobile = await evaluate(`(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
    hasButton: Boolean(document.querySelector('.earlier-messages button')),
  }))()`)
  check(
    '窄屏 390px 无横向溢出，加载按钮仍在',
    mobile.sw <= mobile.cw + 1 && mobile.hasButton,
    `${mobile.sw} / ${mobile.cw}`,
  )
  await shot('long-mobile')

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
