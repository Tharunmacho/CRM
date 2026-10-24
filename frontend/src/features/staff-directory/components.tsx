import { useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronRight, LayoutGrid, List, RefreshCw } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials } from '@/shared/lib/format'
import { Badge, type Tone } from '@/shared/ui/display'
import { Button, Field, SearchInput, Select } from '@/shared/ui/primitives'
import { DataTable, type Column } from '@/shared/ui/table'
import { availabilityOptions, availabilityWords, candidateLink, staffRoleLabel, taskLink, type DirectoryEmployee, type StaffAvailabilityStatus } from './model'

const AVAILABILITY_TONE: Record<StaffAvailabilityStatus, Tone> = { available: 'green', approved_leave: 'amber', manual_leave: 'amber', paused: 'neutral', inactive: 'neutral', not_assigned: 'neutral' }

export function AvailabilityBadge({ employee }: { employee: DirectoryEmployee }) {
  return <Badge variant="dot" tone={AVAILABILITY_TONE[employee.availability.status]}>{availabilityWords(employee.availability.status, employee.availability.label)}</Badge>
}

const hue = (name: string) => 'avatar-hue-' + (name.split('').reduce((total, character) => total + character.charCodeAt(0), 0) % 6)

/** Avatar, name and role/e-mail. With `detailHref` the name is a link to the employee record. */
export function EmployeeIdentity({ employee, detailHref, large = false }: { employee: DirectoryEmployee; detailHref?: string; large?: boolean }) {
  const body = <>
    <span className={cn('record-avatar', hue(employee.name), large && 'is-large')} aria-hidden="true">{initials(employee.name)}</span>
    <span className="staff-id-text">
      <span className="record-name" title={employee.name}>{employee.name}</span>
      <span className="record-detail" title={employee.email}>{staffRoleLabel(employee.role)}{!employee.active && ' · Inactive'}{!large && ' · ' + employee.email}</span>
    </span>
  </>
  return detailHref
    ? <Link to={detailHref} className="record-identity staff-identity" aria-label={`Open employee record for ${employee.name}`}>{body}</Link>
    : <div className="record-identity staff-identity">{body}</div>
}

