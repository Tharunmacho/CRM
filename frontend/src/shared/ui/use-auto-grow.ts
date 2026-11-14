import { useEffect, useLayoutEffect, type RefObject } from 'react'
import { clampRows, growHeight } from '@/shared/lib/auto-grow'

export interface AutoGrowOptions {
  /** Never shorter than this many lines. Default 1. */
  minRows?: number
  /** Taller than this and the box scrolls inside instead of growing. Default 6; 0 means unlimited. */
  maxRows?: number
  /** Turn the behaviour off without breaking the rules of hooks. Default true. */
  enabled?: boolean
}

/* useLayoutEffect warns when React renders on the server; the DOM is only touched in the browser anyway. */
const useIsoLayoutEffect = typeof window === 'undefined' ? useEffect : useLayoutEffect

const px = (value: string) => { const n = parseFloat(value); return Number.isFinite(n) ? n : 0 }

/** Scrollable ancestors keep their position while the box is briefly collapsed to be measured. */
function scrollSnapshot(el: HTMLElement) {
  const saved: Array<[Element, number]> = []
  for (let node: HTMLElement | null = el.parentElement; node; node = node.parentElement) {
    if (node.scrollTop > 0) saved.push([node, node.scrollTop])
  }
  const root = document.scrollingElement
  if (root && root.scrollTop > 0 && !saved.some(([node]) => node === root)) saved.push([root, root.scrollTop])
  return () => { for (const [node, top] of saved) if (node.scrollTop !== top) node.scrollTop = top }
}

/** Fits `el` to its content now. Returns without touching the DOM when the size is already right. */
export function fitTextarea(el: HTMLTextAreaElement, minRows: number, maxRows: number) {
  if (typeof window === 'undefined' || !el.isConnected) return
  if (el.clientWidth === 0 && el.offsetParent === null) return // hidden (display: none): nothing to measure yet
  const style = window.getComputedStyle(el)
  const fontSize = px(style.fontSize) || 14
  const lineHeight = px(style.lineHeight) || fontSize * 1.3
  const borderBox = style.boxSizing === 'border-box'
  const restore = scrollSnapshot(el)
  el.style.height = 'auto'
  const result = growHeight({
    scrollHeight: el.scrollHeight,
    lineHeight,
    paddingY: px(style.paddingTop) + px(style.paddingBottom),
    borderY: px(style.borderTopWidth) + px(style.borderBottomWidth),
    borderBox,
    minRows,
    maxRows,
  })
  const next = `${result.height}px`
  el.style.height = next
  restore()
  const overflow = result.scrolls ? 'auto' : 'hidden'
  if (el.style.overflowY !== overflow) el.style.overflowY = overflow
  if (el.style.resize !== 'none') el.style.resize = 'none'
}

/**
 * Keeps a textarea exactly as tall as its text (between `minRows` and `maxRows`). Re-fits on every value change, when
 * the box changes width (container resize, sidebar toggle, rotate) and when web fonts finish loading. Only ever writes
 * `style.height` when it changed, so an idle box never shifts the layout, and it never moves focus or the caret.
 */
export function useAutoGrow(
  ref: RefObject<HTMLTextAreaElement | null>,
  value: string | number | readonly string[] | undefined,
  { minRows = 1, maxRows = 6, enabled = true }: AutoGrowOptions = {},
) {
  const min = clampRows(minRows, 1)
  const max = maxRows > 0 ? Math.max(min, clampRows(maxRows, min)) : 0

  useIsoLayoutEffect(() => {
    const el = ref.current
    if (enabled && el) fitTextarea(el, min, max)
  }, [ref, value, enabled, min, max])

  useEffect(() => {
    const el = ref.current
    if (!enabled || !el || typeof window === 'undefined') return undefined
    const refit = () => fitTextarea(el, min, max)
    let width = el.clientWidth
    let frame = 0
    const onSize = () => {
      if (el.clientWidth === width) return // our own height writes also fire the observer: only a new width matters
      width = el.clientWidth
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(refit)
    }
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(onSize)
    observer?.observe(el)
    window.addEventListener('resize', onSize)
    el.addEventListener('input', refit) // uncontrolled usage
    void document.fonts?.ready.then(refit)
    return () => {
      cancelAnimationFrame(frame)
      observer?.disconnect()
      window.removeEventListener('resize', onSize)
      el.removeEventListener('input', refit)
    }
  }, [ref, enabled, min, max])
}
