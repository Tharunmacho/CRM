import { ArrowLeft, ArrowUpRight, CalendarDays, CheckCheck, ClipboardList, Clock3, FileBarChart, Mail, Phone, RefreshCw, UserCog, Users } from 'lucide-react'
import { Link, useParams } from 'react-router-dom'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { AvailabilityBadge, DirectoryFeedback, DirectoryUpdated, EmployeeIdentity, SectionHead } from '@/features/staff-directory/components'
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
  const queues = employee ? [
    { to: taskLink(employee.id, 'overdue'), title: 'Overdue tasks', hint: 'Past their deadline and still open', value: employee.overdueTasks, tone: 'overdue' },
    { to: taskLink(employee.id, 'escalated'), title: 'Very overdue tasks', hint: 'Late for a long time and sent to a supervisor', value: employee.escalatedTasks, tone: 'escalated' },
    { to: taskLink(employee.id, 'missing'), title: 'Daily updates not done', hint: 'Open tasks that still need today’s update', value: employee.missingUpdates, tone: 'missing' },
  ] : []

  return <div className="staff-directory-workspace staff-detail-workspace">
    <Link to={back} className="staff-back-link"><ArrowLeft size={16} />Back to staff directory</Link>
    <PageHeader title={employee?.name ?? 'Employee details'} description="The work they have now, whether they get new candidates, and the work they finished." actions={<>{canSeeAttendance && <Link className="staff-link-button" to={attendance}><CalendarDays size={16} />Attendance</Link>}<Button disabled={loading || refreshing} icon={<RefreshCw size={16} className={loading || refreshing ? 'animate-spin' : ''} />} onClick={refresh}>{loading || refreshing ? 'Refreshing…' : 'Refresh'}</Button></>} />
    <DirectoryFeedback loading={loading && !view} error={error} retry={refresh} stale={!!view} />
    {result && !employee && <Panel flush><EmptyState title="Employee not found" description="We cannot find this employee in your list. Go back to the staff directory and pick someone else." action={<Link className="staff-link-button" to={back}>Back to staff directory</Link>} /></Panel>}
    {employee && view && <>
      <Panel flush className="staff-profile-card">
        <EmployeeIdentity employee={employee} large />
        <div className="staff-profile-contact"><span className="staff-profile-label">Contact</span><a href={`mailto:${employee.email}`}><Mail size={15} />{employee.email}</a>{employee.phone && <a href={`tel:${employee.phone}`}><Phone size={15} />{employee.phone}</a>}</div>
        <div className="staff-profile-availability"><span className="staff-profile-label">New candidates</span><AvailabilityBadge employee={employee} />{employee.availability.status === 'paused' && <small>You can still give them candidates by hand.</small>}</div>
      </Panel>
      <div className="staff-section-title"><div><h2>Current workload</h2><p>The work they have right now. It does not depend on the dates below.</p></div><DirectoryUpdated generatedAt={view.generatedAt} timezone={view.timezone} /></div>
      <SummaryCards items={[
        { label: 'Assigned candidates', value: <Link to={candidateLink(employee.id)} aria-label={`View ${employee.assignedCandidates} assigned candidates`}>{employee.assignedCandidates}<ArrowUpRight size={18} /></Link>, detail: 'Candidates they look after', icon: Users },
        { label: 'Initial calls pending', value: <Link to={taskLink(employee.id, 'initial')} aria-label={`View ${employee.initialCallsPending} initial calls pending`}>{employee.initialCallsPending}<ArrowUpRight size={18} /></Link>, detail: 'Waiting for the first call', icon: Phone },
        { label: 'Open tasks', value: <Link to={taskLink(employee.id, 'open')} aria-label={`View ${employee.openTasks} open tasks`}>{employee.openTasks}<ArrowUpRight size={18} /></Link>, detail: 'All tasks not yet done', icon: ClipboardList },
        { label: 'Overdue tasks', value: <Link to={taskLink(employee.id, 'overdue')} aria-label={`View ${employee.overdueTasks} overdue tasks`}>{employee.overdueTasks}<ArrowUpRight size={18} /></Link>, detail: 'Past the task deadline', icon: Clock3, tone: 'red' },
      ]} />
      <div className="staff-workload-panels">
        <Panel flush className="staff-card-panel"><SectionHead title="Needs attention" meta="Pick a list to see those tasks." /><div className="staff-queue-list">
          {queues.map((queue) => <Link key={queue.title} to={queue.to}><span><strong>{queue.title}</strong><small>{queue.hint}</small></span><b className="staff-tag" data-tone={queue.value ? queue.tone : undefined}>{queue.value}</b><ArrowUpRight size={17} aria-hidden="true" /></Link>)}
        </div></Panel>
        <Panel className="staff-ownership"><span className="staff-ownership-icon" aria-hidden="true"><UserCog size={20} /></span><h2>Keep work moving</h2><p>Look at this person’s candidates and choose the ones to move to another recruiter.</p><Link className="staff-link-button is-primary" to={candidateLink(employee.id)}>Manage assigned candidates<ArrowUpRight size={16} /></Link><small>You must give a reason to move a candidate. Their history and task deadlines stay the same.</small></Panel>
      </div>
      <Panel flush className="staff-card-panel"><SectionHead title="Finished work in the chosen dates" meta="These dates only change the finished tasks and the time taken." />
        <div className="staff-period" role="group" aria-label="Dates for finished work"><Field label="From date"><Input type="date" value={period.from} max={period.to} required onChange={event => { if (event.target.value) updateParam('from', event.target.value) }} /></Field><Field label="To date"><Input type="date" value={period.to} min={period.from} required onChange={event => { if (event.target.value) updateParam('to', event.target.value) }} /></Field><Link className="staff-link-button" to={reportLink(employee.id, period)}><FileBarChart size={16} />View staff report</Link></div>
        {result ? <div className="staff-perf"><Link to={taskLink(employee.id, 'completed', period)} className="staff-perf-stat"><span><CheckCheck size={17} />Completed tasks<ArrowUpRight size={15} /></span><strong>{employee.completedTasks}</strong><small>Completed from {periodLabel(period)}</small></Link><div className="staff-perf-stat"><span><Clock3 size={17} />Average time to finish a task</span><strong>{completionLabel(employee.averageCompletionHours)}</strong><small>{employee.averageCompletionHours == null ? 'No finished tasks to measure' : 'Time from when a task was made to when it was done'}</small></div></div> : <p role="status" className="staff-note">{error ? 'We cannot show this for the chosen dates. Change the dates or try again.' : 'Loading finished work for these dates…'}</p>}
        <p className="staff-note">Includes first calls and recruitment tasks finished in these dates. The numbers above do not change when you change the dates. Dates use {view.timezone}.</p>
      </Panel>
    </>}
  </div>
}
