import { useId, type ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'

/**
 * Panel: white surface (16px radius, card shadow + faint border) with a route-stub header.
 *
 * Props
 *  - title?    node     header title (16 semibold). No title = no header
 *  - meta?     node     muted line under the title (one fact)
 *  - actions?  node     right side of the header (buttons, a Segmented control)
 *  - route?    boolean  show the route stub before the title (default true)
 *  - flush?    boolean  no body padding (tables, lists that touch the edges)
 *  - as?       'section' | 'div' | 'aside' | 'article'
 *  - id?, className?, bodyClassName?
 */
export function Panel({ title, meta, actions, route = true, flush = false, as: Tag = 'section', id, className, bodyClassName, children }: {
  title?: ReactNode
  meta?: ReactNode
  actions?: ReactNode
  route?: boolean
  flush?: boolean
  as?: 'section' | 'div' | 'aside' | 'article'
  id?: string
  className?: string
  bodyClassName?: string
  children?: ReactNode
}) {
  const titleId = useId()
  return (
    <Tag id={id} className={cn('v2-panel', className)} aria-labelledby={title ? titleId : undefined}>
      {(title || actions) && (
        <header className="v2-panel-head">
          <div className="v2-panel-heading">
            {route && title && <span className="v2-stub" aria-hidden="true" />}
            <div className="v2-panel-titles">
              {title && <h2 id={titleId} className="v2-panel-title">{title}</h2>}
              {meta && <p className="v2-panel-meta">{meta}</p>}
            </div>
          </div>
          {actions && <div className="v2-panel-actions">{actions}</div>}
        </header>
      )}
      <div className={cn('v2-panel-body', flush && 'is-flush', bodyClassName)}>{children}</div>
    </Tag>
  )
}
