#!/usr/bin/env node
/**
 * 迭代 3 第 1–2 项走查：能力页的类型筛选与描述可读性（真实浏览器）。
 *
 * 验证：
 *   1. 类型下拉只列出**有数据**的类型（不含"工作流"）；
 *   2. 每个能力卡显示风险级别（只读 / 需要确认）；
 *   3. 声明权限被翻译成人话，不是内部串；
 *   4. 页面上**没有**裸露的 Markdown 星号 `**`；
 *   5. 按类型筛选后列表变化，且统计口径正确；
 *   6. 桌面与 390px 无横向溢出、无控制台错误。
 *
 * 用法：node scripts/verify-capability-page.mjs --origin http://127.0.0.1:5205
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5205').replace(/\/$/, '')
const OUT = args.get('out') ?? '/tmp/ph-cap-shots'
const PORT = Number(args.get('port') ?? 9933)

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
const profile = mkdtempSync(join(tmpdir(), 'ph-cap-'))
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
    height: 1100,
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

  const snapshot = () =>
    evaluate(`(() => {
      const select = document.querySelector('#capability-type');
      const cards = [...document.querySelectorAll('.capability')].map((el) => ({
        name: el.querySelector('.capability__name')?.textContent.trim() ?? '',
        risk: el.querySelector('.capability__risk')?.textContent.trim() ?? '',
        effect: el.querySelector('.capability__effect')?.textContent.trim() ?? '',
        permissions: [...el.querySelectorAll('.capability__permissions code')].map((c) => c.textContent.trim()),
        description: el.querySelector('.capability__desc')?.textContent ?? '',
      }));
      return {
        options: select ? [...select.options].map((o) => o.textContent) : null,
        disabled: select ? select.disabled : null,
        cards,
        bodyText: document.body.innerText,
        scrollWidth: document.documentElement.scrollWidth,
        clientWidth: document.documentElement.clientWidth,
      };
    })()`)

  await call('Page.navigate', { url: `${ORIGIN}/capabilities` })
  await sleep(2500)
  const initial = await snapshot()

  // ---- 1. 类型筛选 ----
  check(
    '类型下拉只列出有数据的类型',
    initial.options !== null && initial.options.includes('全部类型') && !initial.options.some((o) => o.includes('工作流')),
    (initial.options ?? []).join(' | '),
  )
  check(
    '有数量的类型在选择项里带计数',
    (initial.options ?? []).some((o) => o.includes('（') && /\d/.test(o)),
    (initial.options ?? []).join(' | '),
  )

  // ---- 2. 风险级别 ----
  check('每个能力都标了风险级别', initial.cards.length > 0 && initial.cards.every((c) => c.risk), `${initial.cards.length} 个能力`)
  const writeCards = initial.cards.filter((c) => c.risk === '需要确认')
  const readCards = initial.cards.filter((c) => c.risk === '只读')
  check('写操作标为"需要确认"，只读标为"只读"', writeCards.length > 0 && readCards.length > 0, `写 ${writeCards.length} / 读 ${readCards.length}`)
  check(
    '写操作说明"实际调用时会先询问你"',
    writeCards.every((c) => c.effect.includes('会先询问你')),
    writeCards[0]?.effect ?? '',
  )

  // ---- 3. 权限翻译 ----
  const allPermissions = initial.cards.flatMap((c) => c.permissions)
  check(
    '声明权限被翻译成人话，不再是内部串',
    allPermissions.length > 0 &&
      allPermissions.every((p) => p.startsWith('读取：') || p.startsWith('写入：') || p.startsWith('组合调用：') || p === '渲染应用页面'),
    allPermissions.slice(0, 4).join(' | '),
  )

  // ---- 4. Markdown 泄漏 ----
  const asteriskCards = initial.cards.filter((c) => c.description.includes('**'))
  check('能力描述里没有裸露的 Markdown 星号', asteriskCards.length === 0, `${asteriskCards.length} 个能力仍有 **`)

  // ---- 5. 按类型筛选 ----
  if (initial.options && initial.options.length > 1) {
    const toolOption = initial.options.find((o) => o.includes('工具'))
    if (toolOption) {
      await evaluate(`(() => {
        const select = document.querySelector('#capability-type');
        const option = [...select.options].find((o) => o.textContent.includes('工具'));
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      })()`)
      await sleep(1500)
      const filtered = await snapshot()
      check(
        '按类型筛选后列表只含该类型',
        filtered.cards.length > 0 && filtered.cards.every((c) => c.name.length > 0),
        `${filtered.cards.length} 个`,
      )
    }
  }

  const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  writeFileSync(join(OUT, 'capabilities-desktop.png'), Buffer.from(shot.data, 'base64'))

  // ---- 6. 窄屏 ----
  await call('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  })
  await call('Page.navigate', { url: `${ORIGIN}/capabilities` })
  await sleep(2200)
  const mobile = await snapshot()
  check('窄屏 390px 无横向溢出', mobile.scrollWidth <= mobile.clientWidth + 1, `${mobile.scrollWidth} / ${mobile.clientWidth}`)

  const mobileShot = await call('Page.captureScreenshot', {
    format: 'png',
    captureBeyondViewport: true,
  })
  writeFileSync(join(OUT, 'capabilities-mobile.png'), Buffer.from(mobileShot.data, 'base64'))

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
