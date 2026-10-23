import { Component, useEffect, useState, type ReactNode } from 'react'

export function PageLoading({ fullScreen = false, onRetry }: { fullScreen?: boolean; onRetry?: () => void }) {
  const [slow, setSlow] = useState(false)
  useEffect(() => {
    const timer = window.setTimeout(() => setSlow(true), 6_000)
    return () => window.clearTimeout(timer)
  }, [])
  return (
    <div
      role="status"
      className={`workspace-loading ${fullScreen ? 'is-fullscreen' : ''}`}
    >
      <div className="workspace-loading-card">
        <span className="workspace-loading-mark" aria-hidden="true"><span /></span>
        {fullScreen && <p className="workspace-loading-brand">Care India</p>}
        <h1>{fullScreen ? 'Opening your workspace' : 'Loading this view'}</h1>
        <p>{slow ? 'The server is taking longer than usual. Your saved work is safe.' : 'Getting your latest records and permissions.'}</p>
        <div className="workspace-loading-track" aria-hidden="true"><span /></div>
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
          <h1 className="text-h3 font-semibold text-ink">This page could not load</h1>
          <p className="mt-2 text-sm text-ink-500">Reload the page to try again.</p>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="mt-4 rounded-control bg-navy-600 px-4 py-2 text-sm font-medium text-white focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-navy-500"
          >
            Reload page
          </button>
        </div>
      </div>
    )
  }
}
