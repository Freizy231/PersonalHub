#!/usr/bin/env node
/**
 * 迭代 2 走查：对话在**真实失败**下的状态呈现（真实浏览器 + CDP）。
 *
 * 用不可达的供应商地址（127.0.0.1:9）制造真实失败，不产生任何外发。
 * 验证两条分流：
 *   - 供应商不可达（服务端 retryable=true）→ 出现「重试」按钮；
 *   - 未配置密钥（服务端 retryable=false）→ **没有**重试按钮，
 *     而是提示去检查配置。
 *
 * 用法：
 *   node scripts/verify-send-state.mjs \
 *     --origin http://127.0.0.1:5202 --conversation-id <id>
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5202').replace(/\/$/, '')
const CONVERSATION_ID = args.get('conversation-id')
const EXPECT = args.get('expect') ?? 'retryable' // retryable | not-configured
const OUT = args.get('out') ?? '/tmp/ph-v3-shots'
const PORT = Number(args.get('port') ?? 9899)

if (!CONVERSATION_ID) {
  console.error('必须提供 --conversation-id。')
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
const profile = mkdtempSync(join(tmpdir(), 'ph-send-'))
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

  await call('Page.navigate', { url: `${ORIGIN}/conversations/${CONVERSATION_ID}` })
  await sleep(2000)

  // 在输入框里打字并回车（真实交互，不是调用 hook）。
  const typed = await evaluate(`(() => {
    const box = document.querySelector('#composer-input');
    if (!box) return 'no-composer';
    box.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(box, '这是一条真实发送的测试消息');
    box.dispatchEvent(new Event('input', { bubbles: true }));
    return 'typed';
  })()`)
  check('输入框可用并已填入内容', typed === 'typed', typed)

  await evaluate(`(() => {
    const box = document.querySelector('#composer-input');
    box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  })()`)

  // 等失败状态出现（真实网络请求 + 服务端响应）。
  let bar = null
  for (let i = 0; i < 25; i += 1) {
    await sleep(400)
    bar = await evaluate(`(() => {
      const el = document.querySelector('.send-status--error');
      if (!el) return null;
      return {
        text: el.textContent,
        buttons: [...el.querySelectorAll('button')].map((b) => b.textContent.trim()),
        code: el.querySelector('.send-status__code')?.textContent ?? '',
      };
    })()`)
    if (bar) break
  }

  check('失败状态条出现（不是只有行为异常）', bar !== null, bar ? bar.text.slice(0, 80) : '未出现')

  if (bar) {
    if (EXPECT === 'retryable') {
      check(
        '可重试错误给出「重试」入口',
        bar.buttons.includes('重试'),
        `按钮=${JSON.stringify(bar.buttons)}`,
      )
      check(
        '说明失败原因',
        bar.text.includes('模型服务') || bar.text.includes('网络') || bar.text.includes('无法连接'),
        bar.text.slice(0, 60),
      )
    } else {
      check(
        '不可重试错误**不**给重试按钮',
        !bar.buttons.includes('重试'),
        `按钮=${JSON.stringify(bar.buttons)}`,
      )
      check(
        '引导用户去检查配置',
        bar.text.includes('检查配置') || bar.text.includes('设置'),
        bar.text.slice(0, 80),
      )
    }
    check('显示错误码，便于报障', bar.code.includes('错误码'), bar.code)
  }

  // 用户消息必须已经入库（发送后立刻清空、原文不丢）。
  const messages = await evaluate(`(() =>
    [...document.querySelectorAll('.message__content')].map((el) => el.textContent.trim())
  )()`)
  // 原文该落在哪儿，取决于服务端**有没有收下这条消息**：
  //   - 可重试（供应商不可达）：后端先落库再调用模型，消息已入库，
  //     因此出现在列表里是正确行为；
  //   - 不可重试（未配置密钥）：后端在落库之前就拒绝，消息根本没入库，
  //     原文必须回到输入框。若它既不在列表、也不在输入框，就是真的丢了。
  if (EXPECT === 'retryable') {
    check(
      '测试消息已在消息列表里（发送没有丢）',
      messages.some((text) => text.includes('真实发送的测试消息')),
      `共 ${messages.length} 条消息`,
    )
  } else {
    const draft = await evaluate(`document.querySelector('#composer-input')?.value ?? ''`)
    check(
      '服务端未收下时，原文退回输入框（没有凭空消失）',
      draft.includes('真实发送的测试消息'),
      draft ? `输入框=${draft.slice(0, 30)}` : '输入框为空',
    )
    check(
      '未落库的消息不会出现在列表里（不会假装发出去了）',
      !messages.some((text) => text.includes('真实发送的测试消息')),
      `共 ${messages.length} 条消息`,
    )
  }

  const shot = await call('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true })
  writeFileSync(
    join(OUT, `send-${EXPECT}.png`),
    Buffer.from(shot.data, 'base64'),
  )

  check('全程无未捕获异常', pageErrors.length === 0, pageErrors.join(' | ').slice(0, 300))
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
