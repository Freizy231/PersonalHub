#!/usr/bin/env node
/**
 * 真实点击走查：用户任务闭环（迭代 1 验收场景）。
 *
 * 在真实浏览器里完成：
 *   1. /tasks 点「标记完成」→ 状态变已完成
 *   2. 刷新页面 → 仍是已完成（服务端为准）
 *   3. 点「重新打开」→ 回到未完成
 *   4. 点「删除」→ 只进入确认态（未调用接口）→ 点确认 → 条目消失
 *   5. /tasks/done 筛选视图只显示已完成
 *
 * 前置：后端与前端已启动，且库里有种子数据。
 * 用法：node scripts/verify-task-closure.mjs --origin http://127.0.0.1:5199
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5199').replace(/\/$/, '')
const PORT = Number(args.get('port') ?? 9555)
const TITLE = args.get('title') ?? '给番茄钟应用加一个休息提醒'

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

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
const profile = mkdtempSync(join(tmpdir(), 'ph-task-'))
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

let socket
const results = []
function check(name, ok, detail = '') {
  results.push({ name, ok, detail })
  console.log(`${ok ? '✓' : '✗'} ${name}${detail ? ` — ${detail}` : ''}`)
}

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

  const goto = async (path) => {
    await call('Page.navigate', { url: `${ORIGIN}${path}` })
    await sleep(1800)
  }

  /** 找到包含给定标题的任务条目，读取其状态文本。 */
  const statusOf = (title) =>
    evaluate(`(() => {
      const li = [...document.querySelectorAll('.user-task')]
        .find((el) => el.textContent.includes(${JSON.stringify(title)}));
      if (!li) return null;
      const badge = li.querySelector('.user-task__status');
      return badge ? badge.textContent.trim() : null;
    })()`)

  const clickIn = (title, label) =>
    evaluate(`(() => {
      const li = [...document.querySelectorAll('.user-task')]
        .find((el) => el.textContent.includes(${JSON.stringify(title)}));
      if (!li) return 'no-item';
      const btn = [...li.querySelectorAll('button')]
        .find((b) => b.textContent.trim() === ${JSON.stringify(label)});
      if (!btn) return 'no-button';
      btn.click();
      return 'clicked';
    })()`)

  // ---- 1. 标记完成 ----
  await goto('/tasks')
  check('任务页初始状态为未完成', (await statusOf(TITLE)) === '未完成', `当前：${await statusOf(TITLE)}`)

  check('点击「标记完成」', (await clickIn(TITLE, '标记完成')) === 'clicked')
  await sleep(1200)
  check('状态变为已完成', (await statusOf(TITLE)) === '已完成', `当前：${await statusOf(TITLE)}`)

  // ---- 2. 刷新后仍保持 ----
  await goto('/tasks')
  check('刷新后仍为已完成（服务端为准）', (await statusOf(TITLE)) === '已完成', `当前：${await statusOf(TITLE)}`)

  // ---- 3. 重新打开 ----
  check('点击「重新打开」', (await clickIn(TITLE, '重新打开')) === 'clicked')
  await sleep(1200)
  check('状态回到未完成', (await statusOf(TITLE)) === '未完成', `当前：${await statusOf(TITLE)}`)

  // ---- 4. 两段式删除 ----
  check('点击「删除」进入确认态', (await clickIn(TITLE, '删除')) === 'clicked')
  await sleep(400)
  const confirmVisible = await evaluate(`(() => {
    const li = [...document.querySelectorAll('.user-task')]
      .find((el) => el.textContent.includes(${JSON.stringify(TITLE)}));
    return Boolean(li && li.querySelector('.user-task__confirm'));
  })()`)
  check('确认态出现，且说明后果', confirmVisible)
  const stillThere = (await statusOf(TITLE)) !== null
  check('确认前条目仍在（未直接删除）', stillThere)

  const confirmed = await evaluate(`(() => {
    const li = [...document.querySelectorAll('.user-task')]
      .find((el) => el.textContent.includes(${JSON.stringify(TITLE)}));
    if (!li) return 'no-item';
    const btn = [...li.querySelectorAll('.user-task__confirm button')]
      .find((b) => b.textContent.trim() === '删除');
    if (!btn) return 'no-button';
    btn.click();
    return 'clicked';
  })()`)
  check('点击确认删除', confirmed === 'clicked', confirmed)
  await sleep(1400)
  check('条目已消失', (await statusOf(TITLE)) === null)

  // ---- 5. 筛选视图 ----
  await goto('/tasks/done')
  const doneOnly = await evaluate(`(() => {
    const items = [...document.querySelectorAll('.user-task')];
    return {
      count: items.length,
      allDone: items.every((el) => el.querySelector('.user-task__status')?.textContent.trim() === '已完成'),
    };
  })()`)
  check('已完成筛选只显示已完成任务', doneOnly.allDone, `共 ${doneOnly.count} 条`)

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
