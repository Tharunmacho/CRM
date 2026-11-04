import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * A form section card: icon tile, bold title, one-line subtitle, thin divider, then the body.
 * Props: `icon`, `title`, `subtitle?`, `action?` (a button on the right, e.g. "+ Add field"), `children`, `id?`, `className?`.
 */
export function SectionCard({ icon: Icon, title, subtitle, action, id, className, children }: {
  icon: LucideIcon; title: string; subtitle?: string; action?: ReactNode; id?: string; className?: string; children: ReactNode
}) {
  return (
    <section id={id} className={cn('afp-card', className)}>
      <header className="afp-card-head">
        <span className="afp-card-icon" aria-hidden="true"><Icon /></span>
        <div className="afp-card-title"><h2>{title}</h2>{subtitle && <p>{subtitle}</p>}</div>
        {action && <div className="afp-card-action">{action}</div>}
      </header>
      <div className="afp-card-body">{children}</div>
    </section>
  )
}

/**
 * The labelled field grid: 4 columns on desktop, 2 on tablet, 1 on phones. Children are `GridField`s;
 * give a field `span={2}` / `span={4}` to widen it.
 */
export function FieldGrid({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cn('afp-grid', className)}>{children}</div>
}

/** A label above a control, with a required asterisk, an optional inline slot ("Same as current" checkbox) and a hint/error line. */
export function GridField({ label, required, span = 1, hint, error, inline, children }: {
  label: string; required?: boolean; span?: 1 | 2 | 3 | 4; hint?: ReactNode; error?: string; inline?: ReactNode; children: ReactNode
}) {
  return (
    <div className={cn('afp-field', `span-${span}`, error && 'has-error')}>
      <div className="afp-field-head">
        <span className="afp-label">{label}{required && <span className="afp-req" aria-hidden="true"> *</span>}</span>
        {inline && <span className="afp-inline">{inline}</span>}
      </div>
      {children}
      {error ? <p className="afp-hint is-error" role="alert">{error}</p> : hint ? <p className="afp-hint">{hint}</p> : null}
    </div>
  )
}

/** A dashed red card for a required section that is still empty. Props: `title`, `text`, `action?`. */
export function EmptyRequired({ title, text, action }: { title: string; text: string; action?: ReactNode }) {
  return <div className="afp-empty" role="status"><b>{title}</b><p>{text}</p>{action}</div>
}
