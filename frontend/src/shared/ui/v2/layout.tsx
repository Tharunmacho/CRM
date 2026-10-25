import { useId, useState, type CSSProperties, type ElementType, type ReactNode } from 'react'
import { ArrowLeft, SlidersHorizontal } from 'lucide-react'
import { cn } from '@/shared/lib/cn'

/**
 * Layout helpers for screens that do NOT share one template. Gaps are on the 8px grid (`gap` = number of 8px units).
 *
 *  Stack    ({ gap?: 0.5|1|2|3|4|5|6|8 (default 2), as?, align? })               vertical flow
 *  Cluster  ({ gap?, as?, align?: 'start'|'center'|'end'|'baseline', justify?: 'start'|'between'|'end'|'center', wrap? (default true) })  horizontal flow that wraps
 *  SplitPane({ list, detail, hasSelection?, onBack?, backLabel?, listWidth?, contained?, listLabel?, detailLabel? })
 *      master-detail. >1100px: list left (default 360px), detail right. <=1100px: only one is shown: the detail (with a Back
 *      button) when hasSelection, otherwise the list. `contained` bounds both to the window height, each scrolling inside itself.
 *  Workbench({ toolbar?, rail?, railLabel?, inspector?, children })
 *      toolbar on top, a filter rail (left, 264px), the results (children), an optional inspector (right, 360px).
 *      <=1100px: the rail folds behind a "Filters" button; <1280px the inspector stacks under the results.
 *  Board    ({ minColumn?: number (default 280), gap? })    equal-height cards/columns on an auto-fill grid
 *  BoardColumn({ title, meta?, actions?, children })        a titled column of BoardCards
 *  BoardCard({ as?, onClick?, children })                   equal-height card (a button when onClick is given)
 */
type Gap = 0.5 | 1 | 2 | 3 | 4 | 5 | 6 | 8

export function Stack({ gap = 2, as: Tag = 'div', align, className, children }: { gap?: Gap; as?: ElementType; align?: 'start' | 'center' | 'end' | 'stretch'; className?: string; children?: ReactNode }) {
  return <Tag className={cn('v2-stack', className)} style={{ gap: gap * 8, alignItems: align && (align === 'stretch' ? 'stretch' : `flex-${align}`) } as CSSProperties}>{children}</Tag>
}

export function Cluster({ gap = 1, as: Tag = 'div', align = 'center', justify = 'start', wrap = true, className, children }: { gap?: Gap; as?: ElementType; align?: 'start' | 'center' | 'end' | 'baseline'; justify?: 'start' | 'between' | 'end' | 'center'; wrap?: boolean; className?: string; children?: ReactNode }) {
  const map = { start: 'flex-start', end: 'flex-end', center: 'center', between: 'space-between', baseline: 'baseline' } as const
  return <Tag className={cn('v2-cluster', className)} style={{ gap: gap * 8, alignItems: map[align], justifyContent: map[justify], flexWrap: wrap ? 'wrap' : 'nowrap' } as CSSProperties}>{children}</Tag>
}

export function SplitPane({ list, detail, hasSelection = false, onBack, backLabel = 'Back to list', listWidth = 360, contained = false, listLabel = 'List', detailLabel = 'Details', className }: {
  list: ReactNode
  detail: ReactNode
  hasSelection?: boolean
  onBack?: () => void
  backLabel?: string
  listWidth?: number
  contained?: boolean
  listLabel?: string
  detailLabel?: string
  className?: string
}) {
  return (
    <div className={cn('v2-split', contained && 'is-contained', hasSelection ? 'is-detail' : 'is-list', className)} style={{ '--v2-split-list': `${listWidth}px` } as CSSProperties}>
      <section className="v2-split-list" aria-label={listLabel}>{list}</section>
      <section className="v2-split-detail" aria-label={detailLabel}>
        {onBack && <button type="button" className="v2-back v2-split-back" onClick={onBack}><ArrowLeft size={15} aria-hidden="true" />{backLabel}</button>}
        {detail}
      </section>
    </div>
  )
}

export function Workbench({ toolbar, rail, railLabel = 'Filters', inspector, inspectorLabel = 'Details', children, className }: {
  toolbar?: ReactNode
  rail?: ReactNode
  railLabel?: string
  inspector?: ReactNode
  inspectorLabel?: string
  children: ReactNode
  className?: string
}) {
  const [railOpen, setRailOpen] = useState(false)
  const railId = useId()
  return (
    <div className={cn('v2-workbench', rail && 'has-rail', inspector && 'has-inspector', className)}>
      {toolbar && <div className="v2-workbench-toolbar">{toolbar}</div>}
      {rail && (
        <>
          <button type="button" className="v2-workbench-railtoggle" aria-expanded={railOpen} aria-controls={railId} onClick={() => setRailOpen((open) => !open)}><SlidersHorizontal size={16} aria-hidden="true" />{railOpen ? 'Hide' : 'Show'} {railLabel.toLowerCase()}</button>
          <aside id={railId} className={cn('v2-workbench-rail', railOpen && 'is-open')} aria-label={railLabel}>{rail}</aside>
        </>
      )}
      <div className="v2-workbench-results">{children}</div>
      {inspector && <aside className="v2-workbench-inspector" aria-label={inspectorLabel}>{inspector}</aside>}
    </div>
  )
}

export function Board({ minColumn = 280, gap = 2, className, children }: { minColumn?: number; gap?: 1 | 2 | 3; className?: string; children: ReactNode }) {
  return <div className={cn('v2-board', className)} style={{ '--v2-board-min': `${minColumn}px`, gap: gap * 8 } as CSSProperties}>{children}</div>
}

export function BoardColumn({ title, meta, actions, children, className }: { title: ReactNode; meta?: ReactNode; actions?: ReactNode; children: ReactNode; className?: string }) {
  const id = useId()
  return (
    <section className={cn('v2-board-col', className)} aria-labelledby={id}>
      <header className="v2-board-col-head">
        <div><h3 id={id}>{title}</h3>{meta && <p>{meta}</p>}</div>
        {actions}
      </header>
      <div className="v2-board-col-body">{children}</div>
    </section>
  )
}

export function BoardCard({ as: Tag = 'div', onClick, className, children }: { as?: ElementType; onClick?: () => void; className?: string; children: ReactNode }) {
  return onClick
    ? <button type="button" className={cn('v2-board-card is-action', className)} onClick={onClick}>{children}</button>
    : <Tag className={cn('v2-board-card', className)}>{children}</Tag>
}