export function DirectoryFilters({ employees, search, employee, availability, onChange }: {
  employees: DirectoryEmployee[]; search: string; employee: string; availability: string; onChange: (name: string, value: string) => void
}) {
  const active = Boolean(search || employee || availability)
  return <div className="staff-toolbar" role="group" aria-label="Staff filters">
    <Field label="Search employees"><SearchInput value={search} placeholder="Name or email address" onChange={(value) => onChange('search', value)} /></Field>
    <Field label="Employee"><Select value={employee} onChange={(event) => onChange('employee', event.target.value)}><option value="">All employees</option>{employees.map((person) => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field>
    <Field label="New candidates"><Select value={availability} onChange={(event) => onChange('availability', event.target.value)}><option value="">Anyone</option>{availabilityOptions.map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}</Select></Field>
    {active && <Button variant="ghost" className="staff-clear" onClick={() => onChange('clear', '')}>Clear filters</Button>}
  </div>
}

export type DirectoryView = 'list' | 'cards'
export function useDirectoryView(): [DirectoryView, (view: DirectoryView) => void] {
  const [view, setView] = useState<DirectoryView>(() => { try { return localStorage.getItem('crm-staff-view') === 'cards' ? 'cards' : 'list' } catch { return 'list' } })
  return [view, (next) => { setView(next); try { localStorage.setItem('crm-staff-view', next) } catch { /* optional */ } }]
}
export function ViewToggle({ view, onChange }: { view: DirectoryView; onChange: (view: DirectoryView) => void }) {
  return <div className="staff-view-toggle" role="group" aria-label="Directory view">
    {([['list', 'List', List], ['cards', 'Cards', LayoutGrid]] as const).map(([key, label, Icon]) => <button key={key} type="button" aria-pressed={view === key} onClick={() => onChange(key)}><Icon size={15} aria-hidden="true" />{label}</button>)}
  </div>
}

function Count({ employee, to, value, noun }: { employee: DirectoryEmployee; to: string; value: number; noun: string }) {
  return <Link className="staff-count-link" to={to} aria-label={`${value} ${noun} for ${employee.name}`}>{value}<ArrowUpRight size={13} aria-hidden="true" /></Link>
}

export function AttentionTags({ employee }: { employee: DirectoryEmployee }) {
  const tags = [
    { to: taskLink(employee.id, 'overdue'), value: employee.overdueTasks, label: 'overdue', tone: 'overdue', aria: 'overdue tasks' },
    { to: taskLink(employee.id, 'escalated'), value: employee.escalatedTasks, label: 'very late', tone: 'escalated', aria: 'very overdue tasks' },
    { to: taskLink(employee.id, 'missing'), value: employee.missingUpdates, label: 'no update', tone: 'missing', aria: 'tasks missing an update' },
  ]
  return <div className="staff-tags">{tags.map((tag) => <Link key={tag.label} to={tag.to} className="staff-tag" data-tone={tag.value ? tag.tone : undefined} title={`${tag.value} ${tag.aria}`} aria-label={`${tag.value} ${tag.aria} for ${employee.name}`}>{tag.value} {tag.label}</Link>)}</div>
}

export function DirectoryTable({ employees, query }: { employees: DirectoryEmployee[]; query: string }) {
  const href = (employee: DirectoryEmployee) => `/staff-directory/${encodeURIComponent(employee.id)}${query ? '?' + query : ''}`
  const columns: Column<DirectoryEmployee>[] = [
    { key: 'name', header: 'Employee', width: '22%', render: (employee) => <EmployeeIdentity employee={employee} detailHref={href(employee)} /> },
    { key: 'intake', header: 'New candidates', width: '19%', render: (employee) => <AvailabilityBadge employee={employee} /> },
    { key: 'candidates', header: 'Candidates', width: '10%', render: (employee) => <Count employee={employee} to={candidateLink(employee.id)} value={employee.assignedCandidates} noun="assigned candidates" /> },
    { key: 'calls', header: 'Initial calls', width: '8%', render: (employee) => <Count employee={employee} to={taskLink(employee.id, 'initial')} value={employee.initialCallsPending} noun="initial calls pending" /> },
    { key: 'open', header: 'Open tasks', width: '8%', render: (employee) => <Count employee={employee} to={taskLink(employee.id, 'open')} value={employee.openTasks} noun="open tasks" /> },
    { key: 'attention', header: 'Needs attention', width: '23%', render: (employee) => <AttentionTags employee={employee} /> },
    { key: 'record', header: 'Record', width: '8%', actions: true, align: 'right', render: (employee) => <Link className="staff-open" to={href(employee)} aria-label={`View employee record for ${employee.name}`} title={`View employee record for ${employee.name}`}>Open<ChevronRight size={14} aria-hidden="true" /></Link> },
  ]
  return <DataTable compact stackBelow={1280} rows={employees} columns={columns} rowKey={(employee) => employee.id} pageSize={50} />
}

export function DirectoryCards({ employees, query }: { employees: DirectoryEmployee[]; query: string }) {
  return <ul className="staff-cards">{employees.map((employee) => {
    const href = `/staff-directory/${encodeURIComponent(employee.id)}${query ? '?' + query : ''}`
    return <li key={employee.id} className="staff-card">
      <header><EmployeeIdentity employee={employee} detailHref={href} /></header>
      <div><AvailabilityBadge employee={employee} /></div>
      <dl>
        <div><dt>Candidates</dt><dd><Count employee={employee} to={candidateLink(employee.id)} value={employee.assignedCandidates} noun="assigned candidates" /></dd></div>
        <div><dt>Initial calls</dt><dd><Count employee={employee} to={taskLink(employee.id, 'initial')} value={employee.initialCallsPending} noun="initial calls pending" /></dd></div>
        <div><dt>Open tasks</dt><dd><Count employee={employee} to={taskLink(employee.id, 'open')} value={employee.openTasks} noun="open tasks" /></dd></div>
      </dl>
      <footer><AttentionTags employee={employee} /><Link className="staff-open" to={href} aria-label={`View employee record for ${employee.name}`}>Open<ChevronRight size={14} aria-hidden="true" /></Link></footer>
    </li>
  })}</ul>
}

export function DirectoryFeedback({ loading, error, retry, stale = false }: { loading: boolean; error: string; retry: () => void; stale?: boolean }) {
  if (error) return <div role="alert" className="staff-error"><div><strong>{stale ? 'We could not refresh the staff list' : 'We could not load the staff list'}</strong><p>{error}{stale && ' What you see may be out of date.'}</p></div><Button icon={<RefreshCw size={16} />} onClick={retry}>{error === 'The start date must be on or before the end date.' ? 'Reset dates' : 'Try again'}</Button></div>
  if (loading) return <div role="status" className="staff-skeleton" aria-label="Loading staff"><div className="staff-skeleton-kpis">{[0, 1, 2, 3].map((i) => <span key={i} />)}</div><div className="staff-skeleton-table">{[0, 1, 2, 3, 4].map((i) => <span key={i} />)}</div><span className="sr-only">Loading staff…</span></div>
  return null
}

export function DirectoryUpdated({ generatedAt, timezone }: { generatedAt: string; timezone: string }) {
  const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(new Date(generatedAt))
  return <span className="staff-updated"><i aria-hidden="true" />Work right now · Updated {time}</span>
}

export function SectionHead({ title, meta, action }: { title: string; meta?: string; action?: ReactNode }) {
  return <header className="staff-head"><div><h2>{title}</h2>{meta && <p>{meta}</p>}</div>{action && <div className="staff-head-actions">{action}</div>}</header>
}
