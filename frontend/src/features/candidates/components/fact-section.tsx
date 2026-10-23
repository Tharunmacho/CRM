import type { ReactNode } from 'react'
import { cn } from '@/shared/lib/cn'
import type { ProfileRow } from '../profile-view'

export type Fact = [label: string, value: ReactNode]

export const isFilled = (v: ReactNode) => v !== null && v !== undefined && v !== false && v !== ''

/**
 * The one card every profile section uses: a small letter-spaced heading with a thin dark rule
 * beneath it, then the content. `meta` is a quiet line under the heading.
 */
export function ProfileCard({
  title,
  meta,
  action,
  children,
  className,
  id,
}: {
  title: ReactNode
  meta?: ReactNode
  action?: ReactNode
  children: ReactNode
  className?: string
  id?: string
}) {
  return (
    <section id={id} className={cn('min-w-0 rounded-2xl border border-rule-soft bg-white p-5 shadow-soft sm:p-7', className)}>
      <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2">
        <div className="min-w-0">
          <h2 className="text-[14px] font-semibold uppercase leading-5 tracking-[0.16em] text-ink-700">{title}</h2>
          {meta && <p className="mt-1 text-[15px] leading-6 text-ink-500">{meta}</p>}
        </div>
        {action && <div className="flex shrink-0 flex-wrap items-center gap-2">{action}</div>}
      </header>
      <div className="mt-3 h-px bg-ink-700" />
      <div>{children}</div>
    </section>
  )
}

/** A titled strip inside a card, separated from the content above by a thin rule. */
export function CardBlock({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mt-5 border-t border-rule-soft pt-5">
      <p className="mb-3 text-[14px] font-semibold uppercase tracking-[0.12em] text-ink-600">{title}</p>
      {children}
    </div>
  )
}

/**
 * Label on the left (about a third of the row), bold value on the right, a thin divider between
 * rows. Pairs without a value are dropped.
 */
export function FactGrid({ facts }: { facts: Fact[] }) {
  const shown = facts.filter(([, v]) => isFilled(v))
  if (shown.length === 0) return null
  return (
    <dl className="divide-y divide-rule-soft">
      {shown.map(([label, value]) => (
        <div key={label} className="grid min-w-0 gap-1 py-3 sm:grid-cols-[35%_minmax(0,1fr)] sm:gap-6">
          <dt className="text-[16px] leading-6 text-ink-600">{label}</dt>
          {/* Wraps rather than truncating: addresses and names run long. */}
          <dd className="min-w-0 break-words text-[17px] font-semibold leading-6 text-ink-700">{value}</dd>
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
    <a href={url} target="_blank" rel="noreferrer" className="break-all text-navy-700 hover:underline">
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

export function BulletSection({ title, items }: { title: string; items?: string[] }) {
  const shown = (items ?? []).map((s) => s.trim()).filter(Boolean)
  if (shown.length === 0) return null
  return (
    <ProfileCard title={title}>
      <ul className="list-disc space-y-2.5 pb-1 pl-6 pt-4 text-[17px] leading-7 text-ink-700 marker:text-ink-400">
        {shown.map((s, i) => (
          <li key={i} className="break-words pl-1">{s}</li>
        ))}
      </ul>
    </ProfileCard>
  )
}
