import { ArrowLeft, ArrowUpRight, CalendarDays, CheckCheck, ClipboardList, Clock3, FileBarChart, Mail, Phone, RefreshCw, Users } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { AvailabilityBadge, DirectoryFeedback, DirectoryUpdated, EmployeeIdentity } from '@/features/staff-directory/components'
import { candidateLink, completionLabel, periodLabel, reportLink, taskLink } from '@/features/staff-directory/model'
import { useStaffDirectory } from '@/features/staff-directory/use-staff-directory'
import '@/features/staff-directory/staff-directory.css'

export default function StaffDetailPage() {
  const { id } = useParams()
  const { me } = useStore()
  const { result, lastResult, loading, refreshing, error, params, period, updateParam, refresh } = useStaffDirectory()
  const view = result ?? lastResult
  const employee = view?.staff.find(person => person.id === id)
  const back = `/staff-directory${params.size ? '?' + params.toString() : ''}`
  const canSeeAttendance = employee && employee.role !== 'admin' && (me.role === 'admin' || employee.role === 'staff' || employee.id === me.id)
  const attendance = `/attendance?${new URLSearchParams({ staff: id ?? '', month: period.to.slice(0, 7), view: 'employee' })}`

  return <div className="staff-directory-workspace staff-detail-workspace">
    <Link to={back} className="staff-back-link"><ArrowLeft size={17} />Back to staff directory</Link>
    <PageHeader eyebrow="Employee record" title={employee?.name ?? 'Employee details'} description="Current responsibilities, availability and completed work." actions={<div className="staff-detail-actions">{canSeeAttendance && <Link className="staff-link-button" to={attendance}><CalendarDays size={17} />Attendance</Link>}<Button disabled={loading || refreshing} icon={<RefreshCw size={16} className={loading || refreshing ? 'animate-spin' : ''} />} onClick={refresh}>{loading || refreshing ? 'Refreshing…' : 'Refresh'}</Button></div>} />
    <DirectoryFeedback loading={loading && !view} error={error} retry={refresh} stale={!!view} />
    {result && !employee && <Panel><EmptyState title="Employee not found" description="This employee is not available in your directory. Return to the team overview to choose another person." action={<Link className="staff-link-button" to={back}>Back to staff directory</Link>} /></Panel>}
    {employee && view && <>
      <Panel className="staff-profile-card"><EmployeeIdentity employee={employee} /><div className="staff-profile-contact"><a href={`mailto:${employee.email}`}><Mail size={16} />{employee.email}</a>{employee.phone && <a href={`tel:${employee.phone}`}><Phone size={16} />{employee.phone}</a>}</div><div className="staff-profile-availability"><span>Automatic intake</span><AvailabilityBadge employee={employee} />{employee.availability.status === 'paused' && <small>Manual reassignment is still available.</small>}</div></Panel>
      <div className="staff-section-title"><div><h2>Current workload</h2><p>Assigned work right now, independent of the performance dates below.</p></div><DirectoryUpdated generatedAt={view.generatedAt} timezone={view.timezone} /></div>
      <SummaryCards items={[
        { label: 'Assigned candidates', value: <Link to={candidateLink(employee.id)} aria-label={`View ${employee.assignedCandidates} assigned candidates`}>{employee.assignedCandidates}<ArrowUpRight size={19} /></Link>, detail: 'Candidates currently owned', icon: Users },
        { label: 'Initial calls pending', value: <Link to={taskLink(employee.id, 'initial')} aria-label={`View ${employee.initialCallsPending} initial calls pending`}>{employee.initialCallsPending}<ArrowUpRight size={19} /></Link>, detail: 'Awaiting their first contact', icon: Phone },
        { label: 'Open tasks', value: <Link to={taskLink(employee.id, 'open')} aria-label={`View ${employee.openTasks} open tasks`}>{employee.openTasks}<ArrowUpRight size={19} /></Link>, detail: 'All unfinished assigned tasks', icon: ClipboardList },
        { label: 'Overdue tasks', value: <Link to={taskLink(employee.id, 'overdue')} aria-label={`View ${employee.overdueTasks} overdue tasks`}>{employee.overdueTasks}<ArrowUpRight size={19} /></Link>, detail: 'Past the task deadline', icon: Clock3 },
      ]} />
      <div className="staff-workload-panels">
        <Panel flush className="staff-directory-panel"><header className="staff-panel-heading"><div><h2>Needs attention</h2><p>Choose a queue to review its current tasks.</p></div></header><div className="staff-queue-list">
          <Link to={taskLink(employee.id, 'overdue')}><span><strong>Overdue tasks</strong><small>Past their deadline and still open</small></span><b className={employee.overdueTasks ? 'staff-warning' : ''}>{employee.overdueTasks}</b><ArrowUpRight size={18} /></Link>
          <Link to={taskLink(employee.id, 'escalated')}><span><strong>Escalated tasks</strong><small>Marked for a supervisor’s attention</small></span><b className={employee.escalatedTasks ? 'staff-warning' : ''}>{employee.escalatedTasks}</b><ArrowUpRight size={18} /></Link>
          <Link to={taskLink(employee.id, 'missing')}><span><strong>Missing daily updates</strong><small>Open tasks without the required update</small></span><b className={employee.missingUpdates ? 'staff-warning' : ''}>{employee.missingUpdates}</b><ArrowUpRight size={18} /></Link>
        </div></Panel>
        <Panel className="staff-assignment-card"><span className="staff-small-eyebrow">Candidate ownership</span><h2>Keep work moving</h2><p>Review assigned candidates and select the records that need to move to another recruiter.</p><Link className="staff-link-button" to={candidateLink(employee.id)}>Manage assigned candidates<ArrowUpRight size={17} /></Link><p className="staff-assignment-note">Reassignment requires a reason and keeps the candidate’s history and task deadlines.</p></Panel>
      </div>
      <Panel flush className="staff-directory-panel staff-performance-panel"><header className="staff-panel-heading"><div><h2>Performance in selected period</h2><p>Dates apply to completed tasks and their completion time only.</p></div></header>
        <div className="staff-performance-filters"><Field label="From date"><Input type="date" value={period.from} max={period.to} required onChange={event => { if (event.target.value) updateParam('from', event.target.value) }} /></Field><Field label="To date"><Input type="date" value={period.to} min={period.from} required onChange={event => { if (event.target.value) updateParam('to', event.target.value) }} /></Field><Link className="staff-link-button" to={reportLink(employee.id, period)}><FileBarChart size={17} />View staff report</Link></div>
        {result ? <div className="staff-performance-cards"><Link to={taskLink(employee.id, 'completed', period)} className="staff-performance-stat"><span><CheckCheck size={18} />Completed tasks<ArrowUpRight size={16} /></span><strong>{employee.completedTasks}</strong><small>Completed from {periodLabel(period)}</small></Link><div className="staff-performance-stat"><span><Clock3 size={18} />Average task completion time</span><strong>{completionLabel(employee.averageCompletionHours)}</strong><small>{employee.averageCompletionHours == null ? 'No completed tasks with a measurable duration' : 'Time from task creation to completion'}</small></div></div> : <p role="status" className="staff-directory-note">{error ? 'Performance is unavailable for the selected period. Update the dates or try again.' : 'Loading performance for the selected dates…'}</p>}
        <p className="staff-directory-note">Includes initial calls and recruitment tasks completed in this period. Current candidate and task ownership above does not change when these dates change. Dates use {view.timezone}.</p>
      </Panel>
    </>}
  </div>
}
