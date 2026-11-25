#!/usr/bin/env node
/**
 * 迭代 3 走查：应用创建 → 预览 → 安装 的真实浏览器链路。
 *
 * 前置：用 `scripts/verify_app_flow.py` 起的**脚本化 Provider** 后端
 * （零外发）+ 指向它的前端。
 *
 * 验证：
 *   1. 初始状态：五步指示都在，第一步未开始；
 *   2. 输入描述 → 步骤推进到"后台生成"；
 *   3. 生成完成后进入预览：显示版本预测、并出现「最近的生成」可找回；
 *   4. 预览 iframe 的 sandbox 只有 allow-scripts；
 *   5. 安装后列表出现该应用，版本正确；
 *   6. 再次生成 → 预览显示"当前 1.0.0 → 安装为 1.0.1"；
 *   7. 桌面与 390px 无横向溢出、无控制台错误。
 *
 * 用法：
 *   node scripts/verify-app-flow.mjs --origin http://127.0.0.1:5204
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

const ORIGIN = (args.get('origin') ?? 'http://127.0.0.1:5204').replace(/\/$/, '')
const OUT = args.get('out') ?? '/tmp/ph-appflow-shots'
const PORT = Number(args.get('port') ?? 9911)

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
const profile = mkdtempSync(join(tmpdir(), 'ph-appflow-'))
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

  const gotoApps = async () => {
    await call('Page.navigate', { url: `${ORIGIN}/apps` })
    await sleep(2200)
  }

  const snapshot = () =>
    evaluate(`(() => {
      const steps = [...document.querySelectorAll('.flow-step')].map((el) => ({
        text: el.textContent.trim(),
        state: [...el.classList].find((c) => c.startsWith('flow-step--')),
        current: el.getAttribute('aria-current'),
      }));
      const preview = document.querySelector('.app-preview');
      const facts = preview
        ? [...preview.querySelectorAll('.app-preview__facts div')].map((d) => d.textContent.trim())
        : [];
      const installBtn = preview
        ? [...preview.querySelectorAll('button')].map((b) => b.textContent.trim())
        : [];
      const frame = preview ? preview.querySelector('iframe') : null;
      const pending = [...document.querySelectorAll('.pending-staging__item')].map((el) =>
        el.textContent.trim(),
      );
      const installed = [...document.querySelectorAll('.app-item__name')].map((el) =>
        el.textContent.trim(),
      );
      return {
        steps,
        headline: preview ? preview.querySelector('.app-preview__title')?.textContent.trim() : null,
        facts,
        installBtn,
        sandbox: frame ? frame.getAttribute('sandbox') : null,
        pending,
        installed,
        error: document.querySelector('.alert--error')?.textContent.trim() ?? null,
      };
    })()`)

  // ---- 1. 初始状态 ----
  await gotoApps()
  const initial = await snapshot()
  check('五步指示都在', initial.steps.length === 5, `${initial.steps.length} 步`)
  check(
    '初始时第一步未开始、没有进行中的步骤',
    initial.steps.every((step) => step.state !== 'flow-step--active'),
    initial.steps.map((step) => step.state).join(','),
  )

  // ---- 2. 输入描述 → 步骤推进 ----
  const typed = await evaluate(`(() => {
    const box = document.querySelector('#app-prompt');
    if (!box) return 'no-input';
    box.focus();
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(box, '做一个喝水提醒清单');
    box.dispatchEvent(new Event('input', { bubbles: true }));
    return 'typed';
  })()`)
  check('描述输入框可用', typed === 'typed', typed)
  await sleep(300)
  const afterTyping = await snapshot()
  check(
    '输入后第一步变为进行中',
    afterTyping.steps[0]?.state === 'flow-step--active',
    afterTyping.steps[0]?.text,
  )

  // 提交
  await evaluate(`(() => {
    const form = document.querySelector('.app-builder');
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  })()`)
  await sleep(400)
  const during = await snapshot()
  // 脚本化 Provider 很快，400ms 后可能已经生成完并进入预览。
  // 关键断言是"第 1 步已完成"，而不是要求此刻仍停在第 2 步。
  check(
    '提交后第 1 步标记完成，流程向前推进',
    during.steps[0]?.state === 'flow-step--done' &&
      during.steps.some(
        (step) => step.state === 'flow-step--active' && !step.text.includes('描述需求'),
      ),
    during.steps.map((step) => `${step.text}${step.current ? '*' : ''}`).join(' | '),
  )

  // ---- 3. 等待生成完成 ----
  let finished = null
  for (let i = 0; i < 60; i += 1) {
    await sleep(500)
    const snap = await snapshot()
    if (snap.headline) {
      finished = snap
      break
    }
    if (snap.error) {
      finished = snap
      break
    }
  }
  check('生成完成后进入预览', Boolean(finished?.headline), finished?.error ?? finished?.headline ?? '超时')
  await shot('flow-preview')

  if (finished?.headline) {
    check(
      '预览说明是"第一次安装"',
      finished.headline.includes('第一次安装'),
      finished.headline,
    )
    check(
      '安装前显示版本预测',
      finished.facts.some((fact) => fact.includes('尚未安装')) &&
        finished.facts.some((fact) => fact.includes('1.0.0')),
      finished.facts.join(' / '),
    )
    check(
      '安装按钮写明目标版本',
      finished.installBtn.some((text) => text.includes('安装 1.0.0')),
      finished.installBtn.join(' / '),
    )
    check(
      '预览 iframe 的 sandbox 只有 allow-scripts',
      finished.sandbox === 'allow-scripts',
      String(finished.sandbox),
    )
    check(
      '预览阶段步骤推进到"预览结果"',
      finished.steps.some(
        (step) => step.current === 'step' && step.text.includes('预览结果'),
      ),
      finished.steps.map((step) => `${step.text}${step.current ? '*' : ''}`).join(' | '),
    )
  }

  // ---- 4. 安装 ----
  await evaluate(`(() => {
    const btn = [...document.querySelectorAll('.app-preview__actions button')]
      .find((b) => b.textContent.includes('安装'));
    btn?.click();
  })()`)
  await sleep(2500)
  const afterInstall = await snapshot()
  check(
    '安装后应用出现在已安装列表',
    afterInstall.installed.includes('喝水提醒清单'),
    afterInstall.installed.join(', '),
  )
  check('安装后预览关闭', afterInstall.headline === null)
  await shot('flow-installed')

  // ---- 5. 再次生成：版本预测 ----
  await gotoApps()
  await evaluate(`(() => {
    const box = document.querySelector('#app-prompt');
    const setter = Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value').set;
    setter.call(box, '再做一个喝水提醒清单');
    box.dispatchEvent(new Event('input', { bubbles: true }));
    const form = document.querySelector('.app-builder');
    form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
  })()`)

  // 「优先复用」可能先弹相似提示：那就点"仍然重新生成"。
  await sleep(600)
  await evaluate(`(() => {
    const btn = [...document.querySelectorAll('.app-reuse button')]
      .find((b) => b.textContent.includes('仍然重新生成'));
    btn?.click();
  })()`)

  let second = null
  for (let i = 0; i < 60; i += 1) {
    await sleep(500)
    const snap = await snapshot()
    if (snap.headline) {
      second = snap
      break
    }
    if (snap.error) {
      second = snap
      break
    }
  }
  check('第二次生成也进入预览', Boolean(second?.headline), second?.error ?? second?.headline ?? '超时')
  if (second?.headline) {
    check(
      '升级预览显示当前版本与目标版本',
      second.facts.some((fact) => fact.includes('1.0.0')) &&
        second.facts.some((fact) => fact.includes('1.0.1')),
      second.facts.join(' / '),
    )
    check(
      '安装按钮写明"替换当前版本"',
      second.installBtn.some((text) => text.includes('替换')),
      second.installBtn.join(' / '),
    )
  }
  await shot('flow-upgrade')

  // ---- 6. 「最近的生成」可找回 ----
  await gotoApps()
  const reopened = await snapshot()
  check(
    '刷新后「最近的生成」仍列出这条结果',
    reopened.pending.some((text) => text.includes('喝水提醒清单')),
    reopened.pending.join(' | ') || '列表为空',
  )

  // ---- 7. 窄屏 ----
  await call('Emulation.setDeviceMetricsOverride', {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  })
  await gotoApps()
  const mobile = await evaluate(`(() => ({
    sw: document.documentElement.scrollWidth,
    cw: document.documentElement.clientWidth,
    steps: document.querySelectorAll('.flow-step').length,
  }))()`)
  check(
    '窄屏 390px 无横向溢出，步骤条仍在',
    mobile.sw <= mobile.cw + 1 && mobile.steps === 5,
    `${mobile.sw} / ${mobile.cw}`,
  )
  await shot('flow-mobile')

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
