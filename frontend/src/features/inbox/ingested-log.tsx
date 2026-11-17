import { useEffect, useMemo, useRef, useState } from 'react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CircleSlash, Clock, Inbox, Loader2, Mail, Paperclip, RefreshCw, Settings2, XCircle } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { EmptyState } from '@/shared/ui/display'
import { Button, SearchInput } from '@/shared/ui/primitives'
import { INGESTED_PAGE_SIZE, type IngestedItem, type IngestedStatusFilter } from './ingested-api'
import { useIngestedList, type useIngestedSummary } from './use-ingested'
import {
  STATUS_FILTERS, accountHealth, accountTitle, accountTotal, attachmentChips, candidateLabel, canNext, canPrev, chipCounts, countText,
  exactTime, fromLine, pageCount, pagingText, relativeTime, resultText, senderName, statusBadge,
} from './ingested-view'
import { SenderAvatar } from './mail-parts'
import './ingested.css'

const BADGE_ICON = { green: CheckCircle2, amber: AlertTriangle, red: XCircle, grey: CircleSlash, blue: Clock } as const

/** One row of the intake log: who sent what, and what became of it. Opening it shows the original email. */
function IngestedRow({ item, showMailbox, onOpen, onOpenCandidate, openingCandidate }: {
  item: IngestedItem
  showMailbox: boolean
  onOpen: (item: IngestedItem) => void
  onOpenCandidate: (id: string) => void
  openingCandidate: boolean
}) {
  const badge = statusBadge(item)
  const Icon = BADGE_ICON[badge.tone]
  const result = resultText(item)
  const files = attachmentChips(item)
  const who = candidateLabel(item.candidate)
  const sender = senderName(item.from)
  return <li className={cn('ig-row', `is-${badge.tone}`)}>
    <button type="button" className="ig-open" onClick={() => onOpen(item)} title="Open the original email">
      <SenderAvatar name={sender} />
      <span className="ig-main">
        <span className="ig-subject" title={item.subject || '(No subject)'}>{item.subject || '(No subject)'}</span>
        <span className="ig-from" title={fromLine(item.from)}>{fromLine(item.from)}</span>
        {(files.shown.length > 0 || showMailbox) && <span className="ig-meta">
          {files.shown.map((name, index) => <span key={`${name}-${index}`} className="ig-file" title={name}><Paperclip size={11} aria-hidden="true" /><span>{name}</span></span>)}
          {files.extra > 0 && <span className="ig-file is-more" title={`${files.extra} more attachment${files.extra === 1 ? '' : 's'}`}>+{files.extra}</span>}
          {showMailbox && <span className="ig-tag" title={`Received by ${item.accountAddress}`}><Mail size={11} aria-hidden="true" /><span>{item.accountAddress}</span></span>}
        </span>}
      </span>
    </button>
    <div className="ig-result">
      <time className="ig-time" dateTime={item.receivedAt} title={exactTime(item.receivedAt)}>{relativeTime(item.receivedAt)}</time>
      <span className={cn('ig-badge', `is-${badge.tone}`)}>
        {badge.spinner ? <Loader2 size={13} className="ig-spin" aria-hidden="true" /> : <Icon size={13} aria-hidden="true" />}
        <span>{badge.label}</span>
      </span>
      {item.status === 'ingested' && item.candidate && <a className="ig-cand" href={`/candidates/${encodeURIComponent(item.candidate.id)}`} aria-disabled={openingCandidate || undefined} title="Open the candidate"
        onClick={(event) => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.button !== 0) return; event.preventDefault(); if (!openingCandidate) onOpenCandidate(item.candidate!.id) }}>{who || 'Open candidate'}</a>}
      {result.detail && <span className="ig-note" title={result.detail}>{result.detail}</span>}
    </div>
  </li>
}

const Skeleton = () => <div role="status" aria-label="Loading emails" className="mb-scroll ig-skeleton">{[0, 1, 2, 3, 4, 5].map((row) => <div key={row} className="mb-skel-row"><span /><div><i /><i /></div></div>)}</div>

