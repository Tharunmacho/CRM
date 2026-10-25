import { useSyncExternalStore } from 'react'

/**
 * Light (default) and dark. The product has no colour palettes: <html data-palette="white"> is always set, whatever an
 * older visit stored under `crm-palette`, so the white-panel tokens (logo blue accent) are the only ones in use.
 */
export type Theme = 'light' | 'dark'

const THEME_KEY = 'crm-theme'
const listeners = new Set<() => void>()

function read(key: string): string | null {
  try { return window.localStorage.getItem(key) } catch { return null }
}
function write(key: string, value: string) {
  try { window.localStorage.setItem(key, value) } catch { /* private mode: the choice lasts for this tab only */ }
}

function currentTheme(): Theme {
  const stored = read(THEME_KEY)
  if (stored === 'light' || stored === 'dark') return stored
  return typeof window !== 'undefined' && window.matchMedia?.('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function apply(theme: Theme) {
  const root = document.documentElement
  root.dataset.theme = theme
  root.dataset.palette = 'white'
}

let theme: Theme = 'light'

/** Call once before the first render (index.html also applies it earlier to avoid a flash). */
export function initTheme() {
  theme = currentTheme()
  apply(theme)
}

function emit() { listeners.forEach((listener) => listener()) }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener) } }

export function setTheme(next: Theme) {
  theme = next; write(THEME_KEY, next); apply(theme); emit()
}

export function useTheme() {
  const t = useSyncExternalStore(subscribe, () => theme, () => 'light' as Theme)
  return { theme: t, setTheme }
}
