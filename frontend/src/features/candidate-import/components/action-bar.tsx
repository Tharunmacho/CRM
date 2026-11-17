import { memo, useEffect, useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, Database, FolderPlus, LoaderCircle, RotateCcw, TriangleAlert, Upload } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import { updatedAgo, type BarModel, type ReadBar } from '../import-view'

interface Props {
  model: BarModel
  failed: number
  /** Something was imported: the bar follows the reading of the documents. */
  anyImported: boolean
  importing: boolean
  rereading: boolean
  /** Fraction of the batch imported while the import runs (0-1). */
  fraction: number
  /** The reading of the imported documents: words, progress and which buttons belong (live, from the status calls). */
  read: ReadBar
  /** When the status was last asked for (milliseconds), shown quietly. */
  updatedAt: number | null
  onImport: () => void
  onRetry: () => void
  onRead: () => void
  onMore: () => void
}

const LINK = 'ui-button ui-button-secondary inline-flex max-w-full shrink-0 items-center justify-center gap-2 whitespace-normal rounded-[8px] border border-[color:var(--border-control)] bg-[color:var(--bg-card)] px-4 py-1.5 text-center text-[14px] font-medium leading-tight text-[color:var(--text-main)] transition-colors hover:border-[color:var(--border-hover)] hover:bg-[color:var(--tint-1)] min-h-10 [&>svg]:h-4 [&>svg]:w-4'

/** "Updated just now": re-worded every few seconds without asking the server. */
function Updated({ at }: { at: number }) {
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000)
    return () => window.clearInterval(timer)
  }, [])
  return <small className="ic-updated">{updatedAgo((now - at) / 1000)}</small>
}

/** Sticky at the bottom (a bottom sheet on a phone): how much is ready, the one main button, and in words why it cannot be pressed. After the import it follows the reading live. */
export const ActionBar = memo(function ActionBar({ model, failed, anyImported, importing, rereading, fraction, read, updatedAt, onImport, onRetry, onRead, onMore }: Props) {
  const imported = model.phase === 'imported'
  const afterImport = anyImported && !importing
  const busy = afterImport && read.tone === 'busy'
  const headline = afterImport ? read.headline : model.headline
  const detail = afterImport ? (failed > 0 ? model.detail : read.detail) : model.reason && !imported ? model.reason : model.detail
  const bar = importing ? fraction : afterImport ? read.progress : undefined
  return (
    <div className={cn('ic-bar-wrap', (importing || busy) && 'is-running', afterImport && `is-${read.tone}`)} role="region" aria-label="Import summary">
      <div className="ic-sticky">
        {bar !== undefined && (
          <span className={cn('ic-sticky-progress', bar === null && 'is-indeterminate')} aria-hidden="true"><span style={bar === null ? undefined : { width: `${Math.round(bar * 100)}%` }} /></span>
        )}
        <div className="ic-sticky-text" aria-live="polite">
          <strong>
            {afterImport && busy ? <LoaderCircle className="ic-ok animate-spin" aria-hidden="true" />
              : afterImport && read.tone === 'warn' ? <TriangleAlert className="ic-ok is-warn" aria-hidden="true" />
              : imported && !failed ? <CheckCircle2 className="ic-ok" aria-hidden="true" /> : null}
            {headline}
          </strong>
          <span>{detail}</span>
          {afterImport && updatedAt !== null && read.tone !== 'neutral' && <Updated at={updatedAt} />}
        </div>
        <div className="ic-sticky-actions">
          {failed > 0 && !importing && <Button icon={<RotateCcw />} onClick={onRetry}>Retry {failed} failed</Button>}
          {afterImport ? (
            <>
              {!model.importDisabled && <Button icon={<Upload />} onClick={onImport}>{model.importLabel}</Button>}
              <Link to="/data-bank" className={LINK}><Database aria-hidden="true" />Open Data Bank</Link>
              {read.finished && <Button icon={<FolderPlus />} onClick={onMore}>Import more</Button>}
              {read.readLabel && !busy && (
                <Button variant="primary" icon={rereading ? <LoaderCircle className="animate-spin" /> : <RotateCcw />} onClick={onRead} disabled={rereading}>{read.readLabel}</Button>
              )}
            </>
          ) : imported && !importing ? (
            // Everything that ran was a duplicate or a failure: nothing to read, but the person can import more or go on.
            <>
              {!model.importDisabled && <Button variant="primary" icon={<Upload />} onClick={onImport}>{model.importLabel}</Button>}
              <Link to="/data-bank" className={LINK}><Database aria-hidden="true" />Open Data Bank</Link>
              <Button icon={<FolderPlus />} onClick={onMore}>Import more</Button>
            </>
          ) : (
            <Button variant="primary" icon={importing ? <LoaderCircle className="animate-spin" /> : <Upload />} onClick={onImport} disabled={model.importDisabled}>{model.importLabel}</Button>
          )}
        </div>
      </div>
    </div>
  )
})
