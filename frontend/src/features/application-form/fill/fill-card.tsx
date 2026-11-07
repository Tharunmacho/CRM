import type { ReactNode } from 'react'
import { Check, type LucideIcon } from 'lucide-react'
import { toAddText } from './fill-fields'

/**
 * One flat section of the Fill details view (the page wraps all of them in one card). Every section has the same header:
 * a 32px rounded icon chip, the title (16px semibold), an optional one-line hint, and at the right the actions or chips of
 * that section followed by "N to add" (amber pill) or "Complete" (green pill), then a hairline and the body. The shell uses
 * this same component for the sections it renders itself (Documents, Recruitment process tracking, the assignment card).
 * `missing` is the number of items the section still needs; `counts` says whether it has required items at all.
 */
export function FillCardShell({ no, icon: Icon, title, hint, missing, counts, aside, children }: {
  /** Only for the element id `af-fill-section-<no>` (the paper form's section number); it is not shown. */
  no: number
  /** A lucide icon component (UserRound, IdCard, GraduationCap, BriefcaseBusiness, FolderOpen ...). */
  icon: LucideIcon
  title: string
  hint?: string
  missing: number
  counts: boolean
  /** Chips and buttons in the header, left of the count (the passport upload, the expiry status). */
  aside?: ReactNode
  children: ReactNode
}) {
  const toAdd = toAddText(missing)
  return (
    <section className="aff-card" id={`af-fill-section-${no}`} aria-labelledby={`af-fill-title-${no}`}>
      <header className="aff-card-head">
        <span className="aff-tile" aria-hidden="true"><Icon /></span>
        <div className="aff-card-title">
          <h2 id={`af-fill-title-${no}`}>{title}</h2>
          {hint && <p>{hint}</p>}
        </div>
        <div className="aff-card-aside">
          {aside}
          {toAdd ? <span className="aff-count is-missing">{toAdd}</span> : counts ? <span className="aff-count is-done"><Check aria-hidden="true" />Complete</span> : null}
        </div>
      </header>
      <div className="aff-card-body">{children}</div>
    </section>
  )
}
