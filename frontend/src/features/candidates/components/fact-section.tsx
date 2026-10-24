import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import type { ProfileRow } from '../profile-view'

export type Fact = [label: string, value: ReactNode]

export const isFilled = (v: ReactNode) => v !== null && v !== undefined && v !== false && v !== ''

/**
 * The one card every profile section uses: a flat card with a hairline under its header (optional
 * soft icon circle, title, quiet description, actions) and the content below. Styles: candidate-v2.css.
 */
export function ProfileCard({
  title,
  meta,
  action,
  icon,
  accent,
  children,
  className,
  id,
}: {
  title: ReactNode
  meta?: ReactNode
  action?: ReactNode
  icon?: ReactNode
  accent?: boolean
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section id={id} className={cn('cp-card', accent && 'is-accent', className)}>
      <header>
        <div className="cp-card-title">
          {icon && <span className="cp-card-icon" aria-hidden="true">{icon}</span>}
          <div className="min-w-0">
            <h2>{title}</h2>
            {meta && <p className="cp-card-meta">{meta}</p>}
          </div>
        </div>
        {action && <div className="cp-card-action">{action}</div>}
      </header>
      <div className="cp-card-body">{children}</div>
    </section>
  )
}

/** A titled strip inside a card, separated from the content above by a thin rule. */
export function CardBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="cp-block">
      <p className="cp-block-title">{title}</p>
      {children}
    </div>
  )
}

/**
 * Label above the value in a soft tile, two tiles to a row on wide screens. Pairs without a value
 * are dropped; long values (addresses, names) wrap rather than truncate.
 */
export function FactGrid({ facts }: { facts: Fact[] }) {
  const shown = facts.filter(([, v]) => isFilled(v))
  if (shown.length === 0) return null
  return (
    <dl className="cp-facts">
      {shown.map(([label, value], index) => (
        // A last tile left alone in a two-column grid takes the full row, so no cell is orphaned.
        <div key={label} className={cn('cp-fact', shown.length % 2 === 1 && index === shown.length - 1 && 'is-wide')}>
          <dt>{label}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  )
}

/**
 * A titled group of label/value pairs. Pairs without a value are dropped, and a group
 * left with none is not drawn at all — an empty heading is noise on a profile.
 */
export function FactSection({ title, meta, action, facts }: { title: string; meta?: ReactNode; action?: ReactNode; facts: Fact[] }) {
  if (!facts.some(([, v]) => isFilled(v))) return null
  return (
    <ProfileCard title={title} meta={meta} action={action}>
      <FactGrid facts={facts} />
    </ProfileCard>
  )
}

function ExternalLink({ href }: { href: string }) {
  const url = /^https?:\/\//i.test(href) ? href : `https://${href}`
  return (
    <a href={url} target="_blank" rel="noreferrer" className="cp-link break-all">
      {href}
    </a>
  )
}

/** Lets a long address wrap at the "@" rather than mid-word in a narrow column. */
function EmailAddress({ value }: { value: string }) {
  const at = value.indexOf('@')
  if (at <= 0) return <>{value}</>
  return (
    <>
      {value.slice(0, at)}
      <wbr />
      {value.slice(at)}
    </>
  )
}

function rowValue(r: ProfileRow): ReactNode {
  switch (r.kind) {
    case 'ident':
      return <span className="ident">{r.value}</span>
    case 'email':
      return <EmailAddress value={r.value} />
    case 'link':
      return r.items && r.items.length > 1 ? (
        <span className="flex flex-col">
          {r.items.map((i) => (
            <ExternalLink key={i} href={i} />
          ))}
        </span>
      ) : (
        <ExternalLink href={r.items?.[0] ?? r.value} />
      )
    default:
      return r.value
  }
}

/** The rows the profile model produced, as a label/value list. */
export function ProfileRows({ rows }: { rows: ProfileRow[] }) {
  return <FactGrid facts={rows.map((r): Fact => [r.label, rowValue(r)])} />
}

export function BulletSection({ title, items, icon }: { title: string; items?: string[]; icon?: ReactNode }) {
  const shown = (items ?? []).map((s) => s.trim()).filter(Boolean)
  if (shown.length === 0) return null
  return (
    <ProfileCard title={title} icon={icon}>
      <ul className="cp-bullets">
        {shown.map((s, i) => (
          <li key={i}>{s}</li>
        ))}
      </ul>
    </ProfileCard>
  )
}
