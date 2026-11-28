import { ClipboardList, Clock3, RefreshCw, UserCheck, Users } from 'lucide-react'
import { useSearchParams } from 'react-router-dom'
import { Button } from '@/shared/ui/primitives'
import { EmptyState, PageHeader, Panel } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { DirectoryCards, DirectoryFeedback, DirectoryFilters, DirectoryTable, DirectoryUpdated, SectionHead, ViewToggle, useDirectoryView } from '@/features/staff-directory/components'
import { useStaffDirectory } from '@/features/staff-directory/use-staff-directory'
import '@/features/staff-directory/staff-directory.css'
import '@/styles/table-theme.css'

export default function StaffDirectoryPage() {
  const { result, lastResult, loading, refreshing, error, params, updateParam, refresh } = useStaffDirectory()
  const [directoryView, setDirectoryView] = useDirectoryView()
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
    <PageHeader title="Recruiter directory" actions={<Button disabled={loading || refreshing} icon={<RefreshCw size={16} className={loading || refreshing ? 'animate-spin' : ''} />} onClick={refresh}>{loading || refreshing ? 'Refreshing…' : 'Refresh'}</Button>} />
    <DirectoryFeedback loading={loading && !view} error={error} retry={refresh} stale={!!view} />
    {view && <>
      <SummaryCards items={[
        { label: 'Employees', value: rows.length, detail: 'Shown now', icon: Users },
        { label: 'Getting new candidates', value: rows.filter(row => row.availability.eligible).length, detail: 'Included when new candidates are shared out', icon: UserCheck, tone: 'green' },
        { label: 'Open tasks', value: total('openTasks'), detail: 'Work they have now', icon: ClipboardList },
        { label: 'Overdue tasks', value: total('overdueTasks'), detail: 'Open tasks past their deadline', icon: Clock3, tone: 'red' },
      ]} />
      <Panel flush className="staff-card-panel tb-blue">
        <SectionHead title="Team overview" action={<><DirectoryUpdated generatedAt={view.generatedAt} timezone={view.timezone} /><ViewToggle view={directoryView} onChange={setDirectoryView} /></>} />
        <DirectoryFilters employees={all} search={search} employee={employee} availability={availability} onChange={filter} />
        {!rows.length
          ? <EmptyState title={all.length ? 'No employees match these filters' : 'No employees yet'} description={all.length ? 'Try another name, employee or setting.' : 'Employees will show here once they have been added.'} action={all.length ? <Button onClick={() => filter('clear', '')}>Clear filters</Button> : undefined} />
          : directoryView === 'cards' ? <DirectoryCards employees={rows} query={params.toString()} /> : <DirectoryTable employees={rows} query={params.toString()} />}
      </Panel>
    </>}
  </div>
}
