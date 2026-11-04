import { CheckCircle2, CircleAlert, Pencil, Upload } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { SECTION_TITLES, missingBySection } from '../form-model'
import type { DocumentKind, MissingItem } from '../types'

function Ring({ percent }: { percent: number }) {
  const radius = 34
  const circumference = 2 * Math.PI * radius
  return (
    <div className="af-ring" role="img" aria-label={`${percent} percent complete`}>
      <svg viewBox="0 0 80 80" aria-hidden="true">
        <circle cx="40" cy="40" r={radius} className="af-ring-track" />
        <circle cx="40" cy="40" r={radius} className={cn('af-ring-bar', percent >= 100 && 'is-full')} strokeDasharray={circumference} strokeDashoffset={circumference * (1 - percent / 100)} />
      </svg>
      <span className="tnum">{percent}<small>%</small></span>
    </div>
  )
}

/** Completeness and the checklist of what is still missing, with a one-click way to fix each item. */
export function SidePanel({
  percent,
  missing,
  canEdit,
  onUpload,
  onFill,
  onJump,
  showExtras,
}: {
  percent: number
  missing: MissingItem[]
  canEdit: boolean
  onUpload: (kind: DocumentKind) => void
  onFill: (item: MissingItem) => void
  onJump: (section: number) => void
  showExtras?: boolean
}) {
  const grouped = missingBySection(missing)
  return (
    <aside className="af-side" aria-label="Form completeness">
      <div className="af-card af-complete">
        <Ring percent={percent} />
        <div>
          <p className="af-complete-title">{percent >= 100 ? 'Form complete' : 'Form completeness'}</p>
          <p className="af-complete-sub">{missing.length === 0 ? 'Every field is filled.' : `${missing.length} ${missing.length === 1 ? 'item' : 'items'} still missing`}</p>
        </div>
      </div>

      {missing.length > 0 && (
        <div className="af-card">
          <h2 className="af-side-h">Still missing</h2>
          <ul className="af-missing">
            {missing.map((item) => (
              <li key={item.key}>
                <CircleAlert aria-hidden="true" />
                <span>{item.kind === 'document' ? item.message || item.label : item.label}</span>
                {canEdit && (item.kind === 'document' ? (
                  <button type="button" onClick={() => onUpload(item.key.replace('documents.', '') as DocumentKind)}><Upload aria-hidden="true" />Upload now</button>
                ) : (
                  <button type="button" onClick={() => onFill(item)}><Pencil aria-hidden="true" />Fill in</button>
                ))}
              </li>
            ))}
          </ul>
        </div>
      )}
      {missing.length === 0 && (
        <div className="af-card af-allgood"><CheckCircle2 aria-hidden="true" />All required details are on the form.</div>
      )}

      <nav className="af-card af-nav" aria-label="Form sections">
        <h2 className="af-side-h">Sections</h2>
        <ol>
          {Object.entries(SECTION_TITLES).filter(([no]) => Number(no) <= 7 || showExtras).map(([no, title]) => {
            const count = grouped.get(Number(no))?.length ?? 0
            return (
              <li key={no}>
                <button type="button" onClick={() => onJump(Number(no))}>
                  <span className="af-nav-no">{no}</span>
                  <span className="af-nav-title">{title}</span>
                  {count > 0 && <span className="af-nav-count">{count}</span>}
                </button>
              </li>
            )
          })}
        </ol>
      </nav>
    </aside>
  )
}
