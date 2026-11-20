import { useRef } from 'react'
import { cn } from '@/shared/lib/cn'
import { VIEW_CAPTION, nextViewIndex, type DailyView, type ViewTab } from '@/features/daily-work/daily-work-model'

/** The id of a view's tab: the table below names it as its label. */
export const viewTabId = (view: DailyView) => `dw-view-${view}`

/**
 * The filter bar above the Tasks table: Current process, Declined, Rejected, Travelled and All, each with its count. One tab stop: Left / Right
 * (and Up / Down, Home, End) move the focus along the bar, Enter or Space picks the view (so arrowing past a tab never starts a request). The chosen
 * view is the page's `?view=`. Under the bar, a plain sentence says what a closed view means, so nobody has to guess why a candidate is in it.
 */
export function ViewTabs({ tabs, active, onChange }: { tabs: ViewTab[]; active: DailyView; onChange: (view: DailyView) => void }) {
  const buttons = useRef<(HTMLButtonElement | null)[]>([])
  const caption = VIEW_CAPTION[active]
  return (
    <div className="dw-views-wrap">
      <div className="dw-views" role="tablist" aria-label="Candidates by status">
        {tabs.map((tab, index) => {
          const selected = tab.key === active
          return (
            <button key={tab.key} ref={(element) => { buttons.current[index] = element }} type="button" role="tab" id={viewTabId(tab.key)}
              aria-selected={selected} tabIndex={selected ? 0 : -1} className={cn('dw-view', `is-${tab.key}`, selected && 'is-active')}
              onClick={() => { if (!selected) onChange(tab.key) }}
              onKeyDown={(event) => {
                const next = nextViewIndex(event.key, index, tabs.length)
                if (next === null) return
                event.preventDefault()
                buttons.current[next]?.focus()
              }}>
              <span className="dw-view-name">{tab.label}</span>
              {tab.count !== null && <b className="dw-view-count tnum">{tab.count}</b>}
            </button>
          )
        })}
      </div>
      {caption && <p className="dw-views-caption" role="note">{caption}</p>}
    </div>
  )
}
