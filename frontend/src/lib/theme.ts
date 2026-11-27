/** Light / Dark / System theme (design system §1.4): applied via `data-theme` on <html>. */
export type ThemePreference = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

export const THEME_STORAGE_KEY = 'nexus-theme'

const isPreference = (v: unknown): v is ThemePreference =>
  v === 'light' || v === 'dark' || v === 'system'

export function readPreference(storage: Pick<Storage, 'getItem'> = localStorage): ThemePreference {
  try {
    const stored = storage.getItem(THEME_STORAGE_KEY)
    return isPreference(stored) ? stored : 'system'
  } catch {
    return 'system' // storage blocked (private mode, etc.)
  }
}

export function resolveTheme(pref: ThemePreference, prefersDark: boolean): ResolvedTheme {
  return pref === 'system' ? (prefersDark ? 'dark' : 'light') : pref
}

const prefersDark = () => globalThis.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false

export function applyTheme(pref: ThemePreference): ResolvedTheme {
  const resolved = resolveTheme(pref, prefersDark())
  document.documentElement.dataset.theme = resolved
  return resolved
}

export function savePreference(pref: ThemePreference): void {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, pref)
  } catch {
    // The choice still applies for this session.
  }
  applyTheme(pref)
}

/** Re-apply when the OS theme flips and the user chose "System". Returns an unsubscribe. */
export function watchSystemTheme(getPreference: () => ThemePreference): () => void {
  const mq = globalThis.matchMedia?.('(prefers-color-scheme: dark)')
  if (!mq) return () => undefined
  const onChange = () => {
    if (getPreference() === 'system') applyTheme('system')
  }
  mq.addEventListener('change', onChange)
  return () => mq.removeEventListener('change', onChange)
}
