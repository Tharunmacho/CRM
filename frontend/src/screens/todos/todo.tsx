import { AlertCircle, RefreshCw } from 'lucide-react'
import { PageHeader, Panel } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { useWorkspaceRead } from '@/features/staff/use-workspace-read'
import { DailyWorksheet } from './worksheet'
import '@/styles/todo-v2.css'

export default function TodoPage() {
  const todoRead = useWorkspaceRead()
  if (todoRead.loading || todoRead.error) return <div className="todo-screen td">
    <PageHeader title="To Do" />
    {todoRead.error ? (
      <Panel className="td-state"><div className="td-state-body">
        <AlertCircle size={20} aria-hidden="true" />
        <div>
          <p role="alert" className="td-state-title">We could not load To Do</p>
          <p className="td-state-text">{todoRead.error}</p>
        </div>
        <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Try again</Button>
      </div></Panel>
    ) : (
      <div className="td-skeleton" role="status" aria-label="Loading To Do">
        <div className="td-skel-tiles">{[0, 1, 2, 3].map((i) => <span key={i} />)}</div>
        <div className="td-skel-body"><span /><span /></div>
      </div>
    )}
  </div>

  return (
    <div className="todo-screen td">
      <PageHeader
        title="To Do"
        actions={
          <>
            <Button icon={<RefreshCw />} onClick={todoRead.refresh}>Refresh</Button>

          </>
        }
      />

      <DailyWorksheet />
    </div>
  )
}
