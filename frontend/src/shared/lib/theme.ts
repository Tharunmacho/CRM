import { useSyncExternalStore } from 'react'

/** Light/dark and the colour palette, stamped on <html> as data-theme / data-palette. */
export type Theme = 'light' | 'dark'
export type PaletteId = 'royal' | 'white' | 'graphite' | 'violet' | 'emerald'

export const PALETTES: { id: PaletteId; label: string; swatch: string }[] = [
  { id: 'royal', label: 'Royal blue', swatch: '#2f55d4' },
  { id: 'white', label: 'White', swatch: '#ffffff' },
  { id: 'graphite', label: 'Graphite', swatch: '#363636' },
  { id: 'violet', label: 'Violet', swatch: '#5b21b6' },
  { id: 'emerald', label: 'Emerald', swatch: '#15803d' },
]

const THEME_KEY = 'crm-theme'
const PALETTE_KEY = 'crm-palette'
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
function currentPalette(): PaletteId {
  const stored = read(PALETTE_KEY)
  return PALETTES.some((palette) => palette.id === stored) ? (stored as PaletteId) : 'royal'
}

function apply(theme: Theme, palette: PaletteId) {
  const root = document.documentElement
  root.dataset.theme = theme
  if (palette === 'royal') delete root.dataset.palette
  else root.dataset.palette = palette
  syncFavicon(palette)
}

/** The browser-tab icon follows the palette, like the logo mark in the sidebar. */
function syncFavicon(id: PaletteId) {
  const picked = PALETTES.find((entry) => entry.id === id)?.swatch ?? '#2f55d4'
  const swatch = picked === '#ffffff' ? '#2f55d4' : picked
  const svg = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="8" fill="${swatch}"/><g fill="none" stroke="#fff" stroke-width="2.2" stroke-linejoin="round" stroke-linecap="round"><path d="M16 7 7 11.5 16 16l9-4.5L16 7Z"/><path d="m7 16 9 4.5 9-4.5"/><path d="m7 20.5 9 4.5 9-4.5"/></g></svg>`
  const link = document.querySelector<HTMLLinkElement>('link[rel="icon"]')
  if (link) { link.type = 'image/svg+xml'; link.href = `data:image/svg+xml,${encodeURIComponent(svg)}` }
}

let theme: Theme = 'light'
let palette: PaletteId = 'royal'

/** Call once before the first render (index.html also applies it earlier to avoid a flash). */
export function initTheme() {
  theme = currentTheme()
  palette = currentPalette()
  apply(theme, palette)
}

function emit() { listeners.forEach((listener) => listener()) }
function subscribe(listener: () => void) { listeners.add(listener); return () => { listeners.delete(listener) } }

export function setTheme(next: Theme) {
  theme = next; write(THEME_KEY, next); apply(theme, palette); emit()
}
export function setPalette(next: PaletteId) {
  palette = next; write(PALETTE_KEY, next); apply(theme, palette); emit()
}

export function useTheme() {
  const t = useSyncExternalStore(subscribe, () => theme, () => 'light' as Theme)
  const p = useSyncExternalStore(subscribe, () => palette, () => 'royal' as PaletteId)
  return { theme: t, palette: p, setTheme, setPalette }
}