/**
 * "From email": the candidate-intake log. Header chips (result counts) double as status filters; the mailbox strip filters by account
 * and scales to many mailboxes by scrolling sideways; the list is paged by the server (25 per page).
 */
export function IngestedLog({ summary, identityId, revision, admin, openingCandidate, onOpenMessage, onOpenCandidate, onManage }: {
  summary: ReturnType<typeof useIngestedSummary>
  identityId: string
  revision: number
  admin: boolean
  openingCandidate: boolean
  onOpenMessage: (messageId: string, accountId: string) => void
  onOpenCandidate: (id: string) => void
  onManage: () => void
}) {
  const [account, setAccount] = useState('all')
  const [status, setStatus] = useState<IngestedStatusFilter>('all')
  const [query, setQuery] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const accounts = summary.data?.accounts ?? []
  const stamp = summary.data ? JSON.stringify([summary.data.ingested, summary.data.review, summary.data.failed, summary.data.ignored, summary.data.pending, summary.data.accounts.length]) : ''
  const list = useIngestedList({ account, status, q, page, pageSize: INGESTED_PAGE_SIZE }, true, identityId, revision, stamp)
  const counts = chipCounts(summary.data)
  // Search waits 300 ms after the last key, and any new search goes back to page one.
  const settled = useRef('')
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = query.trim()
      if (next === settled.current) return
      settled.current = next; setQ(next); setPage(0)
    }, 300)
    return () => window.clearTimeout(timer)
  }, [query])
  // A page that no longer exists (the log shrank) goes back to the last one that does.
  useEffect(() => {
    if (!list.data || list.loading || list.data.items.length > 0 || page === 0) return
    setPage(list.data.total > 0 ? pageCount(list.data.total, list.data.pageSize || INGESTED_PAGE_SIZE) - 1 : 0)
  }, [list.data, list.loading, page])
  // A mailbox that was removed falls back to "All accounts".
  useEffect(() => { if (account !== 'all' && summary.data && !accounts.some((item) => item.id === account)) { setAccount('all'); setPage(0) } }, [summary.data, account])
  // Keep the chosen mailbox chip in view when there are more chips than the strip can show.
  const strip = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const chip = strip.current?.querySelector<HTMLElement>('[aria-pressed="true"]')
    if (chip && strip.current) strip.current.scrollTo({ left: Math.max(0, chip.offsetLeft - 40), behavior: 'smooth' })
  }, [account])

  const choose = (next: IngestedStatusFilter) => { setStatus(status === next ? 'all' : next); setPage(0) }
  const pickAccount = (id: string) => { setAccount(id); setPage(0) }
  const clearFilters = () => { setStatus('all'); setAccount('all'); setQuery(''); settled.current = ''; setQ(''); setPage(0) }
  const retry = () => { summary.refresh(); list.refresh() }

  const items = list.data?.items ?? []
  const total = list.data?.total ?? 0
  const pageSize = list.data?.pageSize || INGESTED_PAGE_SIZE
  const filtered = status !== 'all' || account !== 'all' || q !== ''
  const firstLoad = !list.data && (!list.error || !summary.loaded)
  const summaryLine = summary.data ? `${countText(counts.ingested)} candidates created, ${countText(summary.data.review)} need review, ${countText(summary.data.failed)} failed, ${countText(summary.data.pending)} waiting.` : ''
  const paging = useMemo(() => pagingText(list.data?.page ?? page, pageSize, total, items.length), [list.data?.page, page, pageSize, total, items.length])
  const allTotal = counts.all
  const unclearedError = !!list.error && !list.data && summary.loaded

  return <section className="mb-card ig-card" aria-label="Emails turned into candidates">
    <header className="ig-head">
      <div className="ig-chips" role="group" aria-label="Filter by result">
        {STATUS_FILTERS.map((filter) => <button key={filter.key} type="button" className={cn('ig-chip', `is-${filter.key}`, (status === filter.key) && 'is-active')} aria-pressed={status === filter.key} title={filter.hint} onClick={() => filter.key === 'all' ? (setStatus('all'), setPage(0)) : choose(filter.key)}>
          <span className="ig-chip-label">{filter.label}</span><span className="ig-chip-count">{countText(counts[filter.key])}</span>
        </button>)}
      </div>
      <p className="sr-only" role="status" aria-live="polite">{summaryLine}</p>
      <div className="ig-filterrow">
        <div className="ig-accts-wrap">
          <div ref={strip} className="ig-accts" role="group" aria-label="Filter by mailbox">
            <button type="button" className={cn('ig-acct', account === 'all' && 'is-active')} aria-pressed={account === 'all'} onClick={() => pickAccount('all')}>
              <Inbox size={14} aria-hidden="true" /><span className="ig-acct-name">All accounts</span><span className="ig-acct-count">{countText(allTotal)}</span>
            </button>
            {accounts.map((item) => {
              const health = accountHealth(item)
              return <button key={item.id} type="button" className={cn('ig-acct', account === item.id && 'is-active')} aria-pressed={account === item.id} title={accountTitle(item)} onClick={() => pickAccount(item.id)}>
                <i className={cn('ig-dot', `is-${health.tone}`)} role="img" aria-label={health.label} />
                <span className="ig-acct-name">{item.address}</span>
                {!item.autoImport && item.connectionStatus !== 'disconnected' && <span className="ig-acct-off">Auto-import off</span>}
                <span className="ig-acct-count">{countText(accountTotal(item))}</span>
              </button>
            })}
          </div>
        </div>
        {admin && <Button size="sm" icon={<Settings2 size={14} />} onClick={onManage} title="Connect mailboxes and turn Auto-import on or off">Manage mailboxes</Button>}
      </div>
      <SearchInput value={query} onChange={setQuery} placeholder="Search sender, subject, candidate or file" className="ig-search" />
    </header>

    {list.error && list.data && <p role="alert" className="mb-note is-error ig-inline-error">{list.error} Showing the last emails we loaded. <button type="button" onClick={retry}>Try again</button></p>}
    {unclearedError
      ? <div role="alert" className="ig-error"><EmptyState icon={<AlertTriangle size={24} />} title="We could not load the email log" description={`${list.error} Nothing was lost.`} action={<Button variant="primary" icon={<RefreshCw size={15} />} onClick={retry}>Try again</Button>} /></div>
      : firstLoad ? <Skeleton />
      : items.length === 0 && !list.loading
        ? <EmptyState icon={<Inbox size={22} />} title={filtered ? 'No emails match this view' : 'No emails yet'} description={filtered ? 'Try another result, mailbox or search.' : 'No emails have been turned into candidates yet. New resumes that arrive on your connected mailboxes appear here.'} action={filtered ? <Button onClick={clearFilters}>Clear filters</Button> : undefined} />
        : <ul className={cn('mb-scroll ig-rows', list.loading && 'is-loading')} aria-busy={list.loading}>{items.map((item) => <IngestedRow key={item.id} item={item} showMailbox={account === 'all' && accounts.length > 1} onOpen={(row) => onOpenMessage(row.messageId, row.accountId)} onOpenCandidate={onOpenCandidate} openingCandidate={openingCandidate} />)}</ul>}

    <footer className="ig-foot">
      <p className="ig-paging" role="status" aria-live="polite">{list.data ? paging : ''}{list.loading && list.data ? ' · updating…' : ''}</p>
      <div className="mb-pager">
        <Button size="sm" aria-label="Previous page" disabled={!canPrev(page) || list.loading} onClick={() => setPage(Math.max(0, page - 1))}><ChevronLeft size={15} /></Button>
        <Button size="sm" aria-label="Next page" disabled={!canNext(page, pageSize, total) || list.loading} onClick={() => setPage(page + 1)}><ChevronRight size={15} /></Button>
      </div>
    </footer>
  </section>
}
