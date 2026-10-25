import { useCallback, useEffect, useRef, useState } from 'react'
import { AlertTriangle, ChevronLeft, ChevronRight, Plus } from 'lucide-react'
import type { InboxAccount } from '@/shared/api/client'
import { cn } from '@/shared/lib/cn'
import { smartTime } from '@/shared/lib/format'
import { Switch } from '@/shared/ui/primitives'
import { PROVIDER_LABEL, isLiveAccount } from './inbox-status'
import { ProviderMark } from './mail-parts'

/**
 * The mailbox strip: one horizontal row of equal-height cards, "All accounts" first and then one
 * card per connected mailbox. Each card carries the provider, last sync, unread count and the
 * per-account "Auto-ingest" switch (PRD: Inbox, per account toggle); only an admin can change the
 * switch, everyone else sees its state. When the cards do not fit, the row scrolls inside itself
 * (snap points and arrow buttons); the page never scrolls sideways.
 */
export function AccountRail({ accounts, selectedId, admin, busy, onSelect, onToggleAuto, onConnect }: {
  accounts: InboxAccount[]
  selectedId: string
  admin: boolean
  busy: boolean
  onSelect: (id: string) => void
  onToggleAuto: (account: InboxAccount, enabled: boolean) => void
  onConnect?: () => void
}) {
  const unread = accounts.reduce((sum, item) => sum + (item.unreadCount ?? 0), 0)
  const live = accounts.filter(isLiveAccount).length
  const autoOn = accounts.filter((item) => isLiveAccount(item) && item.autoIngest).length
  const list = useRef<HTMLUListElement>(null)
  const [edge, setEdge] = useState({ left: false, right: false })
  const measure = useCallback(() => {
    const row = list.current
    if (!row) return
    setEdge({ left: row.scrollLeft > 2, right: row.scrollLeft + row.clientWidth < row.scrollWidth - 2 })
  }, [])
  useEffect(() => {
    measure()
    const row = list.current
    if (!row) return
    const observer = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(measure)
    observer?.observe(row)
    return () => observer?.disconnect()
  }, [measure, accounts.length])
  // Keep the chosen mailbox card on screen when there are more cards than the row can show.
  useEffect(() => {
    const row = list.current
    const card = row?.querySelector<HTMLElement>('[aria-pressed="true"]')?.closest<HTMLElement>('.mb-acct')
    if (!card || !row || row.scrollWidth <= row.clientWidth + 1) return
    const left = card.offsetLeft - row.offsetLeft
    if (left < row.scrollLeft || left + card.offsetWidth > row.scrollLeft + row.clientWidth) row.scrollTo({ left: Math.max(0, left - 8), behavior: 'smooth' })
  }, [selectedId])
  const nudge = (direction: -1 | 1) => list.current?.scrollBy({ left: direction * Math.max(240, (list.current?.clientWidth ?? 600) * 0.7), behavior: 'smooth' })
  return <nav className={cn('mb-rail', edge.left && 'has-left', edge.right && 'has-right')} aria-label="Mailboxes">
    <ul ref={list} className="mb-accts mb-accts-scroll" role="group" aria-label="Mailboxes" onScroll={measure}>
      <li className={cn('mb-acct', selectedId === '' && 'is-active')}>
        <button type="button" className="mb-acct-main" aria-pressed={selectedId === ''} onClick={() => onSelect('')}>
          <ProviderMark provider="all" />
          <span className="mb-acct-title">All accounts</span>
          {unread > 0 && <span className="mb-acct-unread" aria-label={`${unread} unread`}>{unread}</span>}
        </button>
        <div className="mb-acct-foot">
          <span className="mb-acct-sub"><span>{accounts.length} {accounts.length === 1 ? 'mailbox' : 'mailboxes'}{live < accounts.length ? ` · ${accounts.length - live} offline` : ''}</span></span>
          <span className="mb-acct-auto-label" title={`Auto-import is on for ${autoOn} of ${live} connected mailboxes`}>Auto-import {autoOn}/{live}</span>
        </div>
      </li>
      {accounts.map((item) => {
        const connected = isLiveAccount(item)
        const attention = item.connectionStatus === 'error' || !!item.lastError
        return <li key={item.id} className={cn('mb-acct', selectedId === item.id && 'is-active', !connected && 'is-off')}>
          <button type="button" className="mb-acct-main" aria-pressed={selectedId === item.id} onClick={() => onSelect(item.id)}>
            <ProviderMark provider={item.provider} off={!connected} />
            <span className="mb-acct-title" title={item.address}>{item.address}</span>
            {(item.unreadCount ?? 0) > 0 && <span className="mb-acct-unread" aria-label={`${item.unreadCount} unread`}>{item.unreadCount}</span>}
          </button>
          <div className="mb-acct-foot">
            {!connected
              ? <>
                <span className="mb-acct-sub is-warn" title={item.connectionStatus === 'disconnected' ? 'Needs reconnecting' : 'Old mailbox, read only'}><AlertTriangle size={13} /><span>{item.connectionStatus === 'disconnected' ? 'Needs reconnecting' : 'Old mailbox, read only'}</span></span>
                <span className="mb-acct-auto-label" title="Saved mail only">Saved mail only</span>
              </>
              : <>
                <span className={cn('mb-acct-sub', attention && 'is-warn')} title={item.lastError ?? (item.lastSyncAt ? `Last checked ${smartTime(item.lastSyncAt)}` : undefined)}>{attention && <AlertTriangle size={13} />}<span>{PROVIDER_LABEL[item.provider]} · {attention ? 'needs attention' : item.lastSyncAt ? smartTime(item.lastSyncAt) : 'not checked yet'}</span></span>
                <span className="mb-acct-auto">
                  <span className={cn('mb-acct-auto-label', item.autoIngest && 'is-on')}>Auto-import</span>
                  {admin
                    ? <Switch compact checked={item.autoIngest} disabled={busy} label={`Auto-import for ${item.address}`} onChange={(enabled) => onToggleAuto(item, enabled)} />
                    : <span className="mb-acct-state">{item.autoIngest ? 'On' : 'Off'}</span>}
                </span>
              </>}
          </div>
        </li>
      })}
    </ul>
    <div className="mb-rail-ctl">
      {(edge.left || edge.right) && <>
        <button type="button" className="mb-icon-btn is-boxed" aria-label="Scroll mailboxes left" title="Scroll left" disabled={!edge.left} onClick={() => nudge(-1)}><ChevronLeft size={16} /></button>
        <button type="button" className="mb-icon-btn is-boxed" aria-label="Scroll mailboxes right" title="Scroll right" disabled={!edge.right} onClick={() => nudge(1)}><ChevronRight size={16} /></button>
      </>}
      {admin && onConnect && <button type="button" className="mb-icon-btn is-boxed" aria-label="Connect mailbox" title="Connect mailbox" onClick={onConnect}><Plus size={16} /></button>}
    </div>
  </nav>
}
