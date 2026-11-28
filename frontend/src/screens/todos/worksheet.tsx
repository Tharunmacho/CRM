import { useState } from 'react'
import { format } from 'date-fns'
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
const blank = (section: SectionKey, values = {}): Row => ({ id: crypto.randomUUID(), section, values, touched: false })
const initial = () => sectionKeys.flatMap(section => section === 'recruitment'
  ? activities.map(Activity => blank(section, { Activity })) : [blank(section)])

export function DailyWorksheet() {
  const store = useStore()
  const [date, setDate] = useState(() => format(new Date(), 'yyyy-MM-dd'))
  const [rows, setRows] = useState(initial)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  function change(row: Row, label: string, value: string) {
    setRows(previous => previous.map(entry => entry.id === row.id
      ? { ...entry, touched: true, values: { ...entry.values, [label]: value } } : entry))
  }
  async function save() {
    const entries = rows.filter(row => row.touched && Object.values(row.values).some(value => value.trim()))
    for (const row of entries) {
      const title = sections[row.section].title
      if (row.section !== 'review' && !row.values[title]?.trim()) {
        setError('Enter ' + title.toLowerCase() + ' under ' + sections[row.section].label + '.')
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
          section, title: values[title]?.trim() || 'End-of-day review',
          details: Object.fromEntries(Object.entries(values).filter(([key, value]) => key !== title && key !== 'Priority' && value.trim())),
          dueAt: new Date(day + 'T' + (values.Time || '17:00')).toISOString(),
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
      <header><h2>Daily office organiser</h2><label>Date <Input type="date" required value={date} onChange={e => setDate(e.target.value)} /></label>
        <span>{date && format(new Date(date + 'T12:00'), 'EEEE')}</span><span>Staff in charge · {store.me.name}</span></header>
      {sectionKeys.map((section, index) => <section key={section}>
        <h3>{index + 1}. {sections[section].label}</h3>
        {rows.filter(row => row.section === section).map(row => <div className={'td-sheet-row td-sheet-' + section} key={row.id}>
          {columns[section].map(label => <label key={label}>{label}
            {label === 'Priority' ? <Select value={row.values.Priority || 'normal'} onChange={e => change(row, label, e.target.value)}>
              <option value="high">High</option><option value="normal">Normal</option><option value="low">Low</option>
            </Select> : <Input type={label === 'Time' ? 'time' : label.endsWith('date') ? 'date' : 'text'} value={row.values[label] || ''}
              maxLength={label === sections[section].title ? 300 : 1000} onChange={e => change(row, label, e.target.value)} />}
          </label>)}
        </div>)}
        {section !== 'review' && <Button type="button" variant="ghost" onClick={() => setRows(previous => [...previous, blank(section)])}>+ Row</Button>}
      </section>)}
      {error && <p role="alert">{error}</p>}
      <Button type="submit" variant="primary" disabled={busy || !rows.some(row => row.touched)}>Save organiser</Button>
    </fieldset>
  </form>
}
