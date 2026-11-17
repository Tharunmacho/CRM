import { memo, useEffect, useRef, useState } from 'react'
import { AlertTriangle, FileText, LoaderCircle, ScanSearch, ShieldCheck, Undo2, X } from 'lucide-react'
import { Button, } from '@/shared/ui/primitives'
import { Select } from '@/shared/ui/select'
import { leftovers, summaryCounts, summaryText, waiting, type IdentifyResult, type Placement } from '../import-api'
import { READ_HINT, formatElapsed, readEstimate, readReasonDetail, type ReadReason } from '../import-view'
import { ErrorCard } from './states'

const filesWord = (count: number) => `${count} ${count === 1 ? 'file' : 'files'}`

/** The prominent card when the file names do not say whose the files are: the honest reason and the one button. */
export const ReadActionCard = memo(function ReadActionCard({ folderName, fileCount, mostlyUnnamed, reason, error, disabled, onRead }: {
  folderName?: string
  fileCount: number
  mostlyUnnamed: boolean
  /** Why the file names do not settle whose the files are (see `personReadingReason`); drives the amber recommendation. */
  reason?: ReadReason | null
  error: string
  disabled: boolean
  onRead: () => void
}) {
  const detail = readReasonDetail(reason)
  return (
    <section className="ic-action-card" aria-label="Find each candidate">
      <span className="ic-action-icon" aria-hidden="true"><ScanSearch /></span>
      <div className="ic-action-text">
        <h2>{folderName ? `Some files in "${folderName}" do not say whose they are` : 'Some files do not say whose they are'}</h2>
        <p>
          The file names do not tell us which candidate each file belongs to. We can read the pages on this computer and sort them by person
          ({filesWord(fileCount)}). <strong>Nothing is sent online.</strong> It can take a few minutes.
        </p>
        {reason && reason !== 'left_out' && (
          <p className="ic-warn-line" role="note"><AlertTriangle aria-hidden="true" /><span><strong>{READ_HINT}.</strong>{detail ? ` ${detail}` : ''} Reading the files is recommended before importing.</span></p>
        )}
        {mostlyUnnamed && <p className="ic-warn-line"><AlertTriangle aria-hidden="true" />Most files say nobody's name, so the split by names below is not final.</p>}
        {error && <ErrorCard title="The files could not be read">{error}</ErrorCard>}
      </div>
      <Button variant="primary" icon={<ScanSearch />} onClick={onRead} disabled={disabled}>{error ? 'Try reading again' : 'Read the files to find each candidate'}</Button>
    </section>
  )
})

/** A quiet link for when the names already look clear: the person may still want the pages read. */
export const ReadQuietLink = memo(function ReadQuietLink({ disabled, onRead }: { disabled: boolean; onRead: () => void }) {
  return (
    <p className="ic-quiet-read">
      Not sure these are the right candidates?{' '}
      <button type="button" className="ic-link" onClick={onRead} disabled={disabled}>Read the files</button>
      <span> to find each person (on this computer, nothing is sent online).</span>
    </p>
  )
})

/** While the files are read here: a moving bar that turns into an estimate, the time spent, and a clear Cancel. */
export function ReadProgressCard({ folderName, fileCount, onCancel }: { folderName?: string; fileCount: number; onCancel: () => void }) {
  const started = useRef(Date.now())
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - started.current) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const estimate = readEstimate(elapsed, fileCount)
  // Announced to screen readers only every half minute: a counter that speaks every second would be noise.
  const spoken = Math.floor(elapsed / 30) * 30
  return (
    <section className="ic-progress-card" aria-label="Reading the files">
      <div className="ic-progress-top">
        <span className="ic-spin-badge" aria-hidden="true"><LoaderCircle /></span>
        <div className="ic-progress-title">
          <h2>Reading {filesWord(fileCount)}{folderName ? ` in "${folderName}"` : ''}</h2>
          <p>Finding each candidate on this computer. This can take a few minutes.</p>
        </div>
        <span className="ic-elapsed" aria-hidden="true">{formatElapsed(elapsed)} <small>elapsed</small></span>
      </div>
      <div className={`ic-bar${estimate === null ? ' is-indeterminate' : ''}`} role="progressbar" aria-label="Reading progress" aria-valuemin={0} aria-valuemax={100}
        aria-valuenow={estimate === null ? undefined : Math.round(estimate * 100)} aria-valuetext={estimate === null ? 'Working' : `About ${Math.round(estimate * 100)} percent`}>
        <span style={estimate === null ? undefined : { width: `${Math.max(4, Math.round(estimate * 100))}%` }} />
      </div>
      <div className="ic-progress-foot">
        <span className="ic-safe"><ShieldCheck aria-hidden="true" />Nothing is sent online. The files stay on this computer.</span>
        <Button size="sm" icon={<X />} onClick={onCancel}>Cancel</Button>
      </div>
      <span className="sr-only" role="status">{spoken === 0 ? 'Reading the files.' : `Still reading the files, ${formatElapsed(spoken)} elapsed.`}</span>
    </section>
  )
}

