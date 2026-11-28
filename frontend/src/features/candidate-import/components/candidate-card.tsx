import { memo, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, ChevronDown, CircleAlert, ExternalLink, Info, LoaderCircle, Plane, RotateCcw, UserCheck } from 'lucide-react'
import { Badge, type Tone } from '@/shared/ui/display'
import { Button, Field, Select } from '@/shared/ui/primitives'
import { cn } from '@/shared/lib/cn'
import { primaryId } from '@/features/candidates/candidate-ids'
import { countOf, fileTooLarge, type DuplicateChoice, type IdentifyResult, type ImportedStatus, type ImportPlan, type ImportResult } from '../import-api'
import {
  FILES_CAPTION, NAME_TO_BE_READ, STATUS_LABEL, alreadyImportedNote, cardStatus, docChips, duplicateState, folderTag, giveAllNote, importedTitle, nameQuality, normalizeNameSource, planCounts, planDuplicate, plannedStoredNames, readChip, readName, readingText, splitIssues, unnamedDetail, unplacedForSoleCandidate,
  type CardStatus, type RowProgress, type StoredName,
} from '../import-view'
import { Avatar } from './avatar'
import { DocChips } from './doc-chips'
import { AddedPanel, DuplicatePanel, RepeatedPages, SkippedLine } from './duplicate-panel'
import { ResultTrack } from './result-track'

export interface CardActions {
  toggleOpen(folder: string): void
  tick(folder: string, checked: boolean): void
  setKind(parent: string, path: string, kind: string): void
  setAssign(parent: string, path: string, target: string): void
  setJob(folder: string, ref: string): void
  regroup(parent: string, mode: 'auto' | 'one'): void
  putBack(key: string): void
  assignLeftOut(parent: string, path: string, target: string): void
  /** Give every one of these files to one candidate (the per-file "Belongs to" choice, made for all at once). */
  assignAllLeftOut(parent: string, paths: string[], target: string): void
  readFiles(parent: string): void
  retry(plan: ImportPlan): void
  /** Before importing: what to do with a candidate the check found in the CRM (Skip is the default). */
  planChoice(folder: string, choice: DuplicateChoice): void
  /** After the import answered "duplicate": act on it. "reopen" brings the choices back after a Skip. */
  answerDuplicate(plan: ImportPlan, choice: DuplicateChoice | 'reopen'): void
}

/** What only an opened card needs: the controls' choices and the other candidates of the same folder. */
export interface OpenData {
  chosen: Record<string, string>
  /** What this card is called in the "Belongs to" choices: the name read from the documents, else the folder or file group. */
  label: string
  siblings: { folder: string; name: string; locked: boolean }[]
  pickedFiles: { path: string; file: File }[]
  kindChoices: { key: string; label: string }[]
  jobChoices: { ref: string; label: string }[]
  /** What an empty job choice means ("Same as above", "From the sheet (J-1)", "No job"). */
  jobDefault: string
  jobValue: string
  oneCandidate: boolean
  putBack: { key: string; path: string }[]
}

export interface CardProps {
  plan: ImportPlan
  actions: CardActions
  state?: RowProgress<ImportResult>
  status?: ImportedStatus
  checked: boolean
  expanded: boolean
  busy: boolean
  touched: boolean
  readResult?: IdentifyResult
  /** The reading (or the file names) made exactly one candidate of this picked folder. */
  soleCandidate?: boolean
  existing?: { id: string | null; label: string }
  /** What was chosen for a candidate the check found in the CRM (before importing). */
  choice?: DuplicateChoice
  canReadFiles: boolean
  open?: OpenData
}

const STATUS_TONE: Record<CardStatus, Tone> = {
  ready: 'green', look: 'amber', exists: 'navy', duplicate: 'amber', skipped: 'neutral', added: 'green', blocked: 'neutral', waiting: 'neutral', importing: 'navy', imported: 'green', failed: 'stamp',
}

