import { useState } from 'react'
import { Link } from 'react-router-dom'
import { CheckCircle2, ChevronDown, Copy, ExternalLink, FilePlus2, LoaderCircle, Undo2 } from 'lucide-react'
import { Button } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import type { DuplicateChoice, ImportedStatus, ImportResult, RepeatedPage, ResultDocument } from '../import-api'
import { countOf } from '../import-api'
import {
  ADDED_KEPT_NOTE, DUPLICATE_CONFIRM, ORIGINALS_NOTE, addedDocuments, addedHeadline, addedReadable, duplicateChoiceLabel, keepsOriginals, pagesLabel, repeatedLine,
  type DuplicateView,
} from '../import-view'
import { ResultTrack } from './result-track'

/**
 * A candidate that is already in the CRM. Before importing (`mode` "plan") the three choices are a pick (Skip is the default); after the import answered
 * "duplicate" (`mode` "result") nothing was created and each choice is an action. "Import as a new candidate anyway" is only there for a possible match,
 * and asks first.
 */
export function DuplicatePanel({ view, mode, selected = 'skip', disabled, onChoose }: {
  view: DuplicateView
  mode: 'plan' | 'result'
  selected?: DuplicateChoice
  disabled?: boolean
  onChoose: (choice: DuplicateChoice) => void
}) {
  const [confirming, setConfirming] = useState(false)
  const choose = (choice: DuplicateChoice) => {
    if (choice === 'import_anyway' && !confirming) { setConfirming(true); return }
    setConfirming(false)
    onChoose(choice)
  }
  return (
    <div className="ic-dup" role="group" aria-label="This candidate is already in the CRM">
      <div className="ic-dup-head">
        <Copy aria-hidden="true" />
        <div>
          <b>{view.headline}</b>
          <span>{mode === 'plan' ? 'Nothing is imported for this card unless you choose to below.' : 'Nothing was created. Choose what to do with these documents.'}</span>
        </div>
        {view.candidateId && <Link to={`/candidates/${view.candidateId}`} className="ic-link"><ExternalLink aria-hidden="true" />Open {view.careLabel || 'candidate'}</Link>}
      </div>
      <div className="ic-dup-actions">
        {view.choices.map((choice) => {
          const pressed = mode === 'plan' && selected === choice && !(choice === 'import_anyway' && confirming)
          return (
            <Button key={choice} size="sm" variant={pressed || (mode === 'result' && choice === 'add_documents') ? 'primary' : 'secondary'} disabled={disabled}
              icon={choice === 'add_documents' ? <FilePlus2 /> : undefined} aria-pressed={mode === 'plan' ? pressed : undefined}
              onClick={() => (mode === 'plan' && choice === selected && choice !== 'import_anyway' ? undefined : choose(choice))}>
              {duplicateChoiceLabel(choice, view.careLabel)}
            </Button>
          )
        })}
      </div>
      {confirming && (
        <p className="ic-dup-confirm" role="alert">
          <span>{DUPLICATE_CONFIRM}</span>
          <Button size="sm" disabled={disabled} onClick={() => choose('import_anyway')}>Yes, import anyway</Button>
          <button type="button" className="ic-link" onClick={() => setConfirming(false)}>Cancel</button>
        </p>
      )}
    </div>
  )
}

/** A duplicate a person left alone: one calm line, with a way back to the choices. */
export function SkippedLine({ view, onReopen, disabled }: { view: DuplicateView | null; onReopen: () => void; disabled?: boolean }) {
  return (
    <div className="ic-skipped" role="status">
      <span><b>Skipped.</b> {view?.headline ?? 'Already in the CRM.'} Nothing was imported for this card.</span>
      {view?.candidateId && <Link to={`/candidates/${view.candidateId}`} className="ic-link"><ExternalLink aria-hidden="true" />Open {view.careLabel || 'candidate'}</Link>}
      <button type="button" className="ic-link" disabled={disabled} onClick={onReopen}><Undo2 aria-hidden="true" />Choose again</button>
    </div>
  )
}

const pagesText = (pages: ResultDocument['pages']) => (Array.isArray(pages) ? pagesLabel(pages) : typeof pages === 'number' && pages > 0 ? countOf(pages, 'page') : '')

/** Documents were added to a candidate already in the CRM: which ones, which were there already, and that nothing else about the candidate changed. */
export function AddedPanel({ result, status, readLine }: { result: ImportResult; status?: ImportedStatus; readLine: (status: ImportedStatus) => string }) {
  const { added, already } = addedDocuments(result)
  const label = (result.careId || result.ref || '').trim() || 'the candidate'
  const readable = addedReadable(result)
  return (
    <div className="ic-added" role="status">
      <div className="ic-added-head">
        <CheckCircle2 aria-hidden="true" />
        <b>{addedHeadline(result)}</b>
        {result.candidateId && <Link to={`/candidates/${result.candidateId}`} className="ic-link"><ExternalLink aria-hidden="true" />Open {label}</Link>}
      </div>
      {added.length > 0 && (
        <ul className="ic-added-list" aria-label="Added">
          {added.map((doc, i) => (
            <li key={`a${i}`}><b>{doc.label ?? doc.slot}</b><span>{[doc.fileName, pagesText(doc.pages)].filter(Boolean).join(' · ')}{doc.note ? ` · ${doc.note}` : ''}</span></li>
          ))}
        </ul>
      )}
      {already.length > 0 && (
        <ul className="ic-added-list is-already" aria-label="Already filed">
          {already.map((doc, i) => (
            <li key={`f${i}`}><b>{doc.label ?? doc.slot}</b><span>already filed{doc.fileName ? ` (${doc.fileName})` : ''}{doc.note ? ` · ${doc.note}` : ''}</span></li>
          ))}
        </ul>
      )}
      <p className="ic-muted">{ADDED_KEPT_NOTE}</p>
      {readable && (status ? (
        <div className="ic-result">
          <ResultTrack status={status} />
          <p className="ic-added-read" aria-live="polite">{status.reading.open > 0 && <LoaderCircle className="animate-spin" aria-hidden="true" />}{readLine(status)}</p>
        </div>
      ) : <p className="ic-muted">Loading what was read…</p>)}
      {readable && result.candidateId && <p className="ic-muted">Open {label} to check the details read from the new documents.</p>}
    </div>
  )
}

/** "3 repeated pages were not filed again", with a Details disclosure that lists what was repeated. */
export function RepeatedPages({ duplicates }: { duplicates: RepeatedPage[] }) {
  const [open, setOpen] = useState(false)
  const line = repeatedLine(duplicates)
  if (!line) return null
  return (
    <div className="ic-repeat">
      <p>
        <Copy aria-hidden="true" />
        <span>{line}</span>
        <button type="button" className={cn('ic-link', open && 'is-open')} aria-expanded={open} onClick={() => setOpen((v) => !v)}>Details<ChevronDown aria-hidden="true" /></button>
      </p>
      {open && (
        <div className="ic-repeat-details">
          <ul>{duplicates.map((item, i) => <li key={i}>{item.message}</li>)}</ul>
          {keepsOriginals(duplicates) && <p className="ic-muted">{ORIGINALS_NOTE}</p>}
        </div>
      )}
    </div>
  )
}
