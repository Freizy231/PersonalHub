/**
 * Theme state lives on <html> so that layers rendered outside the app root
 * (dialogs are teleported to <body>) share the same colour tokens.
 * `applyTheme` runs once before the app mounts, which avoids a light flash
 * for people who use the dark theme.
 */
export type ThemeChoice = 'system' | 'light' | 'dark'

const STORAGE_KEY = 'personal-hub-theme'

export function storedTheme(): ThemeChoice {
  try {
    const value = typeof localStorage === 'undefined' ? null : localStorage.getItem(STORAGE_KEY)
    return value === 'light' || value === 'dark' ? value : 'system'
  } catch { return 'system' }
}

export function saveTheme(value: ThemeChoice) {
  try { localStorage.setItem(STORAGE_KEY, value) } catch { /* Private mode: the choice lasts for this session only. */ }
}

export function resolveDark(choice: ThemeChoice, systemPrefersDark: boolean) {
  return choice === 'dark' || (choice === 'system' && systemPrefersDark)
}

export function applyTheme(dark: boolean) {
  if (typeof document === 'undefined' || !document.documentElement?.classList) return
  document.documentElement.classList.toggle('dark', dark)
}

export function initTheme() {
  const prefers = typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-color-scheme: dark)').matches
  applyTheme(resolveDark(storedTheme(), prefers))
}
