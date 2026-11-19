import { useCallback, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { cn } from '@/shared/lib/cn'
import { FORM_TABS, nextTab, paneIdFor, tabAccessibleName, type FormTab } from '../form-tabs'

export interface TabBadge { count: number; kind: 'missing' | 'notes' | 'overdue' }

/**
 * The sticky section navigator: one tab per section of the page. One click shows that section in the same page (the panels
 * stay mounted, so nothing typed is lost). It is a single tab stop; the arrow keys, Home and End move between tabs.
 * `alsoShown` are tabs whose section is on screen next to the chosen one (a wide screen shows Personal with Passport).
 * On a phone the bar scrolls inside itself (it snaps tab by tab) and a soft fade at an edge says there is more that way.
 */
export function FormTabs({ tabs, active, alsoShown, badges, onSelect }: {
  tabs: FormTab[]
  active: FormTab
  alsoShown: FormTab[]
  badges: Partial<Record<FormTab, TabBadge>>
  onSelect: (tab: FormTab) => void
}) {
  const list = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ start: false, end: false })
  const measure = useCallback(() => {
    const bar = list.current
    if (!bar) return
    const start = bar.scrollLeft > 4
    const end = bar.scrollLeft + bar.clientWidth < bar.scrollWidth - 4
    setEdges((now) => (now.start === start && now.end === end ? now : { start, end }))
  }, [])
  useEffect(() => {
    const bar = list.current
    if (!bar) return
    measure()
    bar.addEventListener('scroll', measure, { passive: true })
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(bar)
    return () => { bar.removeEventListener('scroll', measure); observer?.disconnect() }
  }, [measure, tabs.length])
  // The chosen tab is always inside the visible part of the bar (only the bar scrolls, never the page).
  useEffect(() => {
    const bar = list.current
    const tab = bar?.querySelector<HTMLElement>(`#af-tab-${active}`)
    if (!bar || !tab || bar.scrollWidth <= bar.clientWidth) return
    const left = tab.offsetLeft - 16
    const right = tab.offsetLeft + tab.offsetWidth + 16
    if (left < bar.scrollLeft) bar.scrollTo({ left: Math.max(0, left), behavior: 'auto' })
    else if (right > bar.scrollLeft + bar.clientWidth) bar.scrollTo({ left: right - bar.clientWidth, behavior: 'auto' })
  }, [active])
  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const target = nextTab(tabs, active, event.key)
    if (!target) return
    event.preventDefault()
    onSelect(target)
    list.current?.querySelector<HTMLElement>(`#af-tab-${target}`)?.focus()
  }
  return (
    <div className="af-tabs">
      <div className="af-tablist-wrap" data-fade-start={edges.start} data-fade-end={edges.end}>
        <div className="af-tablist" role="tablist" aria-label="Form sections" ref={list} onKeyDown={onKeyDown}>
          {FORM_TABS.filter((tab) => tabs.includes(tab.id)).map((tab) => {
            const badge = badges[tab.id] ?? null
            const selected = tab.id === active
            return (
              <button
                key={tab.id}
                id={`af-tab-${tab.id}`}
                type="button"
                role="tab"
                className={cn('af-tab', alsoShown.includes(tab.id) && 'is-shown')}
                aria-selected={selected}
                aria-controls={paneIdFor(tab.id)}
                aria-label={tabAccessibleName(tab.label, badge)}
                tabIndex={selected ? 0 : -1}
                onClick={() => onSelect(tab.id)}
              >
                <span>{tab.label}</span>
                {badge && (badge.count > 0 || badge.kind === 'overdue') && (
                  <span className={cn('af-tab-count', badge.kind === 'notes' && 'is-plain', badge.kind === 'overdue' && 'is-over')} aria-hidden="true">{badge.kind === 'overdue' ? '!' : badge.count}</span>
                )}
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}
