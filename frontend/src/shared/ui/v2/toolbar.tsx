import { useRef, type KeyboardEvent, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * Toolbar: one aligned row of search + filters + view toggle. Every control is 40px; wraps to full-width rows on phones.
 *
 * Props
 *  - search?   node   usually <SearchInput/>; takes the flexible space (max 360px)
 *  - filters?  node   Selects / DatePickers / chips (children are appended here too)
 *  - end?      node   right side: view toggle (<Segmented/>), export button
 *  - label?    string accessible name, default "Filters and search"
 */
export function Toolbar({ search, filters, end, children, label = 'Filters and search', className }: { search?: ReactNode; filters?: ReactNode; end?: ReactNode; children?: ReactNode; label?: string; className?: string }) {
  return (
    <div role="toolbar" aria-label={label} className={cn('v2-toolbar', className)}>
      {search && <div className="v2-toolbar-search">{search}</div>}
      {(filters || children) && <div className="v2-toolbar-filters">{filters}{children}</div>}
      {end && <div className="v2-toolbar-end">{end}</div>}
    </div>
  )
}

/**
 * Segmented: a small single-choice switch (list/grid view, period). Arrow keys move the choice.
 *
 * Props
 *  - options  { value, label, icon? }[]   `label` is also the accessible name when `iconOnly`
 *  - value    string
 *  - onChange (value) => void
 *  - label    string   accessible name of the group
 *  - iconOnly? boolean  show only the icons (labels become tooltips)
 */
export function Segmented<T extends string>({ options, value, onChange, label, iconOnly, className }: { options: { value: T; label: string; icon?: ReactNode }[]; value: T; onChange: (value: T) => void; label: string; iconOnly?: boolean; className?: string }) {
  const group = useRef<HTMLDivElement>(null)
  const onKey = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = options.findIndex((option) => option.value === value)
    const next = event.key === 'ArrowRight' || event.key === 'ArrowDown' ? index + 1 : event.key === 'ArrowLeft' || event.key === 'ArrowUp' ? index - 1 : null
    if (next === null) return
    event.preventDefault()
    const target = options[(next + options.length) % options.length]
    onChange(target.value)
    requestAnimationFrame(() => group.current?.querySelector<HTMLElement>('[aria-checked="true"]')?.focus())
  }
  return (
    <div ref={group} role="radiogroup" aria-label={label} className={cn('v2-segmented', className)} onKeyDown={onKey}>
      {options.map((option) => (
        <button key={option.value} type="button" role="radio" aria-checked={option.value === value} tabIndex={option.value === value ? 0 : -1} title={iconOnly ? option.label : undefined} aria-label={iconOnly ? option.label : undefined} className="v2-segment" onClick={() => onChange(option.value)}>
          {option.icon}
          {!iconOnly && <span>{option.label}</span>}
        </button>
      ))}
    </div>
  )
}
