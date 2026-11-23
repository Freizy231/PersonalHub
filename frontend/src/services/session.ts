/**
 * 会话令牌。
 *
 * 用途：绑定"本次会话内不再询问"的授权。
 *
 * 为什么放在 sessionStorage 而不是 localStorage：
 * 这是"刷新即失效"的设计（决策 D-010）。sessionStorage 按标签页存活，
 * 刷新后保留、关闭标签页即清空——但我们需要**刷新后失效**，否则用户
 * 无意勾选一次，之后所有写操作都被静默放行。
 * 因此这里在页面加载时生成新令牌，等同"刷新即重新询问"。
 */

const TOKEN_KEY = 'ph.session-token'

function randomToken(): string {
  const bytes = new Uint8Array(16)
  crypto.getRandomValues(bytes)
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')
}

let cached: string | null = null

export function getSessionToken(): string {
  if (cached) return cached
  // 故意不使用 sessionStorage：每次页面加载都换新值，
  // 使命中"刷新页面即失效"。
  cached = randomToken()
  try {
    sessionStorage.setItem(TOKEN_KEY, cached)
  } catch {
    // 隐私模式下可能抛错；内存中的值仍可用。
  }
  return cached
}
