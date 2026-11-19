import { Loader2 } from 'lucide-react'
import '@/styles/clients-simple.css'

/**
 * The Active switch of one agency in the list: a 32 px pill, green with "Active" when on and neutral with "Not active" when off.
 * It is a real button that reports its state with aria-pressed, so the keyboard (Tab, Space, Enter) and screen readers work as
 * for any toggle; the agency it belongs to is named for screen readers only. Without `onToggle` (a person who may not edit)
 * it is the same pill as plain text.
 */
export function ActiveToggle({ agencyName, active, busy = false, error = '', onToggle }: {
  agencyName: string
  active: boolean
  busy?: boolean
  /** The reason the last change did not save; shown under the pill until the next try. */
  error?: string
  onToggle?: (next: boolean) => void
}) {
  const word = active ? 'Active' : 'Not active'
  return <span className="cs-active-cell">
    {onToggle
      ? <button type="button" className={`cs-switch${active ? ' is-on' : ''}`} aria-pressed={active} disabled={busy} aria-busy={busy || undefined}
        title={active ? 'Active: listed first. Press to mark not active.' : 'Not active. Press to mark active and list it first.'}
        onClick={(event) => { event.stopPropagation(); onToggle(!active) }}>
        {busy ? <Loader2 size={13} className="cs-switch-spin" aria-hidden="true" /> : <i aria-hidden="true" />}
        <span className="sr-only">{agencyName}: </span>{word}
      </button>
      : <span className={`cs-switch is-static${active ? ' is-on' : ''}`}><i aria-hidden="true" /><span className="sr-only">{agencyName}: </span>{word}</span>}
    {error && <small className="cs-active-error" role="alert">{error}</small>}
  </span>
}
