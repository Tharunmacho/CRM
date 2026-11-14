import { useCallback, useEffect, useState } from 'react'
import { Download, FileSpreadsheet, Eye, Loader2, Trash2 } from 'lucide-react'
import { liveInterval } from '@/shared/state/live'
import { EmptyState } from '@/shared/ui/display'
import { Button } from '@/shared/ui/primitives'
import { DbTable, useElementWidth, type DbColumn } from '@/features/candidates/components/db-table'
import { Pager } from '@/features/candidates/components/db-parts'
import { downloadBatchReport, leadApi, type UploadDeletionResult } from '../lead-api'
import { createdParts, formatBytes, type LeadBatch } from '../lead-model'
import { DeleteUploadDialog } from './delete-upload-dialog'

interface Props {
  supervisor: boolean
  canAdmin: boolean
  onViewBatch: (batchId: string) => void
  /** Re-read when this changes (a new upload finished). */
  refreshKey: number
  onUpload?: () => void
  onDeleted: (result: UploadDeletionResult) => void
}

/** Every upload: which file, who uploaded it, when, and how many leads were new, duplicates or invalid. */
export function ImportHistory({ supervisor, canAdmin, onViewBatch, refreshKey, onUpload, onDeleted }: Props) {
  const [data, setData] = useState<{ items: LeadBatch[]; total: number } | null>(null)
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(20)
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(true)
  const [busyId, setBusyId] = useState('')
  const [deleting, setDeleting] = useState<LeadBatch | null>(null)
  const [boxRef, boxWidth] = useElementWidth<HTMLDivElement>()

  const load = useCallback(async (signal?: AbortSignal) => {
    try {
      const result = await leadApi.batches(page, pageSize, signal)
      if (signal?.aborted) return
      const lastPage = Math.max(0, Math.ceil(result.total / pageSize) - 1)
      if (page > lastPage) { setPage(lastPage); return }
      setData({ items: result.items, total: result.total }); setError('')
    } catch (e) {
      if (!signal?.aborted) setError(e instanceof Error ? e.message : 'We could not load the uploads.')
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    const controller = new AbortController()
    setLoading(true)
    void load(controller.signal)
    const stop = liveInterval(() => load(controller.signal), 120_000, ['leads'], { debounceMs: 800 })
    return () => { controller.abort(); stop() }
  }, [load, refreshKey])

  const report = async (batch: LeadBatch) => {
    setBusyId(batch.id); setError('')
    try { await downloadBatchReport(batch.id) } catch (e) { setError(e instanceof Error ? e.message : 'We could not make the report.') } finally { setBusyId('') }
  }

  const actions = (batch: LeadBatch) => <span className="ld-batch-acts">
    <Button variant="secondary" icon={<Eye className="h-4 w-4" />} onClick={() => onViewBatch(batch.id)}>View</Button>
    {supervisor && <Button variant="secondary" icon={busyId === batch.id ? <Loader2 className="ld-spin h-4 w-4" /> : <Download className="h-4 w-4" />} disabled={busyId === batch.id} onClick={() => void report(batch)}>Report (Excel)</Button>}
    {canAdmin && <Button variant="secondary" icon={<Trash2 className="h-4 w-4" />} aria-label={`Permanently delete upload ${batch.fileName}`} onClick={() => setDeleting(batch)}>Delete upload</Button>}
  </span>

  const columns: DbColumn<LeadBatch>[] = [
    {
      key: 'file', header: 'File', width: 260, align: 'left',
      render: (b) => (
        <span className="ld-file-cell" title={b.fileName}>
          <span className="ld-file-icon is-small" aria-hidden="true"><FileSpreadsheet size={16} /></span>
          <span className="ld-file-text"><strong>{b.fileName}</strong><small>{[b.fileSize ? formatBytes(b.fileSize) : '', b.format?.toUpperCase(), b.campaignLabel].filter(Boolean).join(' · ') || ' '}</small></span>
        </span>
      ),
    },
    { key: 'by', header: 'Uploaded by', width: 140, render: (b) => <span className="db-clamp">{b.uploadedByName || '–'}</span> },
    {
      key: 'when', header: 'Uploaded', width: 112,
      render: (b) => { const parts = createdParts(b.createdAt); return parts ? <span className="db-2l" title={new Date(b.createdAt).toLocaleString()}><strong className="db-num">{parts.day}</strong><small>{parts.year}</small></span> : <span className="db-muted">–</span> },
    },
    { key: 'total', header: 'Rows', width: 80, render: (b) => <span className="tnum db-num">{b.rowsTotal.toLocaleString()}</span> },
    { key: 'new', header: 'New', width: 80, render: (b) => <span className="db-tone is-ok tnum">{b.rowsNew.toLocaleString()}</span> },
    { key: 'dup', header: 'Duplicates', width: 96, render: (b) => <span className={`db-tone tnum ${b.rowsDuplicate ? 'is-warn' : 'is-quiet'}`}>{b.rowsDuplicate.toLocaleString()}</span> },
    { key: 'invalid', header: 'Invalid', width: 80, render: (b) => <span className={`db-tone tnum ${b.rowsInvalid ? 'is-bad' : 'is-quiet'}`}>{b.rowsInvalid.toLocaleString()}</span> },
    {
      key: 'actions', header: 'Actions', width: canAdmin ? 370 : 220, sticky: 'end', render: actions,
    },
  ]

  let content
  if (loading && !data) content = <p role="status" className="ld-loading">Loading uploads…</p>
  else if (error && !data) content = <EmptyState title="We could not load the uploads" description={error} action={<Button variant="primary" onClick={() => void load()}>Try again</Button>} />
  else if (!data || data.items.length === 0) content = <EmptyState title="No uploads yet" description="When you upload a lead sheet it is listed here, with how many leads were new, duplicates or invalid." action={onUpload ? <Button variant="primary" onClick={onUpload}>Upload leads</Button> : undefined} />
  else content = <>
    {error && <p role="alert" className="db-alert">{error}</p>}
    {boxWidth > 0 && boxWidth < 800 ? <ul className="ld-upload-cards" aria-label="Uploads">
      {data.items.map(batch => <li key={batch.id}>
        <h3>{batch.fileName}</h3>
        <p>{batch.uploadedByName || 'Recruiter'} · {new Date(batch.createdAt).toLocaleDateString()}</p>
        <dl><div><dt>New leads</dt><dd>{batch.rowsNew.toLocaleString()}</dd></div><div><dt>Duplicates</dt><dd>{batch.rowsDuplicate.toLocaleString()}</dd></div><div><dt>Invalid</dt><dd>{batch.rowsInvalid.toLocaleString()}</dd></div></dl>
        {actions(batch)}
      </li>)}
    </ul> : <div className="db-box ld-batches"><DbTable<LeadBatch> variant="grid" label="Uploads" rows={data.items} columns={columns} rowKey={batch => batch.id} /></div>}
    <Pager page={page} pageSize={pageSize} total={data.total} noun="uploads" onPage={setPage} onPageSize={size => { setPageSize(size); setPage(0) }} />
  </>
  return (
    <>
      <div ref={boxRef}>{content}</div>
      {canAdmin && deleting && <DeleteUploadDialog key={deleting.id} batch={deleting} onClose={() => setDeleting(null)} onRefresh={() => { setDeleting(null); void load() }} onDeleted={result => {
        setDeleting(null); setPage(0)
        setData(current => current ? { items: current.items.filter(batch => batch.id !== result.batchId), total: Math.max(0, current.total - 1) } : null)
        onDeleted(result)
      }} />}
    </>
  )
}
