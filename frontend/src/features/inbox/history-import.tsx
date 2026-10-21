import { useEffect, useState } from 'react'
import { api } from '@/shared/api/client'
import { useStore } from '@/shared/state/crm-store'
import { Button, Field, Input, Select, Switch } from '@/shared/ui/primitives'

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
      } catch (cause) { if (live) setError(cause instanceof Error ? cause.message : 'History status could not load.') }
    }
    void read()
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') void read() }, 5000)
    return () => { live = false; window.clearInterval(timer) }
  }, [accountId, store.isCurrentSession])
  const act = async (action: string, body?: unknown) => {
    setBusy(true); setError('')
    try {
      const value = await api.post<Progress>(`/inbox/accounts/${accountId}/history${action}`, body)
      if (store.isCurrentSession()) setProgress(value)
    } catch (cause) { if (store.isCurrentSession()) setError(cause instanceof Error ? cause.message : 'The import change could not be confirmed. Check its status before retrying.') }
    finally { setBusy(false) }
  }
  const active = progress && ['queued', 'running'].includes(progress.status)
  return <details className="rounded-control border border-rule bg-field/30 p-3">
    <summary className="cursor-pointer text-sm font-medium">Import older mail {progress ? `· ${progress.status}` : ''}</summary>
    <div className="mt-3 space-y-3">
      <p className="text-xs text-ink-600">Import a bounded batch from the saved mailbox history. New mail continues to sync. Original attachments are retained; OCR stays off unless you enable it below.</p>
      {progress && <p role="status" className="text-xs text-ink-700">{progress.status}: {progress.processed} examined, {progress.imported} added. Current batch limit {progress.max_count}. Since {progress.since}.{progress.auto_ingest ? ' Attachment processing enabled.' : ' Attachment processing off.'}</p>}
      {(error || progress?.last_error) && <p role="alert" className="text-xs text-stamp-700">{error || progress?.last_error}</p>}
      <Field label="Import messages received since"><Input type="date" value={since} max={new Date().toISOString().slice(0, 10)} disabled={busy || !!active || disabled} onChange={(event) => setSince(event.target.value)} /></Field>
      <Field label="Maximum messages per batch"><Select value={limit} disabled={busy || !!active || disabled} onChange={(event) => setLimit(Number(event.target.value))}><option value={50}>50 messages</option><option value={100}>100 messages</option><option value={200}>200 messages</option><option value={500}>500 messages</option></Select></Field>
      <Switch checked={auto} onChange={setAuto} disabled={busy || !!active || disabled} label="Process imported attachments" description="Explicitly queue candidate document extraction for this historical batch." />
      <div className="flex flex-wrap gap-2">
        {active ? <Button size="sm" disabled={busy || disabled} onClick={() => void act('/pause')}>Pause history import</Button> : <>
          {progress && ['limited', 'paused', 'error'].includes(progress.status) && <Button size="sm" disabled={busy || disabled} onClick={() => void act('/resume', { limit })}>{progress.status === 'limited' ? 'Import next batch' : 'Resume history import'}</Button>}
          <Button size="sm" disabled={busy || disabled || !since} onClick={() => void act('', { since, limit, auto_ingest: auto })}>{progress ? 'Start another date range' : 'Import historical mail'}</Button>
        </>}
      </div>
    </div>
  </details>
}