/** After the reading: four big numbers, what disagrees, and the way back to sorting by file names. */
export const ReadSummaryBar = memo(function ReadSummaryBar({ folderName, result, placement, running, onForget }: {
  folderName?: string
  result: IdentifyResult
  placement: Placement
  running: boolean
  onForget: () => void
}) {
  const counts = summaryCounts(result, placement)
  const flagged = result.people.filter((person) => person.conflicts.length > 0 || person.warnings.length > 0)
  const stats: { key: string; value: number; label: string; tone: string }[] = [
    { key: 'people', value: result.people.length, label: result.people.length === 1 ? 'candidate found' : 'candidates found', tone: 'blue' },
    { key: 'placed', value: counts.placed, label: counts.placed === 1 ? 'page placed' : 'pages placed', tone: 'green' },
    { key: 'need', value: counts.needYou, label: 'need you', tone: counts.needYou > 0 ? 'amber' : 'muted' },
    { key: 'unreadable', value: counts.unreadable, label: 'unreadable', tone: counts.unreadable > 0 ? 'red' : 'muted' },
  ]
  if (counts.leftOut > 0) stats.push({ key: 'left', value: counts.leftOut, label: 'left out', tone: 'muted' })
  return (
    <section className="ic-summary" aria-label="What was found" aria-live="polite">
      <header>
        <div>
          <h2>{folderName ? `Found by reading "${folderName}"` : 'Found by reading the files'}</h2>
          <p>{summaryText(result, placement)}. Nothing was sent online.</p>
        </div>
        {!running && <Button size="sm" variant="ghost" icon={<Undo2 />} onClick={onForget}>Go back to sorting by file names</Button>}
      </header>
      <dl className="ic-stats">
        {stats.map((stat) => (
          <div key={stat.key} className={`ic-stat is-${stat.tone}`}>
            <dd>{stat.value}</dd>
            <dt>{stat.label}</dt>
          </div>
        ))}
      </dl>
      {result.people.length === 0 && <p className="ic-warn-line"><AlertTriangle aria-hidden="true" />No candidate could be told apart in these files. Place the pages below yourself, or go back to sorting by file names.</p>}
      {result.stopped && <p className="ic-warn-line"><AlertTriangle aria-hidden="true" />The reading stopped early: the files not read are listed below.</p>}
      {flagged.length > 0 && (
        <ul className="ic-flagged">
          {flagged.map((person) => (
            <li key={person.key}>
              <AlertTriangle aria-hidden="true" />
              <span><b>{person.name}:</b> {[...person.conflicts, ...person.warnings].map((c) => c.message).join(' ')}{person.needsLook ? ' Not ticked for import until you check.' : ''}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
})

/** The pages nobody owns yet: whose are they? Each has a place-it dropdown; nothing is guessed. */
export const UnplacedTray = memo(function UnplacedTray({ result, placement, running, onPlace }: {
  result: IdentifyResult
  placement: Placement
  running: boolean
  onPlace: (id: string, target: string | undefined) => void
}) {
  const rows = leftovers(result)
  if (rows.length === 0) return null
  const stillWaiting = waiting(result, placement).length
  return (
    <details className={`ic-tray${stillWaiting > 0 ? ' is-waiting' : ''}`} open={stillWaiting > 0 || undefined}>
      <summary>
        <span className="ic-tray-count">{stillWaiting > 0 ? stillWaiting : rows.length}</span>
        <span className="ic-tray-title">
          {stillWaiting > 0
            ? 'Pages that need you. We do not guess: choose whose they are, or leave them out.'
            : `All pages are placed or left out. ${rows.length} choice${rows.length === 1 ? '' : 's'} of yours (change)`}
        </span>
      </summary>
      <ul>
        {rows.map(({ id, kind, row }) => (
          <li key={id}>
            <span className="ic-tray-file" title={row.path}><FileText aria-hidden="true" /><span>{row.path}</span>{row.pages && <em>{row.pages.length === 1 ? 'page' : 'pages'} {row.pages.join(', ')}</em>}</span>
            <span className="ic-tray-why">{kind === 'unreadable' ? 'Could not be read: ' : ''}{row.reason}</span>
            <Select compact aria-label={`Whose is ${row.path}`} disabled={running}
              value={placement[id] === undefined ? '' : placement[id] === '' ? '__out__' : placement[id]}
              onChange={(e) => onPlace(id, e.target.value === '' ? undefined : e.target.value === '__out__' ? '' : e.target.value)}>
              <option value="">Choose…</option>
              {result.people.map((person) => <option key={person.key} value={person.key}>{person.name}</option>)}
              <option value="__out__">Leave out</option>
            </Select>
          </li>
        ))}
      </ul>
    </details>
  )
})
