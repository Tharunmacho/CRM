import { useEffect, useState, type KeyboardEvent, type ReactNode } from 'react'
import { ArrowRight } from 'lucide-react'
import { dateLabel, dateTimeLabel } from '@/shared/lib/format'
import type { DashboardTask, OrderMetrics } from '@/features/reports/report-api'
import type { Candidate } from '@/shared/model/types'
import { Avatar, Flag, Pill, SlaMeter, SourceTag, StageMini } from '@/features/dashboard/components/identity'

/** Below this width tables become stacked cards (label/value rows) instead of a squeezed table. */
const NARROW = '(max-width: 1279px)'
export function useNarrow() {
  const [narrow, setNarrow] = useState(() => typeof window !== 'undefined' && window.matchMedia(NARROW).matches)
  useEffect(() => {
    const query = window.matchMedia(NARROW)
    const update = () => setNarrow(query.matches)
    update(); query.addEventListener('change', update)
    return () => query.removeEventListener('change', update)
  }, [])
  return narrow
}

const press = (action: () => void) => ({ role: 'button' as const, tabIndex: 0, onClick: action, onKeyDown: (event: KeyboardEvent) => { if (event.target === event.currentTarget && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); action() } } })

/** A list that shows a first page and reveals more on request. */
function Stack({ children, total, step = 5, label, empty }: { children: (limit: number) => ReactNode; total: number; step?: number; label: string; empty: ReactNode }) {
  const [limit, setLimit] = useState(step)
  if (!total) return <>{empty}</>
  return <div className="dr-stack">
    <ul aria-label={label}>{children(limit)}</ul>
    <div className="dr-stack-foot"><span>Showing {Math.min(limit, total)} of {total}</span>{limit < total && <button className="dr-link" onClick={() => setLimit(limit + step)}>Show more</button>}</div>
  </div>
}

export function TaskCards({ rows, onOpen, showOwner, empty }: { rows: DashboardTask[]; onOpen: (row: DashboardTask) => void; showOwner: boolean; empty: ReactNode }) {
  return <Stack total={rows.length} step={6} label="Tasks" empty={empty}>{limit => rows.slice(0, limit).map(row => <li key={row.id} className={`dr-mcard${row.overdue ? ' is-overdue' : ''}`} {...press(() => onOpen(row))}>
    <div className="dr-mcard-top"><div className="dr-person"><Avatar name={row.candidate} /><div><strong>{row.candidate}</strong><span>{row.title}</span></div></div><Pill tone={row.notUpdated ? 'warn' : 'good'} title={row.notUpdated ? 'No note from the owner yet today' : undefined}>{row.notUpdated ? 'Not updated today' : 'Updated today'}</Pill></div>
    <dl>
      <div><dt>Stage</dt><dd><StageMini no={row.stage} /></dd></div>
      <div><dt>Due</dt><dd className="dr-due"><span className={row.overdue ? 'dr-overdue' : ''}>{dateTimeLabel(row.dueAt)}</span><SlaMeter dueAt={row.dueAt} /></dd></div>
      {showOwner && <div><dt>Assigned to</dt><dd><span className="dr-owner"><Avatar name={row.owner} size="sm" />{row.owner.split(' ')[0]}</span></dd></div>}
    </dl>
  </li>)}</Stack>
}

export function OrderCards({ rows, country, onOpen, empty }: { rows: OrderMetrics[]; country: (row: OrderMetrics) => { city?: string | null; country?: string | null }; onOpen: (row: OrderMetrics) => void; empty: ReactNode }) {
  return <Stack total={rows.length} label="Active orders" empty={empty}>{limit => rows.slice(0, limit).map(row => {
    const place = country(row)
    return <li key={row.id} className="dr-mcard" {...press(() => onOpen(row))}>
      <div className="dr-mcard-top"><div className="dr-copy"><strong>{row.ref} · {row.designation}</strong><small>{row.company} · {row.client}</small></div><span className="dr-place"><Flag country={place.country} />{place.country || 'Not added'}</span></div>
      <div className="dr-progress" title={`${row.deployed} deployed of ${row.openings} openings`}><span><i style={{ width: `${row.openings ? Math.min(100, row.deployed / row.openings * 100) : 0}%` }} /></span><b>{row.deployed}/{row.openings} deployed</b></div>
      <dl><div><dt>Matched</dt><dd>{row.matched}</dd></div><div><dt>Selected</dt><dd>{row.selected}</dd></div></dl>
    </li>
  })}</Stack>
}

/** What the candidate does: designation, else years of experience, else the first skill, else a muted note. */
export function roleLine(candidate: Candidate) {
  const role = candidate.designations[0]
  if (role) return role
  if (candidate.totalExperience > 0) return `${candidate.totalExperience} yr${candidate.totalExperience === 1 ? '' : 's'} experience${candidate.skills[0] ? ` · ${candidate.skills[0]}` : ''}`
  if (candidate.skills[0]) return candidate.skills[0]
  return <span className="dr-muted">No job title yet</span>
}

export function CandidateCards({ rows, status, onOpen }: { rows: Candidate[]; status: (row: Candidate) => { label: string; tone: 'info' | 'warn' | 'good' | 'bad' | 'muted' | 'accent' }; onOpen: (row: Candidate) => void }) {
  return <ul className="dr-stack-list" aria-label="Latest candidates">{rows.map(row => <li key={row.id} className="dr-mcard" {...press(() => onOpen(row))}>
    <div className="dr-mcard-top"><div className="dr-person"><Avatar name={row.name} /><div><strong>{row.name}</strong><span>{roleLine(row)}</span></div></div><Pill tone={status(row).tone}>{status(row).label}</Pill></div>
    <dl><div><dt>Source</dt><dd><SourceTag source={row.source} /></dd></div><div><dt>Added</dt><dd>{dateLabel(row.addedAt)}</dd></div></dl>
    <span className="dr-mcard-open">Open<ArrowRight size={14} /></span>
  </li>)}</ul>
}
