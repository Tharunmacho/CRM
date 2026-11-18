import { useEffect, useRef, useState } from 'react'
import { useLiveStatus } from '@/shared/state/live'
import { agoText, changedCells } from './daily-report-live'
import type { Cells } from './daily-report-view'

/**
 * "Live" with a quiet dot and "Updated 12 s ago". The dot is amber and says "Reconnecting…" when the stream has dropped,
 * grey when live updates are not running at all. The time text is not announced to screen readers.
 */
export function LiveBadge({ updatedAt, busy }: { updatedAt: number | null; busy: boolean }) {
  const status = useLiveStatus()
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => { if (document.visibilityState === 'visible') setNow(Date.now()) }, 5_000)
    return () => window.clearInterval(timer)
  }, [])
  const tone = status === 'up' ? 'is-up' : status === 'off' ? 'is-off' : 'is-down'
  const word = status === 'up' ? 'Live' : status === 'off' ? 'Not live' : 'Reconnecting…'
  return <span className={`rt-live ${tone}`} title={status === 'up' ? 'Changes your colleagues make appear here as they happen.' : status === 'off' ? 'Live updates are off; the report refreshes every 30 seconds.' : 'The live connection dropped; the report refreshes every 30 seconds until it is back.'}>
    <i aria-hidden="true" className={busy ? 'is-busy' : undefined} />
    <b>{word}</b>
    <span aria-live="off">{updatedAt ? `· updated ${agoText(now - updatedAt)}` : '· loading…'}</span>
  </span>
}

/**
 * Which cells changed since the last answer, for a subtle 600 ms tint. `scope` names the question being shown
 * (tab, period, filters): when it changes the new numbers are simply the baseline, nothing flashes.
 */
export function useChangedCells(scope: string, values: Cells): Set<string> {
  const previous = useRef<{ scope: string; values: Cells } | null>(null)
  const [flash, setFlash] = useState<Set<string>>(() => new Set())
  const signature = JSON.stringify(values)
  useEffect(() => {
    const before = previous.current
    previous.current = { scope, values }
    if (!before || before.scope !== scope) { setFlash(new Set()); return undefined }
    const changed = changedCells(before.values, values)
    if (!changed.length) return undefined
    setFlash(new Set(changed))
    const timer = window.setTimeout(() => setFlash(new Set()), 700)
    return () => window.clearTimeout(timer)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope, signature])
  return flash
}
