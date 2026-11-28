import { useEffect, useState } from 'react'
import { Dialog } from '@/shared/ui/overlay'
import { Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { Button, Input, Select } from '@/shared/ui/primitives'
import { sectionKeys, sections, type SectionKey } from './organiser'
import type { Priority } from '@/shared/model/types'

const columns: Record<SectionKey, string[]> = {
  meetings: ['Time', 'Meeting with', 'Purpose', 'Location or meeting link', 'Status'],
  interviews: ['Time', 'Country', 'Job title', 'Client or agency', 'Number of candidates', 'Status'],
  calls: ['Contact name', 'Country', 'Purpose', 'Follow-up date', 'Status'],
  recruitment: ['Activity', 'Target', 'Completed', 'Pending'],
  payables: ['Pay to', 'Purpose', 'Amount', 'Due date', 'Status'],
  receivables: ['Receive from', 'Purpose', 'Amount', 'Expected date', 'Status'],
  deployment: ['Candidate or worker', 'Country', 'Process', 'Current status', 'Next action'],
  reminders: ['Task', 'Priority', 'Responsible staff', 'Due date', 'Status'],
  review: [...sections.review.fields],
}

const activities = ['New requirements received', 'Candidates sourced', 'Candidates pre-screened', 'CVs forwarded to clients', 'Interviews arranged', 'Candidates selected', 'Offer letters received', 'Visas received']
type Row = { id: string; section: SectionKey; values: Record<string, string>; touched: boolean }
const indiaClock = new Intl.DateTimeFormat('en-GB', {
  timeZone: 'Asia/Kolkata', hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
})
const indiaDateFormat = new Intl.DateTimeFormat('sv-SE', { timeZone: 'Asia/Kolkata', year: 'numeric', month: '2-digit', day: '2-digit' })
const indiaDate = () => indiaDateFormat.format(new Date())
const pad = (n: number) => String(n).padStart(2, '0')
const hourLabel = (hour: number) => (hour % 12 || 12) + (hour < 12 ? ' AM' : ' PM')
const blank = (section: SectionKey, values = {}): Row => ({
  id: crypto.randomUUID(), section, touched: false,
  values: section === 'meetings' || section === 'interviews' ? { Time: indiaClock.format(new Date()), ...values } : values,
})
const initial = () => sectionKeys.flatMap(section => section === 'recruitment'
  ? activities.map(Activity => blank(section, { Activity })) : [blank(section)])

export function DailyWorksheet() {
  const store = useStore()
  const [today, setToday] = useState(indiaDate)
  const [chosenDate, setChosenDate] = useState<string | null>(null)
  const date = chosenDate ?? today
  useEffect(() => {
    const refreshDate = () => setToday(indiaDate())
    const timer = window.setInterval(refreshDate, 30_000)
    window.addEventListener('focus', refreshDate)
    return () => { window.clearInterval(timer); window.removeEventListener('focus', refreshDate) }
  }, [])
  const [rows, setRows] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  type Column = { key: string; name: string }
  type Layout = Partial<Record<SectionKey, Column[]>>
  const storageKey = 'todo-worksheet-fields:' + store.me.id
  const [layout, setLayout] = useState<Layout>(() => {
    try { return JSON.parse(localStorage.getItem(storageKey) || '{}') || {} } catch { return {} }
  })
  const fields = (section: SectionKey) => layout[section] ?? columns[section].map(key => ({ key, name: key }))
  const [editing, setEditing] = useState<SectionKey | null>(null)
  const [draft, setDraft] = useState<Column[]>([])
  const [fieldError, setFieldError] = useState('')
  function editFields(section: SectionKey) {
    setDraft(fields(section))
    setFieldError('')
    setEditing(section)
  }
  function applyFields() {
    if (!editing) return
    const cleaned = draft.map(field => ({ ...field, name: field.name.trim() }))
    const names = cleaned.map(field => field.name.toLowerCase())
    if (!names.length) { setFieldError('Keep at least one field in the section.'); return }
    if (names.includes('') || new Set(names).size !== names.length) {
      setFieldError('Give each field a unique name.')
      return
    }
    const next = { ...layout, [editing]: cleaned }
    try { localStorage.setItem(storageKey, JSON.stringify(next)) }
    catch { setFieldError('Could not save the field layout in this browser.'); return }
    setLayout(next)
    setRows(previous => previous.map(row => row.section === editing ? { ...row,
      values: Object.fromEntries(cleaned.filter(field => Object.prototype.hasOwnProperty.call(row.values, field.key)).map(field => [field.key, row.values[field.key]]))
    } : row))
    setEditing(null)
  }
  function change(row: Row, label: string, value: string) {
    setRows(previous => previous.map(entry => entry.id === row.id
      ? { ...entry, touched: true, values: { ...entry.values, [label]: value } } : entry))
  }
  async function save() {
    const entries = rows.filter(row => row.touched && Object.values(row.values).some(value => value.trim()))
    for (const row of entries) {
      const title = sections[row.section].title
      if (fields(row.section).some(field => field.key === title) && !row.values[title]?.trim()) {
        setError('Enter ' + (fields(row.section).find(field => field.key === title)?.name || title).toLowerCase() + ' under ' + sections[row.section].label + '.')
        return
      }
    }
    setBusy(true)
    setError('')
    try {
      for (const row of entries) {
        const { section, values } = row
        const title = sections[section].title
        const day = values['Due date'] || values['Expected date'] || values['Follow-up date'] || date
        await store.addTodo({
          section, title: (fields(section).some(field => field.key === title) && values[title]?.trim()) || sections[section].label,
          details: Object.fromEntries(fields(section).filter(({key}) => key !== title && key !== 'Priority' && values[key]?.trim()).map(({key, name}) => [name, values[key].trim()])),
          dueAt: new Date(day + 'T' + (values.Time || '17:00') + '+05:30').toISOString(),
          priority: (values.Priority || 'normal') as Priority,
        })
        setRows(previous => previous.map(entry => entry.id === row.id ? blank(section, section === 'recruitment' ? { Activity: values.Activity } : {}) : entry))
      }
      store.notify('Daily organiser saved.')
    } catch (failure) { setError(failure instanceof Error ? failure.message : 'Could not save the entry. Try again.') }
    finally { setBusy(false) }
  }
  return <form className="td-sheet" onSubmit={event => { event.preventDefault(); void save() }}>
    <fieldset disabled={busy}>
      <header className="td-sheet-header"><h2>Daily office organiser</h2>
        <label>Date<Input type="date" required value={date} onChange={event => setChosenDate(event.target.value || null)} /></label>
      </header>
      {sectionKeys.map((section, index) => <section key={section}>
        <div className="td-section-band">
          <h3><span className="td-section-number">{index + 1}</span>{sections[section].label}</h3>
          <div className="td-sheet-tools">
            <Button type="button" size="sm" variant="ghost" onClick={() => editFields(section)}>Edit fields</Button>
          {section !== 'review'  && <Button type="button" variant="ghost" onClick={() => setRows(previous => [...previous, blank(section)])}>+ Row</Button>}
          </div>
        </div>
        {rows.filter(row => row.section === section).map((row, rowIndex) => <div className={'td-sheet-row td-sheet-' + section} key={row.id}>
          {fields(section).map(({key: label, name}) => label === 'Time' ? <div className="td-clock-cell" key={label}>
            <span className={rowIndex && fields(section).length <= 6 ? 'td-repeat-label' : undefined}>{name} (IST)</span>
            <div className="td-clock" role="group" aria-label={name + ' in Indian Standard Time'}>
              <select aria-label={name + ' hour and AM or PM'} value={row.values.Time?.slice(0, 2) || '00'}
                onChange={e => change(row, label, e.target.value + ':' + (row.values.Time?.slice(3) || '00'))}>
                {Array.from({ length: 24 }, (_, h) => <option key={h} value={pad(h)}>{hourLabel(h)}</option>)}
              </select>
              <select aria-label={name + ' minutes'} value={row.values.Time?.slice(3) || '00'}
                onChange={e => change(row, label, (row.values.Time?.slice(0, 2) || '00') + ':' + e.target.value)}>
                {Array.from({ length: 60 }, (_, m) => <option key={m} value={pad(m)}>{pad(m)} min</option>)}
              </select>
            </div>
          </div> : <label key={label}><span className={rowIndex && fields(section).length <= 6 ? "td-repeat-label" : undefined}>{name}</span>
            {label === 'Priority' ? <Select value={row.values.Priority || 'normal'} onChange={e => change(row, label, e.target.value)}>
              <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
            </Select> : <Input type={label.endsWith('date') ? 'date' : 'text'} value={row.values[label] || ''}
              maxLength={label === sections[section].title ? 300 : 1000} onChange={e => change(row, label, e.target.value)} />}
          </label>)}
        </div>)}
      </section>)}
      {error && <p role="alert">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy || !rows.some(row => row.touched)}>Save organiser</Button>
    </fieldset>
    {editing && <Dialog open title={sections[editing].label + ' fields'} onClose={() => setEditing(null)}
      footer={<><Button type="button" onClick={() => setEditing(null)}>Cancel</Button><Button type="button" variant="primary" onClick={applyFields}>Save fields</Button></>}>
      <div className="grid gap-2">
        {draft.map((field, index) => <div className="flex items-center gap-2" key={field.key}>
          <Input autoFocus={index === 0} aria-label={'Field name ' + (index + 1)} maxLength={60} value={field.name}
            onChange={event => {
              const name = event.currentTarget.value
              setDraft(previous => previous.map(item => item.key === field.key ? { ...item, name } : item))
            }} />
          <Button type="button" variant="ghost" aria-label={'Remove ' + field.name}
            onClick={() => setDraft(previous => previous.filter(item => item.key !== field.key))}><Trash2 size={16} /></Button>
        </div>)}
        <Button type="button" disabled={draft.length >= 12} onClick={() => setDraft(previous => [...previous, { key: crypto.randomUUID(), name: '' }])}>Add field</Button>
        {fieldError && <p role="alert">{fieldError}</p>}
      </div>
    </Dialog>}
  </form>
}
