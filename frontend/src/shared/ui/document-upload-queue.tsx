import { useEffect, useRef, useState } from 'react'
import { Check, Loader2, RotateCcw, TriangleAlert, X } from 'lucide-react'
import { documentFileError } from '@/shared/lib/document-files'
import '@/styles/document-upload-queue.css'

type UploadItem = { id: number; file: File; status: 'waiting' | 'uploading' | 'done' | 'failed'; error: string }

/** One request at a time. Retrying a batch only sends entries that have not succeeded. */
export function useDocumentUploadQueue(upload: ((file: File) => void | Promise<void>) | undefined, options: { multiple?: boolean; disabled?: boolean; validate?: (file: File) => string | null | undefined } = {}) {
  const [items, setItems] = useState<UploadItem[]>([])
  const [busy, setBusy] = useState(false)
  const rows = useRef<UploadItem[]>([])
  const sequence = useRef(0)
  const running = useRef(false)
  const alive = useRef(true)
  const current = useRef({ upload, options }); current.current = { upload, options }
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])
  const publish = () => { if (alive.current) setItems([...rows.current]) }
  const process = async (chosen: UploadItem[]) => {
    if (running.current || !current.current.upload || current.current.options.disabled) return
    running.current = true; setBusy(true)
    try {
      for (const item of chosen) {
        if (!alive.current) break
        const invalid = documentFileError(item.file) || current.current.options.validate?.(item.file)
        if (invalid) { item.status = 'failed'; item.error = invalid; publish(); continue }
        item.status = 'uploading'; item.error = ''; publish()
        try { await current.current.upload?.(item.file); item.status = 'done' }
        catch (cause) { item.status = 'failed'; item.error = cause instanceof Error ? cause.message : 'The file was not uploaded. Retry this file.' }
        publish()
      }
    } finally { running.current = false; if (alive.current) setBusy(false) }
  }
  const take = async (picked: FileList | readonly File[] | null) => {
    if (!picked || running.current || !current.current.upload || current.current.options.disabled) return
    const files = Array.from(picked)
    const chosen = (current.current.options.multiple ? files : files.slice(0, 1)).map((file) => ({ id: ++sequence.current, file, status: 'waiting' as const, error: '' }))
    if (!chosen.length) return
    rows.current = [...rows.current.filter((item) => item.status === 'failed'), ...chosen]; publish()
    await process(chosen)
  }
  return { items, busy, take, retry: () => process(rows.current.filter((item) => item.status === 'failed')), clear: () => { if (!running.current) { rows.current = []; publish() } } }
}

export function DocumentUploadQueue({ queue }: { queue: ReturnType<typeof useDocumentUploadQueue> }) {
  if (!queue.items.length) return null
  const done = queue.items.filter((item) => item.status === 'done').length
  const failed = queue.items.filter((item) => item.status === 'failed').length
  return <div className="document-upload-queue" aria-label="File upload progress" aria-busy={queue.busy}>
    <header><span role="status">{done} of {queue.items.length} {queue.items.length === 1 ? 'file uploaded' : 'files uploaded'}</span>{!queue.busy && <button type="button" onClick={queue.clear} aria-label="Dismiss upload progress"><X aria-hidden="true" /></button>}</header>
    {queue.busy && <progress max={queue.items.length} value={queue.items.filter((item) => item.status === 'done' || item.status === 'failed').length} aria-label="Files processed" />}
    <ul>{queue.items.map((item) => <li key={item.id} className={`is-${item.status}`}>
      {item.status === 'done' ? <Check aria-hidden="true" /> : item.status === 'failed' ? <TriangleAlert aria-hidden="true" /> : <Loader2 aria-hidden="true" className={item.status === 'uploading' ? 'animate-spin' : undefined} />}
      <span><b>{item.file.name}</b><small role={item.status === 'failed' ? 'alert' : undefined}>{item.error || ({ waiting: 'Waiting', uploading: 'Uploading…', done: 'Uploaded', failed: 'Upload failed' }[item.status])}</small></span>
    </li>)}</ul>
    {failed > 0 && !queue.busy && <button type="button" className="document-upload-retry" onClick={() => void queue.retry()}><RotateCcw aria-hidden="true" />Retry {failed === 1 ? 'failed file' : `${failed} failed files`}</button>}
  </div>
}
