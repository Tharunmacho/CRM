import { useId } from 'react'
import { CheckCircle2, MessageSquareText, Pencil, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { documentLabel } from '@/shared/model/document-slots'
import { missingBySection } from '../form-model'
import { documentsCountText } from '../document-rows'
import { paperSections, ringLabel } from '../paper-model'
import type { DocumentKind, MissingItem } from '../types'

/** The percentage as one centred label inside the ring ("5%", never a number and a sign on two lines). */
function Ring({ percent }: { percent: number }) {
  const radius = 34
  const circumference = 2 * Math.PI * radius
  const label = ringLabel(percent)
  const gradient = useId()
  const value = Number.parseInt(label, 10)
  return (
    <div className="af-ring" role="img" aria-label={`${value} percent complete`}>
      <svg viewBox="0 0 80 80" aria-hidden="true">
        <defs>
          <linearGradient id={gradient} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0%" style={{ stopColor: 'var(--primary-deep)' }} />
            <stop offset="100%" style={{ stopColor: 'color-mix(in srgb, var(--primary) 55%, #7dd3fc)' }} />
          </linearGradient>
        </defs>
        <circle cx="40" cy="40" r={radius} className="af-ring-track" />
        <circle cx="40" cy="40" r={radius} className={cn('af-ring-bar', value >= 100 && 'is-full')} stroke={value >= 100 ? undefined : `url(#${gradient})`} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - value / 100)} />
      </svg>
      <span className="af-ring-label tnum" aria-hidden="true">{label}</span>
    </div>
  )
}

/** Completeness and the checklist of what is still missing, with a one-click way to fix each item. */
export function SidePanel({
  percent,
  missing,
  documents,
  canEdit,
  onUpload,
  onFill,
  onJump,
  notes,
}: {
  percent: number
  missing: MissingItem[]
  /** How many of the documents that are unlocked and not optional have arrived. */
  documents: { uploaded: number; total: number; percent: number }
  canEdit: boolean
  onUpload: (kind: DocumentKind) => void
  onFill: (item: MissingItem) => void
  onJump: (section: number) => void
  /** The recruiter notes entry of the section list: how many notes there are, and where it leads. */
  notes?: { count: number; overdue: boolean; onOpen: () => void }
}) {
  const grouped = missingBySection(missing)
  const complete = percent >= 100
  return (
    <aside className="af-side" aria-label="Form completeness">
      <div className="af-card af-complete">
        <div className="af-complete-main">
          <Ring percent={percent} />
          <div className="af-complete-text">
            <p className="af-complete-title">{complete ? 'Form complete' : 'Form completeness'}</p>
            <p className="af-complete-sub">{missing.length === 0 ? 'Everything required is filled in.' : `${missing.length} ${missing.length === 1 ? 'item is' : 'items are'} still missing`}</p>
          </div>
        </div>
        <div className="af-complete-docs">
          <p className="af-complete-docs-line"><span className="tnum">{documentsCountText(documents)}</span></p>
          <span className={cn('af-docs-bar', documents.total > 0 && documents.uploaded === documents.total && 'is-full')} role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={documents.percent} aria-label="Documents uploaded">
            <i style={{ width: `${documents.percent}%` }} />
          </span>
        </div>
      </div>

      {missing.length > 0 && (
        <div className="af-card">
          <h2 className="af-side-h">Still missing</h2>
          <ul className={cn('af-missing', canEdit && 'has-action')}>
            {missing.map((item) => (
              <li key={item.key}>
                <i className="af-missing-dot" aria-hidden="true" />
                <span>{item.kind === 'document' ? `${documentLabel(item.key.replace('documents.', ''))} not uploaded` : item.label}</span>
                {canEdit && (item.kind === 'document' ? (
                  <button type="button" onClick={() => onUpload(item.key.replace('documents.', ''))}><Upload aria-hidden="true" />Upload</button>
                ) : (
                  <button type="button" onClick={() => onFill(item)}><Pencil aria-hidden="true" />Fill in</button>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {missing.length === 0 && (
        <div className="af-card af-allgood"><CheckCircle2 aria-hidden="true" />{documents.uploaded < documents.total
          ? 'Details for this stage are complete. Document collection is tracked separately.'
          : 'All required details and documents are on the form.'}</div>
      )}

      <nav className="af-card af-nav" aria-label="Form sections">
        <h2 className="af-side-h">Sections</h2>
        <ol>
          {paperSections().map(({ no, title }) => {
            const count = grouped.get(no)?.length ?? 0
            return (
              <li key={no}>
                <button type="button" onClick={() => onJump(no)}>
                  <span className="af-nav-no">{no}</span>
                  <span className="af-nav-title">{title}</span>
                  {count > 0 && <span className="af-nav-count">{count}</span>}
                </button>
              </li>
            )
          })}
          {notes && (
            <li>
              <button type="button" onClick={notes.onOpen}>
                <span className="af-nav-no is-icon"><MessageSquareText aria-hidden="true" /></span>
                <span className="af-nav-title">Recruiter notes</span>
                {(notes.count > 0 || notes.overdue) && <span className={cn('af-nav-count', notes.overdue ? 'is-over' : 'is-plain')}>{notes.overdue ? '!' : notes.count}</span>}
              </button>
            </li>
          )}
        </ol>
      </nav>
    </aside>
  )
}
