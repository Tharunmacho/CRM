import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { ArrowLeft } from 'lucide-react'
import { cn } from '@/shared/lib/cn'

/**
 * PageHeader: the band at the top of a screen. Title 28/34, one plain-English sentence, actions on the right (40px).
 *
 * Props
 *  - title        node
 *  - description? node   one sentence saying what the screen is for
 *  - route?       string | node   small route chip above the title (e.g. "Stage 4 of 11"); a string gets the route stub
 *  - actions?     node   primary action last; wraps under the title on phones
 *  - back?        { label, to?, onClick? }   a quiet "back" link above the header
 *  - children?    node   extra row under the header (tabs, summary)
 */
export function PageHeader({ title, description, route, actions, back, children, className }: {
  title: ReactNode
  description?: ReactNode
  route?: ReactNode
  actions?: ReactNode
  back?: { label: string; to?: string; onClick?: () => void }
  children?: ReactNode
  className?: string
}) {
  return (
    <header className={cn('v2-page-header', className)}>
      {back && (back.to
        ? <Link to={back.to} className="v2-back" onClick={back.onClick}><ArrowLeft size={15} aria-hidden="true" />{back.label}</Link>
        : <button type="button" className="v2-back" onClick={back.onClick}><ArrowLeft size={15} aria-hidden="true" />{back.label}</button>)}
      <div className="v2-page-header-row">
        <div className="v2-page-header-copy">
          {route != null && (typeof route === 'string' ? <p className="v2-route-chip"><i aria-hidden="true" />{route}</p> : <div className="v2-page-header-route">{route}</div>)}
          <h1 className="v2-page-title">{title}</h1>
          {description && <p className="v2-page-desc">{description}</p>}
        </div>
        {actions && <div className="v2-page-actions">{actions}</div>}
      </div>
      {children}
    </header>
  )
}
