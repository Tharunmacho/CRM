import { Link } from 'react-router-dom'
import { ArrowUpRight, ChevronRight, RefreshCw, Search } from 'lucide-react'
import { Button, Field, Input, Select } from '@/shared/ui/primitives'
import { initials } from '@/shared/lib/format'
import { availabilityOptions, candidateLink, staffRoleLabel, taskLink, type DirectoryEmployee } from './model'

export function AvailabilityBadge({ employee }: { employee: DirectoryEmployee }) {
  return <span className={`staff-availability is-${employee.availability.status}`}><span aria-hidden="true" />{employee.availability.label}</span>
}

export function EmployeeIdentity({ employee, detailHref }: { employee: DirectoryEmployee; detailHref?: string }) {
  return <div className="staff-identity"><span className="staff-avatar" aria-hidden="true">{initials(employee.name)}</span><div>{detailHref ? <Link className="staff-person-link" to={detailHref}>{employee.name}</Link> : <strong>{employee.name}</strong>}<span className="staff-role">{staffRoleLabel(employee.role)}{!employee.active && ' · Inactive'}</span><span className="staff-email">{employee.email}</span></div></div>
}

export function DirectoryFilters({ employees, search, employee, availability, onChange }: {
  employees: DirectoryEmployee[]; search: string; employee: string; availability: string; onChange: (name: string, value: string) => void
}) {
  const active = Boolean(search || employee || availability)
  return <div className="staff-directory-filters">
    <Field label="Search employees"><div className="staff-search"><Search size={17} aria-hidden="true" /><Input type="search" value={search} placeholder="Name or email address" onChange={event => onChange('search', event.target.value)} /></div></Field>
    <Field label="Employee"><Select value={employee} onChange={event => onChange('employee', event.target.value)}><option value="">All employees</option>{employees.map(person => <option key={person.id} value={person.id}>{person.name}</option>)}</Select></Field>
    <Field label="Automatic intake"><Select value={availability} onChange={event => onChange('availability', event.target.value)}><option value="">All availability</option>{availabilityOptions.map(option => <option key={option.value} value={option.value}>{option.label}</option>)}</Select></Field>
    {active && <Button variant="ghost" className="staff-clear-filters" onClick={() => onChange('clear', '')}>Clear filters</Button>}
  </div>
}

export function DirectoryTable({ employees, query }: { employees: DirectoryEmployee[]; query: string }) {
  return <div className="staff-directory-table-wrap"><table className="staff-directory-table">
    <caption className="sr-only">Current employee workload and automatic intake availability. Counts open the matching records.</caption>
    <thead><tr><th scope="col">Employee</th><th scope="col">Automatic intake</th><th scope="col">Candidates</th><th scope="col">Initial calls</th><th scope="col">Open tasks</th><th scope="col">Needs attention</th><th scope="col"><span className="sr-only">Employee details</span></th></tr></thead>
    <tbody>{employees.map(employee => {
      const detailHref = `/staff-directory/${encodeURIComponent(employee.id)}${query ? '?' + query : ''}`
      return <tr key={employee.id}>
        <td className="staff-directory-person" data-label="Employee"><EmployeeIdentity employee={employee} detailHref={detailHref} /></td>
        <td className="staff-directory-availability" data-label="Automatic intake"><AvailabilityBadge employee={employee} /></td>
        <td data-label="Candidates"><Link className="staff-count-link" to={candidateLink(employee.id)} aria-label={`${employee.assignedCandidates} assigned candidates for ${employee.name}`}>{employee.assignedCandidates}<ArrowUpRight size={13} aria-hidden="true" /></Link></td>
        <td data-label="Initial calls"><Link className="staff-count-link" to={taskLink(employee.id, 'initial')} aria-label={`${employee.initialCallsPending} initial calls pending for ${employee.name}`}>{employee.initialCallsPending}<ArrowUpRight size={13} aria-hidden="true" /></Link></td>
        <td data-label="Open tasks"><Link className="staff-count-link" to={taskLink(employee.id, 'open')} aria-label={`${employee.openTasks} open tasks for ${employee.name}`}>{employee.openTasks}<ArrowUpRight size={13} aria-hidden="true" /></Link></td>
        <td className="staff-directory-attention" data-label="Needs attention"><div className="staff-attention-links">
          <Link className={employee.overdueTasks ? 'has-overdue' : ''} to={taskLink(employee.id, 'overdue')} aria-label={`${employee.overdueTasks} overdue tasks for ${employee.name}`}>{employee.overdueTasks} overdue</Link>
          <Link className={employee.escalatedTasks ? 'has-escalation' : ''} to={taskLink(employee.id, 'escalated')} aria-label={`${employee.escalatedTasks} escalated tasks for ${employee.name}`}>{employee.escalatedTasks} escalated</Link>
          <Link className={employee.missingUpdates ? 'has-missing' : ''} to={taskLink(employee.id, 'missing')} aria-label={`${employee.missingUpdates} tasks missing an update for ${employee.name}`}>{employee.missingUpdates} missing updates</Link>
        </div></td>
        <td className="staff-directory-action"><Link className="staff-link-button" to={detailHref} aria-label={`View employee record for ${employee.name}`}>View record<ChevronRight size={16} /></Link></td>
      </tr>
    })}</tbody>
  </table></div>
}

export function DirectoryFeedback({ loading, error, retry, stale = false }: { loading: boolean; error: string; retry: () => void; stale?: boolean }) {
  if (error) return <div role="alert" className="staff-directory-error"><div><strong>{stale ? 'Could not refresh staff records' : 'Could not load staff records'}</strong><p>{error}{stale && ' Displayed records may be out of date.'}</p></div><Button icon={<RefreshCw size={16} />} onClick={retry}>{error === 'The start date must be on or before the end date.' ? 'Reset dates' : 'Try again'}</Button></div>
  if (loading) return <div role="status" className="staff-directory-loading"><span className="staff-loading-dot" aria-hidden="true" />Loading employee records…</div>
  return null
}

export function DirectoryUpdated({ generatedAt, timezone }: { generatedAt: string; timezone: string }) {
  const time = new Intl.DateTimeFormat('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: timezone }).format(new Date(generatedAt))
  return <span className="staff-updated"><span aria-hidden="true" />Current workload · Updated {time}</span>
}