const NAME_FROM = {
  documents: 'read from their documents',
  sheet: 'from your sheet',
  typed: 'typed by a person',
} as const

function placementText(plan: ImportPlan): string {
  if (plan.action === 'deploy') return 'All 13 stages recorded, deployed'
  if (plan.action === 'in_progress') {
    return plan.completedThrough > 0 ? `Stages 1-${plan.completedThrough} recorded, continue at stage ${plan.currentStage}` : 'Starts at stage 1 (prescreening)'
  }
  if (plan.action === 'documents_only') return 'Candidate and documents, no job attached'
  return 'Fix the problem below, or leave it out'
}

function resultText(result: ImportResult): string {
  if (result.action === 'deploy') return 'All 13 stages recorded, deployed'
  if (result.action === 'in_progress') {
    return (result.completedThrough ?? 0) > 0 ? `Stages 1-${result.completedThrough} recorded, continue at stage ${result.currentStage}` : 'Starts at stage 1 (prescreening)'
  }
  return ''
}

function StageStrip({ plan }: { plan: ImportPlan }) {
  const done = plan.action === 'deploy' ? 11 : plan.action === 'in_progress' ? plan.completedThrough : 0
  return (
    <span className="ic-stages" aria-hidden="true">
      {Array.from({ length: 11 }, (_, i) => i + 1).map((n) => (
        <i key={n} className={n <= done ? 'is-done' : plan.action === 'in_progress' && n === plan.currentStage ? 'is-now' : undefined} />
      ))}
    </span>
  )
}

const years = (value: number | null) => (value ? `${value} ${value === 1 ? 'year' : 'years'}` : null)

function Issue({ level, message }: { level: 'error' | 'warning' | 'info'; message: string }) {
  const Icon = level === 'error' ? CircleAlert : level === 'warning' ? AlertTriangle : Info
  return <li className={`ic-issue is-${level}`}><Icon aria-hidden="true" /><span>{message}</span></li>
}

