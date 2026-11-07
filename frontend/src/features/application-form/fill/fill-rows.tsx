import { useId, type CSSProperties } from 'react'
import { ArrowDown, ArrowUp, Plus, Trash2, type LucideIcon } from 'lucide-react'
import { IconButton, Input } from '@/shared/ui/primitives'
import { isBlank, isBlankRow } from '../form-model'
import { SourceBadge } from './fill-controls'
import { columnSpanMd, type FillColumn, type SourceChip } from './fill-fields'

interface Copy { noun: string; add: string; empty: string }

/** One box of one row: the label is repeated on every row so a row still reads on its own on a phone. */
function RowCell({ column, value, error, canWork, onChange }: { column: FillColumn; value: string; error?: string; canWork: boolean; onChange: (value: string) => void }) {
  const id = useId()
  const style = { ['--span' as string]: column.span, ['--span-md' as string]: columnSpanMd(column) } as CSSProperties
  if (!canWork) {
    return (
      <div className="aff-field is-readonly" style={style}>
        <div className="aff-lab"><span className="aff-label-text">{column.label}</span></div>
        <div className="aff-value">{isBlank(value) ? <span className="aff-empty">Not added</span> : <span className="aff-text">{value}</span>}</div>
      </div>
    )
  }
  return (
    <div className={error ? 'aff-field has-error' : 'aff-field'} style={style}>
      <div className="aff-lab"><label htmlFor={id}>{column.label}</label></div>
      <Input id={id} value={value} placeholder={column.placeholder} inputMode={column.inputMode} maxLength={column.maxLength} autoComplete="off" aria-invalid={error ? true : undefined}
        onChange={(event) => onChange(column.inputMode ? event.target.value.replace(/\D/g, '') : event.target.value)} />
      {error && <p className="aff-note is-error" role="alert">{error}</p>}
    </div>
  )
}

/**
 * The repeating rows of the form (qualifications, employers) as tidy mini-cards: a small title ("Qualification 1") with the
 * move and remove buttons in a fixed top-right column, the fields on the same 12-column grid, and a dashed "Add" button
 * across the width below. Rows are the draft's own (they keep a `rowId`), so every keystroke updates the paper view. A row
 * you have just added stays on screen while it is blank; blank rows are ignored when counting and saving.
 */
export function FillRows<T extends { rowId: string }>({ kind, dataField, rows, columns, valueOf, errors, errorPrefix, canWork, chip, copy, icon: Icon, onChange, onAdd, onMove, onRemove }: {
  kind: 'education' | 'experience'
  /** The missing item this list answers to (`education`, `experience.history`), so "Fill in" lands on it. */
  dataField: string
  rows: T[]
  columns: FillColumn[]
  valueOf: (row: T, key: string) => string
  errors: Record<string, string>
  errorPrefix: string
  canWork: boolean
  chip: SourceChip | null
  copy: Copy
  icon: LucideIcon
  onChange: (rowId: string, key: string, value: string) => void
  onAdd: () => void
  onMove: (rowId: string, direction: -1 | 1) => void
  onRemove: (rowId: string) => void
}) {
  const shown = canWork ? rows : rows.filter((row) => !isBlankRow(row))
  return (
    <div className="aff-rows" data-field={dataField} data-kind={kind}>
      {chip && <div className="aff-rows-bar"><SourceBadge kind={chip} /></div>}
      {shown.length === 0 ? (
        <div className="aff-rows-empty">
          <Icon aria-hidden="true" />
          <p>{copy.empty}</p>
        </div>
      ) : (
        <ol className="aff-row-list">
          {shown.map((row, index) => {
            const at = rows.indexOf(row)
            const noun = copy.noun.toLowerCase()
            return (
              <li className="aff-row" key={row.rowId}>
                <div className="aff-row-head">
                  <span className="aff-row-no">{copy.noun} {index + 1}</span>
                  {canWork && (
                    <div className="aff-row-ctl">
                      <IconButton label={`Move ${noun} ${index + 1} up`} disabled={at === 0} onClick={() => onMove(row.rowId, -1)}><ArrowUp className="h-4 w-4" /></IconButton>
                      <IconButton label={`Move ${noun} ${index + 1} down`} disabled={at === rows.length - 1} onClick={() => onMove(row.rowId, 1)}><ArrowDown className="h-4 w-4" /></IconButton>
                      <IconButton label={`Remove ${noun} ${index + 1}`} tone="danger" onClick={() => onRemove(row.rowId)}><Trash2 className="h-4 w-4" /></IconButton>
                    </div>
                  )}
                </div>
                <div className="aff-grid">
                  {columns.map((column) => (
                    <RowCell key={column.key} column={column} value={valueOf(row, column.key)} canWork={canWork} error={errors[`${errorPrefix}.${at}.${column.key}`]} onChange={(value) => onChange(row.rowId, column.key, value)} />
                  ))}
                </div>
              </li>
            )
          })}
        </ol>
      )}
      {canWork && (
        <button type="button" className="aff-add" disabled={rows.length >= 30} onClick={onAdd}><Plus aria-hidden="true" />{copy.add}</button>
      )}
    </div>
  )
}
