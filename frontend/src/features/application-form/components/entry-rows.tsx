import { RowTable } from './sections'
import { EDUCATION_COLUMNS, EXPERIENCE_COLUMNS, moveRow } from '../form-model'
import { EDUCATION_KEYS, HISTORY_KEYS, emptyEntryRow, parseRows, serializeRows, type EntryRow } from '../entry-bridge'

const withIds = (rows: EntryRow[]) => rows.map((row, index) => ({ ...row, rowId: `r${index}` }))
const withoutIds = (rows: { rowId: string }[]): EntryRow[] => rows.map(({ rowId: _rowId, ...row }) => row as EntryRow)

/**
 * The repeating education / work-history rows of the paper form, inside the Add candidate page. The value is the JSON
 * text the flat entry form keeps; editing any row hands the whole list back, so the page marks it as typed by a person
 * and a later document read never overwrites it.
 */
export function EntryRows({ kind, value, onChange, errors }: {
  kind: 'education' | 'history'
  value: string
  onChange: (next: string) => void
  errors?: Record<string, string>
}) {
  const keys = kind === 'education' ? EDUCATION_KEYS : HISTORY_KEYS
  const parsed = parseRows(value, keys)
  const rows = withIds(parsed.length ? parsed : [emptyEntryRow(keys)])
  const commit = (next: { rowId: string }[]) => onChange(serializeRows(withoutIds(next)))
  return (
    <div className="af af-embed ac-rows" data-rows={kind}>
      <RowTable
        label={kind === 'education' ? 'Add every course, newest first.' : 'Add every employer, newest first.'}
        columns={kind === 'education' ? EDUCATION_COLUMNS : EXPERIENCE_COLUMNS}
        rows={rows}
        editing
        cols={kind === 'education' ? 'minmax(0,1.4fr) minmax(0,.55fr) minmax(0,1.8fr)' : 'minmax(0,1.4fr) minmax(0,1.2fr) minmax(0,.9fr) minmax(0,1fr)'}
        errors={errors}
        errorPrefix="education"
        onChange={(rowId, key, text) => commit(rows.map((row) => (row.rowId === rowId ? { ...row, [key]: text } : row)))}
        onMove={(rowId, direction) => commit(moveRow(rows, rowId, direction))}
        onRemove={(rowId) => commit(rows.filter((row) => row.rowId !== rowId))}
        onAdd={() => commit([...rows, { ...emptyEntryRow(keys), rowId: `r${rows.length}` }])}
        addLabel={kind === 'education' ? 'Add education' : 'Add employer'}
        emptyText=""
        printBlankRows={0}
      />
    </div>
  )
}
