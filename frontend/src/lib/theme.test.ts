import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  THEME_STORAGE_KEY,
  applyTheme,
  readPreference,
  resolveTheme,
  savePreference,
  watchSystemTheme,
} from './theme'

function mockMatchMedia(dark: boolean) {
  const listeners = new Set<() => void>()
  const mq = {
    matches: dark,
    addEventListener: (_: string, cb: () => void) => listeners.add(cb),
    removeEventListener: (_: string, cb: () => void) => listeners.delete(cb),
  }
  vi.stubGlobal('matchMedia', () => mq)
  return {
    flip(next: boolean) {
      mq.matches = next
      listeners.forEach((l) => l())
    },
    listeners,
  }
}

beforeEach(() => {
  localStorage.clear()
  delete document.documentElement.dataset.theme
})
afterEach(() => vi.unstubAllGlobals())

describe('theme', () => {
  it('defaults to system and ignores garbage in storage', () => {
    expect(readPreference()).toBe('system')
    localStorage.setItem(THEME_STORAGE_KEY, 'neon')
    expect(readPreference()).toBe('system')
  })

  it.each([
    ['light', true, 'light'],
    ['dark', false, 'dark'],
    ['system', true, 'dark'],
    ['system', false, 'light'],
  ] as const)('%s with OS dark=%s resolves to %s', (pref, dark, expected) => {
    expect(resolveTheme(pref, dark)).toBe(expected)
  })

  it('applies data-theme and persists the choice', () => {
    mockMatchMedia(false)
    savePreference('dark')
    expect(document.documentElement.dataset.theme).toBe('dark')
    expect(readPreference()).toBe('dark')
  })

  it('follows the OS while on "system", and stops following once a theme is chosen', () => {
    const os = mockMatchMedia(false)
    let pref: 'system' | 'light' = 'system'
    const off = watchSystemTheme(() => pref)
    applyTheme('system')
    expect(document.documentElement.dataset.theme).toBe('light')

    os.flip(true)
    expect(document.documentElement.dataset.theme).toBe('dark')

    pref = 'light'
    os.flip(false)
    os.flip(true)
    expect(document.documentElement.dataset.theme).toBe('dark') // unchanged: user chose light, no re-apply
    off()
    expect(os.listeners.size).toBe(0)
  })

  it('still works when storage is blocked', () => {
    mockMatchMedia(false)
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('blocked')
    })
    expect(() => savePreference('dark')).not.toThrow()
    expect(document.documentElement.dataset.theme).toBe('dark')
  })
})
