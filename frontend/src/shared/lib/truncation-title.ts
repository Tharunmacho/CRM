/**
 * Where the layout has to shorten a name, title, e-mail or file name with an ellipsis (or a line clamp), hovering or
 * focusing it reveals the full text in the browser's own tooltip. One delegated listener covers every screen, including
 * content that arrives later, so no component has to remember a `title`. Elements that already carry a `title`, and
 * single-line inputs/selects whose value does not fit, are handled the same way.
 */

const MARK = 'data-fit-title'

export interface TruncationProbe {
  textOverflow: string
  lineClamp: string
  scrollWidth: number
  clientWidth: number
  scrollHeight: number
  clientHeight: number
}

/** True when the box is clipping its text on purpose (ellipsis or line clamp) and something is actually hidden. */
export function isClipped(p: TruncationProbe): boolean {
  const ellipsis = p.textOverflow.includes('ellipsis') && p.scrollWidth > p.clientWidth + 1
  const clamped = p.lineClamp !== '' && p.lineClamp !== 'none' && p.scrollHeight > p.clientHeight + 1
  return ellipsis || clamped
}

function probe(el: HTMLElement): TruncationProbe {
  const cs = getComputedStyle(el)
  return {
    textOverflow: cs.textOverflow,
    lineClamp: (cs as CSSStyleDeclaration & { webkitLineClamp?: string }).webkitLineClamp ?? '',
    scrollWidth: el.scrollWidth,
    clientWidth: el.clientWidth,
    scrollHeight: el.scrollHeight,
    clientHeight: el.clientHeight,
  }
}

function textOf(el: HTMLElement): string {
  if (el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement) return el.value
  return (el.textContent ?? '').replace(/\s+/g, ' ').trim()
}

function apply(start: EventTarget | null) {
  let el = start instanceof HTMLElement ? start : start instanceof Node ? start.parentElement : null
  for (let depth = 0; el && depth < 5; depth += 1, el = el.parentElement) {
    if (el === document.body) return
    const owned = el.hasAttribute(MARK)
    if (el.hasAttribute('title') && !owned) {
      const lowercase = el.title.toLowerCase()
      if (el.title !== lowercase) el.title = lowercase
      return
    }
    if (el instanceof HTMLInputElement && !['text', 'email', 'tel', 'url', 'search'].includes(el.type)) continue
    const text = textOf(el)
    const clipped = el instanceof HTMLInputElement || el instanceof HTMLTextAreaElement
      ? el.scrollWidth > el.clientWidth + 1
      : isClipped(probe(el))
    if (clipped && text) {
      el.setAttribute('title', text.toLowerCase())
      el.setAttribute(MARK, '')
      return
    }
    if (owned) { el.removeAttribute('title'); el.removeAttribute(MARK) } // it fits now (wider window, shorter text)
  }
}

let installed = false

export function installTruncationTitles() {
  if (installed || typeof document === 'undefined') return
  installed = true
  const handler = (event: Event) => apply(event.target)
  document.addEventListener('mouseover', handler, { passive: true })
  document.addEventListener('focusin', handler, { passive: true })
  document.addEventListener('touchstart', handler, { passive: true })
}
