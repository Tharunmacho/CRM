import type { ReactNode } from 'react'

/**
 * The header every block of the working page shares, whoever draws it (it mirrors the Fill details sections): an icon chip, the title,
 * an optional one-line hint, and a chip or meter at the right, over a hairline. The web layout carries no section numbers; the paper
 * form, the preview and the side panel keep theirs.
 */
export function SectionHeader({ icon, title, hint, chip, id, compact }: {
  icon: ReactNode
  title: string
  hint?: string
  /** What sits at the right: a count, a progress meter, a clock. */
  chip?: ReactNode
  id?: string
  /** The smaller header of the assignment card. */
  compact?: boolean
}) {
  return (
    <header className={compact ? 'af-sh is-compact' : 'af-sh'}>
      <span className="af-sh-tile" aria-hidden="true">{icon}</span>
      <div className="af-sh-title">
        <h2 id={id}>{title}</h2>
        {hint && <p>{hint}</p>}
      </div>
      {chip && <div className="af-sh-chip">{chip}</div>}
    </header>
  )
}
