import { useEffect, useMemo, useRef, useState } from 'react'
import { Columns3, RotateCcw, Search } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { CheckboxInput } from '@/shared/ui/primitives'
import { CHOICE_COMPACT, CHOICE_WIDE, groupMenuColumns, readChoices, saveChoices, type ColumnChoices, type MenuColumn } from './column-fit'

/** The person's own column choices for one screen, kept in localStorage. */
export function useColumnChoices(scope: string) {
  const [choices, setChoices] = useState<ColumnChoices>(() => readChoices(scope))
  const update = (next: ColumnChoices) => { setChoices(next); saveChoices(scope, next) }
  return [choices, update] as const
}

interface Props {
  /** Every column the person may switch (not the actions column), with its group. */
  columns: MenuColumn[]
  /** Keys on screen right now. */
  shown: string[]
  choices: ColumnChoices
  onChange: (next: ColumnChoices) => void
  /** Columns the person wants that did not fit (so the menu can say why a box is unchecked). */
  hidden?: number
  /** Group order in the menu. */
  groups?: readonly string[]
}

/**
 * "Columns" button with a searchable, grouped checklist of every column, "Show all", "Default" and "Fit to screen", the
 * table density, and the opt-in wide table. Unchecked columns are still in the expandable row, the drawer and the cards.
 */
export function ColumnMenu({ columns, shown, choices, onChange, hidden = 0, groups = [] }: Props) {
  const [open, setOpen] = useState(false)
  const [search, setSearch] = useState('')
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!open) return
    const close = (event: Event) => { if (!box.current?.contains(event.target as Node)) setOpen(false) }
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') setOpen(false) }
    document.addEventListener('mousedown', close); document.addEventListener('keydown', key)
    return () => { document.removeEventListener('mousedown', close); document.removeEventListener('keydown', key) }
  }, [open])
  const grouped = useMemo(() => groupMenuColumns(columns, groups, search), [columns, groups, search])
  const toggle = (key: string) => onChange({ ...choices, [key]: !shown.includes(key) })
  const density = choices[CHOICE_COMPACT] === true ? 'compact' : choices[CHOICE_COMPACT] === false ? 'comfortable' : 'auto'
  const setDensity = (value: 'auto' | 'comfortable' | 'compact') => {
    const next = { ...choices }
    if (value === 'auto') delete next[CHOICE_COMPACT]; else next[CHOICE_COMPACT] = value === 'compact'
    onChange(next)
  }
  const showAll = () => { const next: ColumnChoices = { ...choices }; for (const column of columns) next[column.key] = true; onChange(next) }
  const fitToScreen = () => { const next: ColumnChoices = {}; if (choices[CHOICE_WIDE]) next[CHOICE_WIDE] = true; onChange(next) }
  return (
    <div className="db-colmenu" ref={box}>
      <button type="button" className={cn('db-refresh', open && 'is-on')} title="Choose columns" aria-label="Choose columns" aria-haspopup="true" aria-expanded={open} onClick={() => setOpen((value) => !value)}><Columns3 size={15} /></button>
      {open && (
        <div className="db-colmenu-pop" role="group" aria-label="Columns">
          <label className="db-colmenu-search"><Search size={13} aria-hidden="true" /><input type="search" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Find a column" aria-label="Find a column" /></label>
          <div className="db-colmenu-btns">
            <button type="button" onClick={showAll}>Show all</button>
            <button type="button" onClick={() => onChange({})}><RotateCcw size={12} aria-hidden="true" />Default</button>
            <button type="button" onClick={fitToScreen}>Fit to screen</button>
          </div>
          <div className="db-colmenu-list">
            {grouped.length === 0 && <small>No column matches "{search}".</small>}
            {grouped.map((group) => (
              <div key={group.name} className="db-colmenu-group">
                <p>{group.name}</p>
                {group.columns.map((column) => (
                  <label key={column.key} className="db-colmenu-row" title={column.header}>
                    <CheckboxInput checked={shown.includes(column.key)} aria-label={column.header} onChange={() => toggle(column.key)} />
                    <span>{column.header}</span>{column.hint && <em>{column.hint}</em>}
                  </label>
                ))}
              </div>
            ))}
          </div>
          <div className="db-colmenu-foot">
            <span>Spacing</span>
            <div className="db-seg" role="group" aria-label="Spacing">
              {(['auto', 'comfortable', 'compact'] as const).map((value) => (
                <button key={value} type="button" className={cn('db-seg-btn', density === value && 'is-on')} aria-pressed={density === value} onClick={() => setDensity(value)}>{value === 'auto' ? 'Auto' : value === 'comfortable' ? 'Roomy' : 'Compact'}</button>
              ))}
            </div>
            <label className="db-colmenu-wide" title="Let the table be wider than the screen and scroll sideways, the first columns stay in view.">
              <CheckboxInput checked={choices[CHOICE_WIDE] === true} aria-label="Wide table" onChange={(checked) => onChange({ ...choices, [CHOICE_WIDE]: checked })} />
              <span>Wide table (scrolls sideways)</span>
            </label>
            <small>{hidden > 0 ? `${hidden} ${hidden === 1 ? 'column does' : 'columns do'} not fit. Open a row to see every field, or switch on Compact.` : 'Every column fits. Open a row to see every field.'}</small>
          </div>
        </div>
      )}
    </div>
  )
}
