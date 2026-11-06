import { Loader2 } from 'lucide-react'
import { useEffect, useState, type CSSProperties } from 'react'
import { LOADING_LEADS } from '../leads-view-model'
import '@/styles/lead-extras.css'

/** Grey shimmering rows in the place of the table while the first answer (or the answer to new filters) is on its way. */
export function LeadsSkeleton({ rows = 8, columns = 7, label = LOADING_LEADS }: { rows?: number; columns?: number; label?: string }) {
  return (
    <div className="ld-skel" role="status" aria-live="polite" aria-busy="true" aria-label={label} style={{ '--ld-skel-cols': columns } as CSSProperties}>
      <p className="ld-skel-note"><Loader2 size={16} className="ld-spin" aria-hidden="true" />{label}</p>
      <div className="ld-skel-row is-head" aria-hidden="true">{Array.from({ length: columns }, (_, i) => <i key={i} />)}</div>
      {Array.from({ length: rows }, (_, row) => (
        <div className="ld-skel-row" aria-hidden="true" key={row}>{Array.from({ length: columns }, (_, i) => <i key={i} />)}</div>
      ))}
    </div>
  )
}

/** True once `flag` has been on for `ms`: lets old rows stay on screen (dimmed) for a moment before the skeleton replaces them. */
export function useDelayedFlag(flag: boolean, ms: number): boolean {
  const [late, setLate] = useState(false)
  useEffect(() => {
    if (!flag) { setLate(false); return }
    const timer = window.setTimeout(() => setLate(true), ms)
    return () => window.clearTimeout(timer)
  }, [flag, ms])
  return flag && late
}
