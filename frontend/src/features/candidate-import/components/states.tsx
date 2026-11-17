import type { ReactNode } from 'react'
import { CircleAlert, RotateCcw } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'

/** Grey shimmering cards while the folder is being checked. */
export function CheckingSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="ic-skeleton" role="status" aria-live="polite">
      <span className="sr-only">Checking the files…</span>
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="ic-skel-card" aria-hidden="true">
          <span className="ic-skel ic-skel-avatar" />
          <div className="ic-skel-lines">
            <span className="ic-skel ic-skel-line w-40" />
            <span className="ic-skel ic-skel-line w-70" />
            <span className="ic-skel-row"><span className="ic-skel ic-skel-chip" /><span className="ic-skel ic-skel-chip" /><span className="ic-skel ic-skel-chip" /></span>
          </div>
        </div>
      ))}
    </div>
  )
}

/** A friendly error with a way forward. */
export function ErrorCard({ title, children, onRetry, retryLabel = 'Try again' }: { title: string; children?: ReactNode; onRetry?: () => void; retryLabel?: string }) {
  return (
    <div className="ic-error" role="alert">
      <CircleAlert aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        {children && <p>{children}</p>}
      </div>
      {onRetry && <Button size="sm" icon={<RotateCcw />} onClick={onRetry}>{retryLabel}</Button>}
    </div>
  )
}
