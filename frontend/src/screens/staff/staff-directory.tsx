import { ClipboardList, Clock3, RefreshCw, UserCheck, Users } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { DirectoryFeedback, DirectoryFilters, DirectoryTable, DirectoryUpdated } from '@/features/staff-directory/components'
import { useStaffDirectory } from '@/features/staff-directory/use-staff-directory'
import '@/features/staff-directory/staff-directory.css'

export default function StaffDirectoryPage() {
  const { result, lastResult, loading, refreshing, error, params, updateParam, refresh } = useStaffDirectory()
  const view = result ?? lastResult
  const [, setParams] = useSearchParams()
  const search = params.get('search') ?? ''
  const employee = params.get('employee') ?? ''
  const availability = params.get('availability') ?? ''
  const all = view?.staff ?? []
  const term = search.trim().toLocaleLowerCase()
  const rows = all.filter(person => (!employee || person.id === employee) && (!availability || person.availability.status === availability) && (!term || `${person.name} ${person.email}`.toLocaleLowerCase().includes(term)))
  const total = (key: 'openTasks' | 'overdueTasks') => rows.reduce((sum, row) => sum + row[key], 0)
  const filter = (name: string, value: string) => {
    if (name !== 'clear') return updateParam(name, value)
    setParams(previous => { const next = new URLSearchParams(previous); for (const key of ['search', 'employee', 'availability']) next.delete(key); return next }, { replace: true })
  }

  return <div className="staff-directory-workspace">
    <PageHeader eyebrow="Team & operations" title="Staff directory" description="Find your people, understand their workload and follow up on what needs attention." actions={<Button disabled={loading || refreshing} icon={<RefreshCw size={16} className={loading || refreshing ? 'animate-spin' : ''} />} onClick={refresh}>{loading || refreshing ? 'Refreshing…' : 'Refresh'}</Button>} />
    <DirectoryFeedback loading={loading && !view} error={error} retry={refresh} stale={!!view} />
    {view && <>
      <SummaryCards items={[
        { label: 'Employees', value: rows.length, detail: 'In the current view', icon: Users },
        { label: 'Available for intake', value: rows.filter(row => row.availability.eligible).length, detail: 'Receiving automatic assignments', icon: UserCheck },
        { label: 'Open tasks', value: total('openTasks'), detail: 'Current assigned workload', icon: ClipboardList },
        { label: 'Overdue tasks', value: total('overdueTasks'), detail: 'Open tasks past their deadline', icon: Clock3 },
      ]} />
      <Panel flush className="staff-directory-panel">
        <header className="staff-panel-heading"><div><h2>Team overview</h2><p>Open an employee record or select a count to see the matching work.</p></div><DirectoryUpdated generatedAt={view.generatedAt} timezone={view.timezone} /></header>
        <DirectoryFilters employees={all} search={search} employee={employee} availability={availability} onChange={filter} />
        {!rows.length ? <EmptyState title={all.length ? 'No employees match these filters' : 'No employees yet'} description={all.length ? 'Try another name, employee or availability.' : 'Employee accounts will appear here once they have been created.'} action={all.length ? <Button onClick={() => filter('clear', '')}>Clear filters</Button> : undefined} /> : <DirectoryTable employees={rows} query={params.toString()} />}
        <footer className="staff-directory-note"><p><strong>Automatic intake</strong> shows who can receive new candidates through the recruitment rotation. It does not indicate whether someone is online or checked in.</p><p>Workload counts reflect current ownership. Open an employee record to view completed work for a selected period.</p></footer>
      </Panel>
    </>}
  </div>
}
