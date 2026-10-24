import { useEffect, useState } from 'react'
import { KeyRound, LogOut, Pencil, UserPlus, UserX } from 'lucide-react'
import { api } from '@/shared/api/client'
import { dateTimeLabel } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import type { User } from '@/shared/model/types'
import { EmptyState } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { Sheet } from '@/shared/ui/overlay'
import { RolePill, UserAvatar, failureText } from './user-ui'

interface AuditRow {
  id: string; actorId: string | null; action: string; target: string | null
  entityType: string | null; entityId: string | null; at: string
  meta: { changes?: Record<string, { before: unknown; after: unknown }>; transfer?: { candidates: number; tasks: number } | null; revokedSessions?: number }
}
interface AuditPage { items: AuditRow[]; nextBefore: string | null }

const fieldLabels: Record<string, string> = {
  receives_auto_assign: 'Gets new candidates', on_leave: 'On leave', active: 'Account active',
  role: 'Role', name: 'Name', email: 'Email', phone: 'Phone',
}
const show = (value: unknown) => value === null || value === undefined || value === '' ? 'None' : typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value)
const toneOf = (action: string) => /deactivat/i.test(action) ? 'is-red' : /created/i.test(action) ? 'is-green' : /password|session|logout/i.test(action) ? 'is-amber' : 'is-blue'
const iconOf = (action: string) => /deactivat/i.test(action) ? <UserX size={14} /> : /created/i.test(action) ? <UserPlus size={14} />
  : /password/i.test(action) ? <KeyRound size={14} /> : /session|logout/i.test(action) ? <LogOut size={14} /> : <Pencil size={14} />

export function AuditHistory({ user, onClose }: { user: User; onClose: () => void }) {
  const store = useStore()
  const [data, setData] = useState<AuditPage>({ items: [], nextBefore: null })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    setLoading(true); setError('')
    api.get<AuditPage>('/users/' + user.id + '/audit', { signal: controller.signal }).then(setData)
      .catch((err) => { if (!controller.signal.aborted) setError(failureText(err)) })
      .finally(() => { if (!controller.signal.aborted) setLoading(false) })
    return () => controller.abort()
  }, [user.id, retry])
  const more = async () => {
    if (!data.nextBefore || loading) return
    setLoading(true); setError('')
    try {
      const next = await api.get<AuditPage>('/users/' + user.id + '/audit?before=' + encodeURIComponent(data.nextBefore))
      setData((current) => ({ items: [...current.items, ...next.items], nextBefore: next.nextBefore }))
    } catch (err) { setError(failureText(err)) }
    finally { setLoading(false) }
  }
  return <Sheet open onClose={onClose} title={'History · ' + user.name} description="Changes made to this account and things this person did, newest first." width="lg">
    <div className="uv uv-audit">
      <div className="uv-target"><UserAvatar name={user.name} /><div><p>{user.name}</p><span>{user.email}</span></div><RolePill role={user.role} /></div>
      {error && <div role="alert" className="uv-error">{error}<div className="mt-2"><Button size="sm" onClick={() => data.items.length ? void more() : setRetry((v) => v + 1)}>{data.items.length ? 'Try again' : 'Reload history'}</Button></div></div>}
      {loading && data.items.length === 0 && <div role="status" className="uv-skeleton-list" aria-label="Loading history">{[0, 1, 2, 3].map((i) => <span key={i} className="sk" />)}</div>}
      {!loading && !error && data.items.length === 0 && <EmptyState title="No activity recorded yet" description="Changes and actions will show here." />}
      {data.items.length > 0 && <ol className="uv-timeline">{data.items.map((row) => {
        const actor = row.actorId ? store.user(row.actorId)?.name || 'Unknown user' : 'System'
        const self = row.entityType !== 'user'
        return <li key={row.id} className={toneOf(row.action)}>
          <span className="uv-tl-dot" aria-hidden="true">{iconOf(row.action)}</span>
          <div className="uv-tl-head"><p>{row.action}</p><time dateTime={row.at}>{dateTimeLabel(row.at)}</time></div>
          {row.target && <p className="uv-tl-target">{self ? 'Record: ' : 'Account: '}{row.target}</p>}
          {row.meta.changes && Object.keys(row.meta.changes).length > 0 && <dl className="uv-changes">{Object.entries(row.meta.changes).map(([key, change]) => <div key={key}>
            <dt>{fieldLabels[key] ?? key.replaceAll('_', ' ')}</dt><dd><s>{show(change.before)}</s><span aria-label="changed to">to</span><b>{show(change.after)}</b></dd></div>)}</dl>}
          {row.meta.transfer && <p className="uv-tl-meta">Moved {row.meta.transfer.candidates} {row.meta.transfer.candidates === 1 ? 'candidate' : 'candidates'} and {row.meta.transfer.tasks} open {row.meta.transfer.tasks === 1 ? 'task' : 'tasks'}.</p>}
          {typeof row.meta.revokedSessions === 'number' && <p className="uv-tl-meta">{row.meta.revokedSessions} {row.meta.revokedSessions === 1 ? 'session' : 'sessions'} signed out.</p>}
          <p className="uv-tl-actor">{self ? 'Performed by ' : 'Changed by '}{actor}</p>
        </li>
      })}</ol>}
      {data.items.length > 0 && <div className="uv-audit-foot">
        <span>{data.items.length} {data.items.length === 1 ? 'entry' : 'entries'} shown{data.nextBefore ? '' : ' · beginning of history'}</span>
        {data.nextBefore && <Button disabled={loading} onClick={() => void more()}>{loading ? 'Loading…' : 'Show older'}</Button>}
      </div>}
    </div>
  </Sheet>
}
