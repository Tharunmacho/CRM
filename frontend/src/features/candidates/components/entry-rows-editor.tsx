import { useEffect, useState } from 'react'
import { Briefcase, GraduationCap, Plus, Trash2 } from 'lucide-react'
import { Button, Input } from '@/shared/ui/primitives'
import { SuggestInput } from '@/shared/ui/suggest-input'
import { EDUCATION_KEYS, HISTORY_KEYS, emptyEntryRow, parseRows, serializeRows, type EntryRow } from '@/features/application-form/entry-bridge'
import { EntryField, ReadBadge } from './entry-fields'
import type { ScanKind } from '../ocr-form'

type Kind = 'education' | 'history'
interface Column { key: string; label: string; placeholder: string; span: number; inputMode?: 'numeric'; maxLength?: number }

const COLUMNS: Record<Kind, Column[]> = {
  education: [
    { key: 'level', label: 'Educational level / course', placeholder: 'e.g. B.Tech Mechanical Engineering', span: 5 },
    { key: 'passedYear', label: 'Passed year', placeholder: 'YYYY', span: 2, inputMode: 'numeric', maxLength: 4 },
    { key: 'institute', label: 'Institute name & address', placeholder: 'Institute, city', span: 5 },
  ],
  history: [
    { key: 'company', label: 'Company name', placeholder: 'Company', span: 3 },
    { key: 'designation', label: 'Designation', placeholder: 'Designation', span: 3 },
    { key: 'country', label: 'Country', placeholder: 'Country', span: 2 },
    { key: 'period', label: 'Period / duration', placeholder: 'e.g. Jan 2018 - Mar 2020', span: 4 },
  ],
}
const COPY: Record<Kind, { noun: string; plural: string; add: string; empty: string; icon: typeof GraduationCap }> = {
  education: { noun: 'Course', plural: 'courses', add: 'Add education', empty: 'No education added yet. It is read from the resume, or you can add it yourself.', icon: GraduationCap },
  history: { noun: 'Employer', plural: 'employers', add: 'Add employer', empty: 'No previous employer added yet. It is read from the resume, or you can add it yourself.', icon: Briefcase },
}

/**
 * The repeating rows of the paper form (education, previous employers) as small bordered rows. The value is the JSON
 * text the flat entry form keeps, so editing any row hands the whole list back and the page marks it as typed by a
 * person: a later document read never overwrites it. A row you have just added stays on screen while it is blank.
 */
export function EntryRowsEditor({ kind, value, onChange, source, disabled, suggestions = {} }: {
  kind: Kind; value: string; onChange: (next: string) => void; source?: ScanKind; disabled?: boolean
  /** Names offered while typing, by column key (company, designation, country, level, institute). Anything else can still be typed. */
  suggestions?: Record<string, readonly string[]>
}) {
  const keys = kind === 'education' ? EDUCATION_KEYS : HISTORY_KEYS
  const columns = COLUMNS[kind]
  const copy = COPY[kind]
  const [rows, setRows] = useState<EntryRow[]>(() => parseRows(value, keys))
  // Something other than this editor changed the value (a document was read, a draft came back): show it.
  useEffect(() => { if (serializeRows(rows) !== value) setRows(parseRows(value, keys)) }, [value])
  const commit = (next: EntryRow[]) => { setRows(next); onChange(serializeRows(next)) }
  const edit = (index: number, key: string, text: string) => commit(rows.map((row, position) => (position === index ? { ...row, [key]: text } : row)))

  return (
    <div className="ac-rows" data-kind={kind}>
      <div className="ac-rows-bar">
        <span className="ac-rows-count">{rows.length ? `${rows.length} ${rows.length === 1 ? copy.noun.toLowerCase() : copy.plural}` : 'None added'}</span>
        <span className="ac-rows-tools">
          {source && <ReadBadge kind={source} />}
          <Button size="sm" icon={<Plus />} disabled={disabled || rows.length >= (kind === 'education' ? 20 : 30)} onClick={() => commit([...rows, emptyEntryRow(keys)])}>{copy.add}</Button>
        </span>
      </div>
      {rows.length === 0 ? (
        <div className="ac-rows-empty">
          <copy.icon size={20} aria-hidden />
          <p>{copy.empty}</p>
        </div>
      ) : (
        <ol className="ac-row-list">
          {rows.map((row, index) => (
            <li key={index} className="ac-row">
              <span className="ac-row-no" aria-hidden>{index + 1}</span>
              <div className="ac-g ac-row-grid">
                {columns.map((column) => (
                  <EntryField key={column.key} label={column.label} className={`s-${column.span}`}>
                    {suggestions[column.key]?.length
                      ? <SuggestInput options={suggestions[column.key]} value={row[column.key] ?? ''} placeholder={column.placeholder} disabled={disabled} autoComplete="off" onChange={(text) => edit(index, column.key, text)} />
                      : <Input value={row[column.key] ?? ''} placeholder={column.placeholder} inputMode={column.inputMode} maxLength={column.maxLength} disabled={disabled} autoComplete="off"
                          onChange={(event) => edit(index, column.key, column.inputMode ? event.target.value.replace(/\D/g, '') : event.target.value)} />}
                  </EntryField>
                ))}
              </div>
              <button type="button" className="ac-row-remove" disabled={disabled} aria-label={`Remove ${copy.noun.toLowerCase()} ${index + 1}`} title="Remove" onClick={() => commit(rows.filter((_, position) => position !== index))}><Trash2 size={15} /></button>
            </li>
          ))}
        </ol>
      )}
    </div>
  )
}
