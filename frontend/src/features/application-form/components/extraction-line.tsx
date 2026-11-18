import { useId, useState } from 'react'
import { CheckCircle2, Info, Loader2, ShieldAlert } from 'lucide-react'
import { Link } from 'react-router-dom'
import { cn } from '@/shared/lib/cn'
import { describeExtraction, failureDetails, identityActions } from '../form-model'
import { SHOW_MERGE_UI } from '../duplicate-merge'
import type { IdentityCheck } from '../form-api'
import type { DocumentKind, DocumentSlot } from '../types'
import { DuplicateMergeAction } from './candidate-merge'
import '@/styles/application-form-extract.css'

/**
 * The quiet "Notes (n)" line: a small muted button that opens a plain bullet list in a light grey panel. Always in the page (the
 * panel is only hidden), so printing shows it expanded. Nothing here is coloured: advisory notes are never alerts.
 */
export function ExtractionNotes({ notes }: { notes: string[] }) {
  const [open, setOpen] = useState(false)
  const id = useId()
  if (!notes.length) return null
  return (
    <div className="af-xnotes">
      <button type="button" className="af-xnotes-btn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((value) => !value)}>
        <Info aria-hidden="true" />Notes ({notes.length})
      </button>
      <ul id={id} className="af-xnotes-panel" hidden={!open}>{notes.map((note) => <li key={note}>{note}</li>)}</ul>
    </div>
  )
}

/**
 * What the reading of one document did, in the words a recruiter needs. How it looks is decided by `describeExtraction` (which
 * asks `lineTone`) and by nothing else here: red box (failed), blue box (reading), green line (filled), or no box at all with
 * the quiet Notes line. `notes` is the list already routed and de-duplicated across the Documents list (`routeLineNotes`).
 */
export function ExtractionLine({ kind, slot, onCorrect, disabled, attachmentId, onIdentity, onReadAgain, notes }: {
  kind: DocumentKind
  slot: DocumentSlot
  onCorrect?: () => void
  disabled?: boolean
  /** The file this reading belongs to, and what to do about a rejected one (Read again / This is the right person). */
  attachmentId?: string
  onIdentity?: (attachmentId: string, action: IdentityCheck) => Promise<void>
  /** Only for a reading that failed: asks for the same file to be read again (nothing for a file stopped as another person's). */
  onReadAgain?: () => Promise<void>
  notes?: string[]
}) {
  const [working, setWorking] = useState(false)
  const [problem, setProblem] = useState('')
  const line = describeExtraction(kind, slot)
  const quiet = notes ?? line.notes
  if (line.tone === 'none') return null
  const wrongDocument = slot.extraction?.identityMismatch === true
  const Icon = line.tone === 'running' ? Loader2 : line.tone === 'filled' ? CheckCircle2 : ShieldAlert
  const run = async (action: IdentityCheck) => {
    if (!attachmentId || !onIdentity || working) return
    setWorking(true); setProblem('')
    try { await onIdentity(attachmentId, action) }
    catch (cause) { setProblem(cause instanceof Error ? cause.message : 'The check could not be saved. Try again.') }
    finally { setWorking(false) }
  }
  const actions = wrongDocument ? identityActions(slot.extraction) : []
  const readAgain = async () => {
    if (!onReadAgain || working) return
    setWorking(true); setProblem('')
    try { await onReadAgain() }
    catch (cause) { setProblem(cause instanceof Error ? cause.message : 'Reading could not be started. Your file is saved.') }
    finally { setWorking(false) }
  }
  const details = line.tone !== 'failed' || !slot.extraction ? [] : wrongDocument ? failureDetails({ failure: slot.extraction.failure, message: '' }) : failureDetails(slot.extraction)
  return (
    <div className="af-extract-line">
      {line.tone === 'note'
        ? line.text && <p className="af-extract-quiet">{line.text}</p>
        : (
          <div className={cn('af-extract', `is-${line.tone}`, wrongDocument && 'is-wrong-document')} role={line.tone === 'failed' ? 'alert' : 'status'}>
            <Icon aria-hidden="true" /><span>{line.text}</span>
            {SHOW_MERGE_UI && wrongDocument && slot.extraction?.duplicateOf && <DuplicateMergeAction duplicate={slot.extraction.duplicateOf} />}
            {actions.map((action) => {
              switch (action.kind) {
                case 'read_again': return onIdentity && attachmentId ? <button key="read" type="button" className="af-docbtn" disabled={disabled || working} onClick={() => void run('read_again')}>Read again</button> : null
                case 'confirm_name': return onIdentity && attachmentId ? <button key="confirm" type="button" className="af-docbtn" disabled={disabled || working} onClick={() => void run('confirm_name')}>This is the right person: use this document</button> : null
                case 'open_candidate': return <Link key="open" className="af-docbtn" to={`/candidates/${action.candidateId}`}>{action.label}</Link>
                case 'check_passport': return <button key="passport" type="button" className="af-docbtn" onClick={() => document.querySelector('[data-document="passport"]')?.scrollIntoView({ block: 'center' })}>Check the passport</button>
                case 'upload': return onCorrect ? <button key="upload" type="button" className="af-docbtn" disabled={disabled} onClick={onCorrect}>Upload correct file</button> : null
              }
            })}
            {!wrongDocument && line.tone === 'failed' && onReadAgain && <button type="button" className="af-docbtn" disabled={disabled || working} onClick={() => void readAgain()}>{working ? 'Starting…' : 'Read again'}</button>}
            {problem && <small role="alert">{problem}</small>}
            {details.length > 0 && (
              <details className="af-extract-details">
                <summary>Details</summary>
                <ul>{details.map((text) => <li key={text}>{text}</li>)}</ul>
              </details>
            )}
          </div>
        )}
      <ExtractionNotes notes={quiet} />
    </div>
  )
}
