import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { liveInterval } from '@/shared/state/live'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'

const STATUS_WORDS: Record<string, string> = { queued: 'Waiting to start', running: 'Importing', limited: 'Batch finished', paused: 'Paused', error: 'Stopped by a problem', done: 'Finished', completed: 'Finished' }
const statusWord = (status: string) => STATUS_WORDS[status] ?? status.replaceAll('_', ' ')

interface Progress { id: string; since: string; status: string; processed: number; imported: number; max_count: number; auto_ingest: boolean; last_error?: string }

export function HistoryImport({ accountId, disabled }: { accountId: string; disabled: boolean }) {
  const store = useStore()
  const [since, setSince] = useState(() => new Date(Date.now() - 30 * 86400000).toISOString().slice(0, 10))
  const [limit, setLimit] = useState(100)
  const [auto, setAuto] = useState(false)
  const [progress, setProgress] = useState<Progress | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  useEffect(() => {
    let live = true
    const read = async () => {
      try {
        const value = await api.get<Progress | null>(`/inbox/accounts/${accountId}/history`)
        if (live && store.isCurrentSession()) setProgress(value)
      } catch (cause) { if (live) setError(cause instanceof Error ? cause.message : 'We could not load the import status.') }
    }
    void read()
    const stopLive = liveInterval(() => { if (document.visibilityState === 'visible') void read() }, 5000, ['mail'])
    return () => { live = false; stopLive() }
  }, [accountId, store.isCurrentSession])
  const act = async (action: string, body?: unknown) => {
    setBusy(true); setError('')
    try {
      const value = await api.post<Progress>(`/inbox/accounts/${accountId}/history${action}`, body)
      if (store.isCurrentSession()) setProgress(value)
    } catch (cause) { if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'We are not sure that worked. Check the import status before you try again.') }
    finally { setBusy(false) }
  }
  const active = progress && ['queued', 'running'].includes(progress.status)
  return <details className="rounded-control border border-rule bg-field/30 p-3">
    <summary className="cursor-pointer text-sm font-medium">Import older mail {progress ? `· ${statusWord(progress.status)}` : ''}</summary>
    <div className="mt-3 space-y-3">
      <p className="text-xs text-ink-600">Bring in a batch of older mail from this mailbox. New mail keeps coming in as usual. Original attachments are kept. Attachments are only read for candidates if you switch that on below.</p>
      {progress && <p role="status" className="text-xs text-ink-700">{statusWord(progress.status)}: {progress.processed} emails checked, {progress.imported} added. Batch limit {progress.max_count}. From {progress.since}.{progress.auto_ingest ? ' Attachments will be read.' : ' Attachments will not be read.'}</p>}
      {(error || progress?.last_error) && <p role="alert" className="text-xs text-stamp-700">{error || progress?.last_error}</p>}
      <Field label="Import mail received since"><Input type="date" value={since} max={new Date().toISOString().slice(0, 10)} disabled={busy || !!active || disabled} onChange={(event) => setSince(event.target.value)} /></Field>
      <Field label="Most emails to import at once"><Select value={limit} disabled={busy || !!active || disabled} onChange={(event) => setLimit(Number(event.target.value))}><option value={50}>50 messages</option><option value={100}>100 messages</option><option value={200}>200 messages</option><option value={500}>500 messages</option></Select></Field>
      <Switch checked={auto} onChange={setAuto} disabled={busy || !!active || disabled} label="Read attachments in imported mail" description="Read the attachments in this older batch and create candidates from them." />
      <div className="flex flex-wrap gap-2">
        {active ? <Button size="sm" disabled={busy || disabled} onClick={() => void act('/pause')}>Pause import</Button> : <>
          {progress && ['limited', 'paused', 'error'].includes(progress.status) && <Button size="sm" disabled={busy || disabled} onClick={() => void act('/resume', { limit })}>{progress.status === 'limited' ? 'Import next batch' : 'Continue import'}</Button>}
          <Button size="sm" disabled={busy || disabled || !since} onClick={() => void act('', { since, limit, auto_ingest: auto })}>{progress ? 'Import another date range' : 'Import older mail'}</Button>
        </>}
      </div>
    </div>
  </details>
}
