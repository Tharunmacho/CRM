import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { AlertCircle, ArrowUpRight, X } from 'lucide-react'
import { Link } from 'react-router-dom'
import type { CandidateReviewIssue } from '../candidate-review'

/** Exact reasons remain one click away without making every table row taller. */
export function CandidateReviewPopover({ candidateId, name, issues }: { candidateId: string; name: string; issues: CandidateReviewIssue[] }) {
  const [open, setOpen] = useState(false)
  const [at, setAt] = useState({ top: 0, left: 0 })
  const trigger = useRef<HTMLButtonElement>(null)
  const panel = useRef<HTMLDivElement>(null)
  const id = useId()
  useLayoutEffect(() => {
    if (!open || !trigger.current || !panel.current) return
    const anchor = trigger.current.getBoundingClientRect()
    const box = panel.current.getBoundingClientRect()
    setAt({ left: Math.max(12, Math.min(anchor.right - box.width, window.innerWidth - box.width - 12)),
      top: anchor.bottom + box.height + 8 < window.innerHeight ? anchor.bottom + 8 : Math.max(12, anchor.top - box.height - 8) })
    panel.current.focus({ preventScroll: true })
  }, [open])
  useEffect(() => {
    if (!open) return
    const close = () => setOpen(false)
    const outside = (event: PointerEvent) => { if (!trigger.current?.contains(event.target as Node) && !panel.current?.contains(event.target as Node)) close() }
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') { close(); trigger.current?.focus() } }
    const scroll = (event: Event) => { if (!panel.current?.contains(event.target as Node)) close() }
    document.addEventListener('pointerdown', outside)
    document.addEventListener('keydown', escape)
    window.addEventListener('scroll', scroll, true)
    window.addEventListener('resize', close)
    return () => { document.removeEventListener('pointerdown', outside); document.removeEventListener('keydown', escape); window.removeEventListener('scroll', scroll, true); window.removeEventListener('resize', close) }
  }, [open])
  if (!issues.length) return null
  return <>
    <button ref={trigger} type="button" className="db-review-trigger" aria-label={`View ${issues.length} profile ${issues.length === 1 ? 'issue' : 'issues'} for ${name}`} aria-haspopup="dialog" aria-expanded={open} aria-controls={open ? id : undefined}
      onClick={event => { event.stopPropagation(); setOpen(value => !value) }}><AlertCircle aria-hidden="true" /><span>{issues.length} {issues.length === 1 ? 'issue' : 'issues'}</span></button>
    {open && createPortal(<div ref={panel} id={id} role="dialog" aria-label={`Profile issues for ${name}`} tabIndex={-1} className="db-review-popover" style={at} onClick={event => event.stopPropagation()}
      onBlur={event => { if (event.relatedTarget && !panel.current?.contains(event.relatedTarget as Node) && !trigger.current?.contains(event.relatedTarget as Node)) setOpen(false) }}
      onKeyDown={event => { event.stopPropagation(); if (event.key === 'Escape') { setOpen(false); trigger.current?.focus() } }}>
      <header><div><strong>Profile details to check</strong><small>{name}</small></div><button type="button" aria-label="Close profile issues" onClick={() => { setOpen(false); trigger.current?.focus() }}><X aria-hidden="true" /></button></header>
      <ul>{issues.map(issue => <li key={issue.key}><AlertCircle aria-hidden="true" /><div><b>{issue.label}</b><p>{issue.action}</p></div></li>)}</ul>
      <Link to={`/candidates/${candidateId}`} onClick={() => setOpen(false)}>Open profile <ArrowUpRight aria-hidden="true" /></Link>
    </div>, document.body)}
  </>
}
