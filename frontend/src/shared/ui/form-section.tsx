import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * A titled block of a dialog or page form: a soft icon tile, a title and a one-line hint, a hairline, then the
 * fields on one grid (1, 2 or 3 columns from the tablet width up; one column on a phone). Every dialog uses this one
 * block, so sections look and space the same everywhere. Styles: styles/forms.css (.ui-fs).
 *
 * A field that needs the whole row takes `sm:col-span-2` (or `sm:col-span-3`) as usual.
 */
export function FormSection({
  icon,
  title,
  hint,
  cols = 2,
  className,
  children,
}: {
  icon?: ReactNode
  title: string
  hint?: string
  cols?: 1 | 2 | 3
  className?: string
  children: ReactNode
}) {
  return (
    <section className={cn('ui-fs', className)}>
      <header className="ui-fs-head">
        {icon && <span className="ui-fs-icon" aria-hidden="true">{icon}</span>}
        <div className="ui-fs-title">
          <h3>{title}</h3>
          {hint && <p>{hint}</p>}
        </div>
      </header>
      <div className={cn('ui-fs-grid', cols === 2 && 'is-two', cols === 3 && 'is-three')}>{children}</div>
    </section>
  )
}
