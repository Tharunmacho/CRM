import { ArrowLeft, CloudOff, DatabaseZap, FileQuestion, Lock, RefreshCw, TriangleAlert, type LucideIcon } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import type { LoadErrorKind, LoadErrorView } from '../load-error'

const ICONS: Record<LoadErrorKind, LucideIcon> = {
  server_outdated: RefreshCw,
  candidate_not_found: FileQuestion,
  order_not_found: FileQuestion,
  schema_pending: DatabaseZap,
  forbidden: Lock,
  network: CloudOff,
  other: TriangleAlert,
}

/** A specific, calm error card in the form's own styling, with Try again (when it can help) and Back. */
export function FormErrorCard({ error, onRetry, onBack }: { error: LoadErrorView; onRetry: () => void; onBack: () => void }) {
  const Icon = ICONS[error.kind]
  return (
    <div className="af-error" role="alert">
      <span className="af-error-icon" aria-hidden="true"><Icon /></span>
      <h2>{error.title}</h2>
      <p>{error.text}</p>
      <div className="af-error-actions">
        {error.retryable && <Button variant="primary" icon={<RefreshCw />} onClick={onRetry}>Try again</Button>}
        <Button variant={error.retryable ? 'secondary' : 'primary'} icon={<ArrowLeft />} onClick={onBack}>Back</Button>
      </div>
    </div>
  )
}

const bar = (width: string) => <i className="af-sk" style={{ width }} />

/** Placeholder that mirrors the form's grid (side panel, header band, section bars, label/value rows) while it loads. */
export function FormSkeleton() {
  const rows = (count: number) => Array.from({ length: count }, (_, index) => (
    <div className="af-sk-row" key={index}><div className="af-sk-label">{bar('70%')}</div><div className="af-sk-value">{bar(`${55 + ((index * 17) % 35)}%`)}</div><div className="af-sk-label">{bar('64%')}</div><div className="af-sk-value">{bar(`${40 + ((index * 23) % 45)}%`)}</div></div>
  ))
  return (
    <div className="af-layout" role="status" aria-label="Loading the application form" aria-busy="true">
      <aside className="af-side" aria-hidden="true">
        <div className="af-card af-complete"><span className="af-sk af-sk-ring" /><div style={{ flex: 1 }}>{bar('60%')}<br />{bar('80%')}</div></div>
        <div className="af-card">{bar('40%')}<div className="af-sk-lines">{bar('90%')}{bar('75%')}{bar('85%')}</div></div>
      </aside>
      <div className="af-main" aria-hidden="true">
        <div className="af-sheet">
          <div className="af-letterhead">
            <div className="af-brand"><span className="af-sk af-sk-logo" /></div>
            <div className="af-heading">{bar('70%')}<br />{bar('45%')}</div>
            <div className="af-photo-wrap"><span className="af-sk af-sk-photo" /></div>
          </div>
          {[1, 2, 3].map((n) => (
            <section className="af-section" key={n}>
              <div className="af-section-head af-sk-head">{bar('30%')}</div>
              <div className="af-section-body af-sk-body">{rows(n === 1 ? 4 : 2)}</div>
            </section>
          ))}
        </div>
      </div>
    </div>
  )
}