function FileTable({ plan, name, state, busy, open, actions }: { plan: ImportPlan; name: string | null; state?: RowProgress<ImportResult>; busy: boolean; open: OpenData; actions: CardActions }) {
  const parent = plan.parent ?? plan.folder
  const locked = busy || Boolean(state)
  // A kind picked in the table changes the name the file is saved under, so the plan's own kinds are replaced by the picked one.
  const overrides: Record<string, string[]> = {}
  for (const [path, kind] of Object.entries(open.chosen)) {
    const label = kind ? open.kindChoices.find((k) => k.key === kind)?.label : undefined
    if (label) overrides[path] = [label]
  }
  const saved = plannedStoredNames(plan, name, overrides)
  return (
    <div className="ic-files-block">
      <h4 className="ic-files-title">Files</h4>
      <div className="ic-files-wrap">
        <table className="ic-files">
          <thead><tr><th>File</th><th>Treated as</th><th>Belongs to</th><th>Will be saved as</th></tr></thead>
          <tbody>
            {plan.files.map((file) => {
              const picked = open.pickedFiles.find((f) => f.path === file.path)?.file
              const tooLarge = picked ? fileTooLarge(picked) : false
              const planned = file.skipped || tooLarge ? undefined : saved.get(file.path)
              return (
                <tr key={file.path}>
                  <td data-label="File" className="ic-file-name" title={file.path}>
                    <span>{file.path}</span>
                    {file.pages?.length ? <em>{file.pages.length === 1 ? 'page' : 'pages'} {file.pages.join(', ')}</em> : null}
                  </td>
                  <td data-label="Treated as">
                    {file.skipped || tooLarge
                      ? <span className="ic-muted">{file.problem ?? 'Over 25 MB, left out'}</span>
                      : (
                        <Select compact value={open.chosen[file.path] ?? ''} disabled={locked} aria-label={`Treated as: ${file.path}`}
                          onChange={(e) => actions.setKind(parent, file.path, e.target.value)}>
                          <option value="">{file.guessed ? 'Not recognised by name: will be read' : file.label ?? 'Other documents'}</option>
                          {open.kindChoices.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
                        </Select>
                      )}
                  </td>
                  <td data-label="Belongs to">
                    <Select compact value={plan.folder} disabled={locked} aria-label={`Belongs to: ${file.path}`}
                      onChange={(e) => actions.setAssign(parent, file.path, e.target.value)}>
                      <option value={plan.folder}>{open.label}</option>
                      {open.siblings.map((other) => <option key={other.folder} value={other.folder} disabled={other.locked}>{other.name}</option>)}
                      <option value="__out__">Leave this file out</option>
                    </Select>
                  </td>
                  <td data-label="Will be saved as" className="ic-saved-as">
                    {planned ? <SavedAs planned={planned} /> : <span className="ic-muted">Not saved</span>}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function SavedAs({ planned }: { planned: StoredName }) {
  if (planned.pending || !planned.name) return <span className="ic-saved-pending" title="This file's name does not say what it is. It is read during the import and then named.">Decided during the import</span>
  const pieces = planned.pieces ?? []
  if (pieces.length > 1) {
    const more = pieces.length - 1
    const hint = [`${planned.note ?? ''}:`, ...pieces].join(String.fromCharCode(10))
    return (
      <span className="ic-saved-name" title={hint}>
        <code>{pieces[0]}</code>
        <small>+{more} more {more === 1 ? 'piece' : 'pieces'}</small>
      </span>
    )
  }
  return <span className="ic-saved-name"><code title={planned.name}>{planned.name}</code></span>
}

export const CandidateCard = memo(function CandidateCard({ plan, actions, state, status, checked, expanded, busy, touched, readResult, soleCandidate, existing, choice, canReadFiles, open }: CardProps) {
  const parent = plan.parent ?? plan.folder
  const shown = cardStatus(plan, state)
  const split = splitIssues(plan.issues)
  const counts = planCounts(plan, readResult)
  const person = plan.identified ? readResult?.people.find((p) => p.name === plan.name) : undefined
  const read = readName(plan, person)
  const quality = state?.status === 'done' ? 'ok' : plan.identified || /^unnamed candidate/i.test(plan.name) ? nameQuality(plan.name, plan, person) : 'ok'
  const tag = folderTag(plan, read)
  const placed = state?.status === 'done'
  // After the import the title is whatever the server says (it follows the reading of the documents); before it, only a name read from the documents.
  const careId = status?.careId ?? state?.result?.careId
  const after = placed ? importedTitle(status, careId, read) : null
  const name = after ? after.title : read ?? (quality === 'unnamed' ? 'Unnamed candidate' : NAME_TO_BE_READ)
  const nameKnown = after ? after.read : read !== null
  const detail = !placed && quality === 'unnamed' ? unnamedDetail(plan.name) : null
  const caption = after ? (status && after.read ? NAME_FROM[normalizeNameSource(status.nameSource) as keyof typeof NAME_FROM] ?? null : after.note)
    : quality === 'unnamed' ? 'We could not read the name on this card'
    : quality === 'low' ? 'Check the name before importing'
    : read ? 'found by reading the files'
    : null
  const sameFolder = plan.parent && plan.parent !== plan.folder
  const showTick = plan.action !== 'skip' && !state
  const stageText = state?.status === 'done' && state.result ? resultText(state.result) : plan.action === 'documents_only' ? '' : placementText(plan)
  const meta = [
    countOf(counts.documents, 'document'),
    counts.pages !== null ? countOf(counts.pages, 'page') : null,
    counts.leftOut > 0 ? `${counts.leftOut} left out` : null,
    plan.order,
    plan.recruiter,
  ].filter(Boolean).join(' · ')
  const chip = shown === 'imported' ? readChip(status) : null
  const label = chip?.label ?? STATUS_LABEL[shown]
  const chipTone: Tone = chip?.tone ?? STATUS_TONE[shown]
  const infoNotes = split.notes.length
  const [showNotes, setShowNotes] = useState(false)
  const noteWarnings = split.notes.filter((n) => n.level === 'warning').length
  // One person found in the folder and some files still belong to nobody: one click gives them all to this person.
  const giveAll = !state && soleCandidate ? unplacedForSoleCandidate([plan]) : null
  // A candidate already in the CRM: found by the check (before importing), or answered by the import ("duplicate": nothing was created).
  const planDup = !state ? planDuplicate(plan) : null
  const resultDup = state?.status === 'duplicate' || state?.status === 'skipped' ? duplicateState(state.result) : null
  const importedAgain = placed ? alreadyImportedNote(state?.result) : null

  return (
    <li className={cn('ic-card', `is-${shown}`, expanded && 'is-open', quality !== 'ok' && 'has-name-issue', checked && showTick && 'is-ticked')} data-quality={quality}>
      <div className="ic-card-head">
        {showTick ? (
          <label className="ic-tick">
            <input type="checkbox" checked={checked} disabled={busy} onChange={(e) => actions.tick(plan.folder, e.target.checked)} />
            <span className="sr-only">Import {name}</span>
          </label>
        ) : <span className="ic-tick-gap" aria-hidden="true" />}
        <button type="button" className="ic-card-main" aria-expanded={expanded} onClick={() => actions.toggleOpen(plan.folder)}
          title={`${name}${tag.folder ? ` (folder ${tag.folder})` : ''}`}>
          <Avatar name={name} unknown={!nameKnown} />
          <span className="ic-card-text">
            <span className="ic-name-row">
              <span className={cn('ic-name', (!nameKnown || quality !== 'ok') && 'is-muted')}>{name}</span>
              {detail && <span className="ic-tag">{detail}</span>}
              {quality === 'low' && <span className="ic-tag is-warn" title="Only one document gives this name, or the documents disagree.">low confidence</span>}
            </span>
            <span className="ic-caption">
              {caption ? <>{caption} · </> : null}
              <span className="ic-folder-tag" title="The folder these files were picked in. It only groups the files: it is not the candidate's name.">Folder: <b>{tag.folder}</b></span>
              {tag.group ? <span className="ic-folder-tag"> · files grouped as <b>{tag.group}</b></span> : null}
            </span>
          </span>
          <span className="ic-card-badge"><Badge tone={chipTone} dot={chipTone === 'neutral'}>{label}</Badge></span>
          <ChevronDown className="ic-chevron" aria-hidden="true" />
        </button>
      </div>

      <div className="ic-card-body">
        {!placed && (
          <div className="ic-meta-row">
            <p className="ic-meta">{meta}</p>
            {!state && infoNotes > 0 && (
              <button type="button" className={cn('ic-notes-toggle', noteWarnings > 0 && 'is-warn')} aria-expanded={showNotes} onClick={() => setShowNotes((v) => !v)}>
                {noteWarnings > 0 ? <AlertTriangle aria-hidden="true" /> : <Info aria-hidden="true" />}{infoNotes} {infoNotes === 1 ? 'note' : 'notes'}
                <ChevronDown className="ic-chevron" aria-hidden="true" />
              </button>
            )}
          </div>
        )}
        {!placed && <DocChips chips={docChips(plan.files)} />}
        {!state && plan.action !== 'documents_only' && plan.action !== 'skip' && (
          <p className="ic-stage-line"><StageStrip plan={plan} /><span>{stageText}</span></p>
        )}
        {giveAll && (
          <div className="ic-leftout ic-give-all">
            <p className="ic-warn-line"><AlertTriangle aria-hidden="true" /><span>{giveAllNote(giveAll.paths.length, read)}</span></p>
            <Button size="sm" icon={<UserCheck />} disabled={busy} onClick={() => actions.assignAllLeftOut(giveAll.parent, giveAll.paths, giveAll.target)}>
              Give all unplaced files to {read ?? 'this candidate'}
            </Button>
          </div>
        )}
        {!state && showNotes && infoNotes > 0 && <ul className="ic-issues is-notes">{split.notes.map((issue, k) => <Issue key={k} {...issue} />)}</ul>}

        {!state && shown === 'exists' && (
          <div className="ic-info" role="note">
            <Info aria-hidden="true" />
            <div>
              <b>Already in the CRM · {existing?.label ?? plan.duplicateOfCareId ?? plan.duplicateOf}</b>
              <span>This person is already saved, so nothing is imported for this card.</span>
            </div>
            {existing?.id && <Link to={`/candidates/${existing.id}`} className="ic-link"><ExternalLink aria-hidden="true" />Open candidate</Link>}
          </div>
        )}
        {planDup && (
          <DuplicatePanel view={planDup} mode="plan" selected={choice ?? 'skip'} disabled={busy} onChoose={(c) => actions.planChoice(plan.folder, c)} />
        )}
        {!state && split.checked && <p className="ic-checked">{split.checked}</p>}
        {!state && split.errors.length > 0 && <ul className="ic-issues is-errors" role="alert">{split.errors.map((issue, i) => <Issue key={i} {...issue} />)}</ul>}

        {!state && sameFolder && !plan.identified && !touched && (
          <button type="button" className="ic-link ic-same-person" disabled={busy} onClick={() => actions.regroup(parent, 'one')}
            title="These files all belong to one person: make the whole folder one candidate">Same person? Make one</button>
        )}

        {(state?.status === 'waiting' || state?.status === 'running') && (
          <div className="ic-importing" role="status">
            <span><LoaderCircle className="animate-spin" aria-hidden="true" />{state.status === 'running' ? 'Importing this candidate…' : 'Waiting for its turn…'}</span>
            <div className="ic-bar is-indeterminate"><span /></div>
          </div>
        )}
        {state?.status === 'duplicate' && resultDup && (
          <DuplicatePanel view={resultDup} mode="result" disabled={busy} onChoose={(c) => actions.answerDuplicate(plan, c)} />
        )}
        {state?.status === 'skipped' && <SkippedLine view={resultDup} disabled={busy} onReopen={() => actions.answerDuplicate(plan, 'reopen')} />}
        {state?.status === 'added' && state.result && <AddedPanel result={state.result} status={status} readLine={readingLine} />}
        {state?.status === 'failed' && (
          <div className="ic-failed" role="alert">
            <CircleAlert aria-hidden="true" />
            <span><b>Not imported.</b> {state.error}</span>
            {!busy && <Button size="sm" icon={<RotateCcw />} onClick={() => actions.retry(plan)}>Retry</Button>}
          </div>
        )}
        {placed && (
          <div className="ic-result">
            <div className="ic-result-head">
              <span className="ic-result-id">
                {state?.result?.candidateId
                  ? <Link to={`/candidates/${state.result.candidateId}`} className="ic-link"><ExternalLink aria-hidden="true" />Open candidate · {primaryId({ ref: state.result.ref, careId: status?.careId ?? state.result.careId })}</Link>
                  : null}
                {state?.result?.action === 'deploy' && <Plane className="ic-deployed" aria-label="Deployed" />}
              </span>
              {stageText && <span className="ic-muted">{stageText}</span>}
            </div>
            {importedAgain && <p className="ic-again" role="status">{importedAgain}. This folder was sent before, so nothing was filed twice.</p>}
            <ResultTrack status={status} />
            {(state?.result?.duplicates?.length ?? 0) > 0 && <RepeatedPages duplicates={state?.result?.duplicates ?? []} />}
            {status ? (
              <div className="ic-result-lines" aria-live="polite">
                <p>{status.reading.open > 0 && <LoaderCircle className="animate-spin" aria-hidden="true" />}{readingLine(status)}</p>
              </div>
            ) : <p className="ic-muted">Loading what was imported…</p>}
          </div>
        )}

        {expanded && placed && status && status.documents.length > 0 && (
          <div className="ic-filed">
            <p>{countOf(status.documents.length, 'document')} filed</p>
            {!after?.read && (
              <p className="ic-filed-note">These files carry the Care India ID for now. They are renamed after the candidate once the name is read from the documents.</p>
            )}
            <ul>
              {status.documents.map((doc) => (
                <li key={doc.slot}><b>{doc.label}</b><span>{doc.fileName}{doc.versions > 1 ? ` (and ${doc.versions - 1} earlier)` : ''}</span></li>
              ))}
            </ul>
          </div>
        )}

        {expanded && !placed && open && (
          <div className="ic-expanded">
            {!state && (
              <div className="ic-job">
                <Field label="Job for this candidate">
                  <Select compact value={open.jobValue} disabled={busy} onChange={(e) => actions.setJob(plan.folder, e.target.value)}>
                    <option value="">{open.jobDefault}</option>
                    {open.jobChoices.map((o) => <option key={o.ref} value={o.ref}>{o.label}</option>)}
                  </Select>
                </Field>
              </div>
            )}
            {!touched && (
              <p className="ic-grouping">
                {sameFolder
                  ? <>{plan.identified ? 'This folder was separated into people by reading the files.' : 'This folder was separated into several people by the names in its file names.'}{' '}
                    <button type="button" className="ic-link" disabled={busy} onClick={() => actions.regroup(parent, 'one')}>Make it one candidate</button></>
                  : open.oneCandidate
                    ? <>This folder is one candidate. <button type="button" className="ic-link" disabled={busy} onClick={() => actions.regroup(parent, 'auto')}>It holds several people: separate by names</button></>
                    : 'This folder is one candidate.'}
              </p>
            )}
            <FileTable plan={plan} name={read} state={state} busy={busy} open={open} actions={actions} />
            {!state && !plan.identified && (plan.leftOut ?? []).length > 0 && (
              <div className="ic-leftout">
                <p>Not given to anyone, because the file name does not say whose it is. Choose who it belongs to:</p>
                <ul>
                  {(plan.leftOut ?? []).map((path) => (
                    <li key={path}>
                      <span title={path}>{path}</span>
                      <Select compact aria-label={`Who owns ${path}`} value="" disabled={busy}
                        onChange={(e) => { if (e.target.value) actions.assignLeftOut(parent, path, e.target.value) }}>
                        <option value="">Choose…</option>
                        <option value={plan.folder}>{open.label}</option>
                        {open.siblings.filter((other) => !other.locked).map((other) => <option key={other.folder} value={other.folder}>{other.name}</option>)}
                      </Select>
                    </li>
                  ))}
                </ul>
              </div>
            )}
            {!state && open.putBack.map((item) => (
              <p key={item.key} className="ic-putback">
                Left out: {item.path}. <button type="button" className="ic-link" disabled={busy} onClick={() => actions.putBack(item.key)}>Put it back</button>
              </p>
            ))}
            {!state && canReadFiles && !touched && (
              <p className="ic-quiet-read">Not sure these are the right candidates? <button type="button" className="ic-link" disabled={busy} onClick={() => actions.readFiles(parent)}>Read the files</button> to find each person (on this computer).</p>
            )}
          </div>
        )}
      </div>
    </li>
  )
})

function readingLine(status: ImportedStatus): string {
  const { reading, experience } = status
  const base = readingText(reading)
  const experienceText = experience.total ? ` Experience: ${years(experience.total)} in total (India ${years(experience.india) ?? 'none'}, abroad ${years(experience.abroad) ?? 'none'}).` : ''
  return `${base}${status.detailsFound > 0 ? ` ${status.detailsFound} details filled in.` : ''}${experienceText}`
}
