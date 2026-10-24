import { Component, useEffect, useState, type ReactNode } from 'react'

function Bone({ className = '' }: { className?: string }) {
  return <span className={`sk ${className}`} aria-hidden="true" />
}

/**
 * Placeholder for a screen whose records are still arriving. It mirrors the real layout
 * (heading, summary cards, table or detail panels) so the page does not jump on arrival.
 */
export function PageSkeleton({ variant = 'list', label = 'Loading' }: { variant?: 'list' | 'detail'; label?: string }) {
  return (
    <div role="status" aria-label={label} aria-busy="true" className="sk-page">
      <span className="sr-only">{label}…</span>
      {variant === 'detail' && <Bone className="sk-back" />}
      <div className="sk-head">
        <div className="sk-head-copy"><Bone className="sk-title" /><Bone className="sk-line" /></div>
        <div className="sk-actions"><Bone className="sk-button" /><Bone className="sk-button sk-button-primary" /></div>
      </div>
      <div className="sk-cards">
        {[0, 1, 2, 3].map((item) => <div key={item} className="sk-card"><Bone className="sk-line sk-short" /><Bone className="sk-value" /><Bone className="sk-line sk-mid" /></div>)}
      </div>
      {variant === 'detail' ? (
        <div className="sk-panel">
          <Bone className="sk-line sk-mid" />
          <div className="sk-grid">{[0, 1, 2, 3, 4, 5].map((item) => <div key={item}><Bone className="sk-line sk-short" /><Bone className="sk-line" /></div>)}</div>
        </div>
      ) : null}
      <div className="sk-panel sk-panel-flush">
        <div className="sk-toolbar"><Bone className="sk-search" /><Bone className="sk-chip" /><Bone className="sk-chip" /></div>
        {[0, 1, 2, 3, 4, 5].map((item) => (
          <div key={item} className="sk-row">
            <Bone className="sk-avatar" />
            <div className="sk-row-copy"><Bone className="sk-line sk-mid" /><Bone className="sk-line sk-short" /></div>
            <Bone className="sk-line sk-cell" /><Bone className="sk-line sk-cell sk-hide-sm" /><Bone className="sk-pill" />
          </div>
        ))}
      </div>
    </div>
  )
}

/** The outcome of a record read: a skeleton while it runs, a retry panel when it fails. */
export function SnapshotState({ error, onRetry, retryLabel = 'Try again', variant, label }: {
  error?: string; onRetry?: () => void; retryLabel?: string; variant?: 'list' | 'detail'; label?: string
}) {
  if (!error) return <PageSkeleton variant={variant} label={label} />
  return (
    <div role="alert" className="sk-error">
      <p className="sk-error-title">We could not load this page</p>
      <p className="sk-error-copy">{error}</p>
      {onRetry && <button type="button" className="workspace-loading-retry" onClick={onRetry}>{retryLabel}</button>}
    </div>
  )
}

export function PageLoading({ fullScreen = false, onRetry }: { fullScreen?: boolean; onRetry?: () => void }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 6_000)
    return () => window.clearTimeout(timer)
  }, [])
  if (!fullScreen) return slow && onRetry
    ? <SnapshotState error="This is taking longer than usual. Your saved work is safe." onRetry={onRetry} retryLabel="Retry connection" />
    : <PageSkeleton />
  return (
    <div role="status" aria-label="Opening your workspace" className="workspace-loading is-fullscreen">
      <div className="splash">
        <span className="splash-mark" aria-hidden="true"><span /></span>
        <p className="splash-brand">Care India</p>
        <div className="splash-track" aria-hidden="true"><span /></div>
        <p className={`splash-note ${slow ? 'is-visible' : ''}`}>{slow ? 'This is taking longer than usual. Your saved work is safe.' : ' '}</p>
        {slow && onRetry && <button type="button" className="workspace-loading-retry" onClick={onRetry}>Retry connection</button>}
      </div>
    </div>
  )
}

/** A failed page download or render should offer recovery instead of a blank screen. */
export class PageBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  render() {
    if (!this.state.failed) return this.props.children
    return (
      <div className="flex min-h-screen items-center justify-center bg-canvas px-6">
        <div role="alert" className="max-w-sm text-center">
          <h1 className="text-h3 font-semibold text-ink">This page did not open</h1>
          <p className="mt-2 text-sm text-ink-500">Reload the page to try again. If it keeps happening, tell your administrator.</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 rounded-control bg-navy-600 px-4 py-2 text-[14px] font-medium text-[color:var(--on-primary)] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-500"
          >
            Reload page
          </button>
        </div>
      </div>
    )
  }
}
