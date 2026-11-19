import { useId, useState } from 'react'
import { CheckCircle2, ChevronDown, Pencil, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { documentLabel } from '@/shared/model/document-slots'
import { documentsCountText } from '../document-rows'
import { ringLabel } from '../paper-model'
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


/** Completeness and the checklist of what is still missing, with a one-click way to fix each item (the section navigator is the tab bar above the form). */
export function SidePanel({
  percent,
  missing,
  documents,
  canEdit,
  onUpload,
  onFill,
}: {
  percent: number
  missing: MissingItem[]
  /** How many of the documents that are unlocked and not optional have arrived. */
  documents: { uploaded: number; total: number; percent: number }
  canEdit: boolean
  onUpload: (kind: DocumentKind) => void
  onFill: (item: MissingItem) => void
}) {
  // Beside the form the list is always open; on a narrow screen it is one button ("7 missing") that opens it.
  const [listOpen, setListOpen] = useState(false)
  const listId = useId()
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
        {missing.length > 0 && (
          <button type="button" className="af-missing-toggle" aria-expanded={listOpen} aria-controls={listId} onClick={() => setListOpen((open) => !open)}>
            {missing.length} still missing<ChevronDown aria-hidden="true" />
          </button>
        )}
      </div>

      {missing.length > 0 && (
        <div className={cn('af-card af-missing-card', listOpen && 'is-open')} id={listId}>
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
    </aside>
  )
}
