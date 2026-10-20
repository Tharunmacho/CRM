import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ChevronDown,
  CornerUpLeft,
  FileText,
  Image as ImageIcon,
  Mail,
  MailOpen,
  Paperclip,
  Plus,
  Settings2,
  Sparkles,
  UserPlus,
} from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { initials, smartTime } from '@/shared/lib/format'
import { useStore } from '@/shared/state/crm-store'
import { Badge, EmptyState, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { SummaryCards } from '@/shared/ui/workspace'
import { Button, CheckboxInput, SearchInput, Switch, Textarea } from '@/shared/ui/primitives'
import { Dialog, Dropdown, Tabs } from '@/shared/ui/overlay'

const PROVIDER_LABEL = { gmail: 'Gmail', microsoft: 'Microsoft 365', imap: 'IMAP' }

export default function InboxPage() {
  const store = useStore()
  const navigate = useNavigate()
  const [selected, setSelected] = useState<string[]>(() => store.mailAccounts.map((a) => a.id))
  const [query, setQuery] = useState('')
  const [openId, setOpenId] = useState<string | null>(store.mailMessages[0]?.id ?? null)
  const [reply, setReply] = useState('')
  const [managing, setManaging] = useState(false)
  const [mailFilter, setMailFilter] = useState('all')

  const messages = useMemo(() => {
    const q = query.trim().toLowerCase()
    return store.mailMessages
      .filter((m) => selected.includes(m.accountId))
      .filter((message) => mailFilter !== 'unread' || message.unread)
      .filter(
        (m) =>
          !q ||
          m.subject.toLowerCase().includes(q) ||
          m.fromName.toLowerCase().includes(q) ||
          m.body.toLowerCase().includes(q),
      )
      .sort((a, b) => +new Date(b.receivedAt) - +new Date(a.receivedAt))
  }, [store.mailMessages, selected, query, mailFilter])

  const open = messages.find((message) => message.id === openId) ?? messages[0]

  return (
    <>
      <PageHeader
        title="Inbox"
        eyebrow="Connected conversations"
        description="One inbox for every opportunity. Keep candidate conversations and incoming resumes together."
        actions={
          <>
            <AccountPicker
              selected={selected}
              onChange={setSelected}
              onManage={() => setManaging(true)}
            />
            {store.can('connect_sources') && (
              <Button variant="accent" icon={<Plus className="h-4 w-4" />}>
                Connect an account
              </Button>
            )}
          </>
        }
      />

      <SummaryCards
        items={[
          {
            label: 'Total messages',
            value: store.mailMessages.length,
            detail: 'Across connected accounts',
            icon: Mail,
          },
          {
            label: 'Unread',
            value: store.mailMessages.filter((message) => message.unread).length,
            detail: 'Waiting for a closer look',
            icon: MailOpen,
            tone: 'violet',
          },
          {
            label: 'Connected accounts',
            value: store.mailAccounts.length,
            detail: 'Your recruitment mailboxes',
            icon: Settings2,
            tone: 'green',
          },
          {
            label: 'Auto-ingest enabled',
            value: store.mailAccounts.filter((account) => account.autoIngest).length,
            detail: 'Accounts feeding the talent pipeline',
            icon: Sparkles,
            tone: 'amber',
          },
        ]}
      />

      <div className="workspace-split inbox-layout">
        {/* Message list */}
        <Panel flush className="inbox-message-list h-fit min-w-0">
          <PanelHeader title="Messages" meta={`${messages.length} in this view`} />
          <Tabs tabs={[{ key: 'all', label: 'All messages' }, { key: 'unread', label: 'Unread' }]} active={mailFilter} onChange={setMailFilter} />
          <div className="border-b border-rule-soft p-3">
            <SearchInput value={query} onChange={setQuery} placeholder="Search mail" />
          </div>
          <ul className="scrollbar-thin max-h-[36rem] overflow-y-auto">
            {messages.map((m) => (
              <li key={m.id}>
                <button
                  onClick={() => setOpenId(m.id)}
                  aria-pressed={open?.id === m.id}
                  className={cn(
                    'mail-list-item',
                    'w-full border-b border-rule-soft px-3 py-2.5 text-left transition-colors last:border-0',
                    open?.id === m.id ? 'bg-navy-50' : 'hover:bg-field/70',
                  )}
                >
                  <span className="mail-sender-avatar" aria-hidden="true">{initials(m.fromName)}</span>
                  <span className="mail-list-copy">
                    <span className="flex items-baseline gap-2">
                      {m.unread && <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-navy-600" />}
                      <span
                        className={cn(
                          'min-w-0 flex-1 truncate text-sm',
                          m.unread ? 'font-semibold text-ink' : 'text-ink-700',
                        )}
                      >
                        {m.fromName}
                      </span>
                      <span className="shrink-0 text-micro text-ink-400">{smartTime(m.receivedAt)}</span>
                    </span>
                    <span className="mt-0.5 block truncate text-xs text-ink-600">{m.subject}</span>
                    <span className="mt-0.5 block truncate text-micro text-ink-400">{m.preview}</span>
                    <span className="mt-1 flex items-center gap-1.5">
                      {m.attachments.length > 0 && (
                        <span className="inline-flex items-center gap-0.5 text-micro text-ink-400">
                          <Paperclip className="h-3 w-3" />
                          {m.attachments.length}
                        </span>
                      )}
                      {m.candidateId && <Badge tone="navy">Candidate created</Badge>}
                    </span>
                  </span>
                </button>
              </li>
            ))}
            {messages.length === 0 && (
              <EmptyState
                title={selected.length === 0 ? 'No accounts selected' : 'No mail matches'}
                description={
                  selected.length === 0
                    ? 'Tick an account in the picker above to see its mail.'
                    : 'Try another search, or tick more accounts.'
                }
              />
            )}
          </ul>
        </Panel>

        {/* Message detail */}
        <Panel flush className="inbox-reader min-w-0">
          {open ? (
            <>
              <div className="inbox-reader-header border-b border-rule-soft px-4 py-3.5">
                <span className="workspace-eyebrow">Conversation</span>
                <h2 className="text-h3 font-semibold text-ink">{open.subject}</h2>
                <div className="mail-sender-detail mt-2 flex flex-wrap items-center gap-2.5">
                  <span className="mail-sender-avatar" aria-hidden="true">{initials(open.fromName)}</span>
                  <div className="min-w-0">
                    <p className="truncate text-sm text-ink">{open.fromName}</p>
                    <p className="truncate text-micro text-ink-400">{open.fromAddress}</p>
                  </div>
                  <span className="ml-auto text-micro text-ink-400">
                    {smartTime(open.receivedAt)} · to{' '}
                    {store.mailAccounts.find((a) => a.id === open.accountId)?.address}
                  </span>
                </div>
                {open.candidateId && (
                  <button
                    onClick={() => navigate(`/candidates/${open.candidateId}`)}
                    className="mt-2.5 inline-flex items-center gap-1.5 rounded-control border border-navy-100 bg-navy-50 px-2 py-1 text-xs font-medium text-navy-700 hover:bg-navy-100"
                  >
                    This mail produced a candidate — open the record
                  </button>
                )}
              </div>

              <div className="inbox-reader-body px-4 py-4">
                <p className="max-w-[70ch] text-sm leading-relaxed text-ink-700">{open.body}</p>

                {open.attachments.length > 0 && (
                  <div className="mt-4">
                    <p className="mb-2 text-xs font-medium text-ink-600">
                      {open.attachments.length} attachment{open.attachments.length > 1 ? 's' : ''}
                    </p>
                    <ul className="space-y-1.5">
                      {open.attachments.map((a) => {
                        const Icon = a.kind === 'image' ? ImageIcon : FileText
                        return (
                          <li
                            key={a.name}
                            className="mail-attachment flex items-center gap-2.5 rounded-control border border-rule px-3 py-2"
                          >
                            <Icon className="h-4 w-4 shrink-0 text-ink-400" />
                            <span className="min-w-0 flex-1 truncate text-sm text-ink">{a.name}</span>
                            <span className="shrink-0 text-micro text-ink-400 tnum">{a.sizeKb} KB</span>
                            {!open.candidateId && (
                              <Button
                                size="sm"
                                variant="secondary"
                                icon={<UserPlus className="h-3.5 w-3.5" />}
                                onClick={() =>
                                  store.notify('Resume ingestion is not connected yet', 'warn')
                                }
                              >
                                Create candidate
                              </Button>
                            )}
                          </li>
                        )
                      })}
                    </ul>
                  </div>
                )}
              </div>

              <div className="inbox-reply border-t border-rule-soft bg-field/40 px-4 py-3">
                <p className="mb-2 flex items-center gap-1.5 text-xs font-medium text-ink-600">
                  <CornerUpLeft className="h-3.5 w-3.5" />
                  Reply from {store.mailAccounts.find((a) => a.id === open.accountId)?.address}
                </p>
                <Textarea
                  rows={3}
                  value={reply}
                  onChange={(e) => setReply(e.target.value)}
                  placeholder="Write a reply"
                />
                <div className="mt-2 flex justify-end gap-2">
                  <Button variant="ghost" onClick={() => setReply('')}>
                    Discard
                  </Button>
                  <Button
                    variant="primary"
                    disabled={!reply.trim()}
                    onClick={() => {
                      store.notify('Sending mail is not connected yet', 'warn')
                      setReply('')
                    }}
                  >
                    Send reply
                  </Button>
                </div>
              </div>
            </>
          ) : (
            <EmptyState title="Pick a message" description="Choose one from the list to read it." />
          )}
        </Panel>
      </div>

      <ManageAccountsDialog open={managing} onClose={() => setManaging(false)} />
    </>
  )
}

/**
 * Account selector. Multi-select: tick any number of accounts to narrow the list, or
 * use the select-all box. Nothing ticked means every account.
 */
function AccountPicker({
  selected,
  onChange,
  onManage,
}: {
  selected: string[]
  onChange: (ids: string[]) => void
  onManage: () => void
}) {
  const store = useStore()
  const accounts = store.mailAccounts
  const total = store.mailMessages.length

  const countFor = (id: string) => store.mailMessages.filter((m) => m.accountId === id).length
  const allOn = selected.length === accounts.length
  const some = selected.length > 0 && selected.length < accounts.length

  const label = allOn
    ? 'All mail'
    : selected.length === 0
      ? 'No accounts selected'
      : selected.length === 1
        ? (accounts.find((a) => a.id === selected[0])?.address ?? 'All mail')
        : `${selected.length} accounts`

  const count = selected.reduce((n, id) => n + countFor(id), 0)

  const toggle = (id: string) =>
    onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id])

  return (
    <Dropdown
      width="w-[min(22rem,calc(100vw-2rem))]"
      trigger={({ open, toggle: openMenu }) => (
        <button
          onClick={openMenu}
          aria-haspopup="listbox"
          aria-expanded={open}
          className={cn(
            'inbox-account-trigger flex h-9 w-[min(22rem,calc(100vw-2rem))] items-center gap-2 rounded-control border bg-white px-2.5 text-sm transition-colors',
            open ? 'border-ink-300' : 'border-rule hover:border-ink-300',
          )}
        >
          <Mail className="h-4 w-4 shrink-0 text-ink-400" />
          <span className="min-w-0 flex-1 truncate text-left text-ink">{label}</span>
          <span className="shrink-0 text-micro text-ink-400 tnum">{count}</span>
          <ChevronDown
            className={cn('h-4 w-4 shrink-0 text-ink-400 transition-transform', open && 'rotate-180')}
          />
        </button>
      )}
    >
      {(close) => (
        <div role="listbox" aria-multiselectable>
          <label className="flex cursor-pointer items-center gap-2.5 border-b border-rule-soft bg-field/50 px-3 py-2.5 transition-colors hover:bg-field">
            <CheckboxInput
              checked={allOn}
              indeterminate={some}
              onChange={(on) => onChange(on ? accounts.map((a) => a.id) : [])}
            />
            <span className="flex-1 text-sm font-medium text-ink">All mail</span>
            <span className="text-micro text-ink-400 tnum">{total}</span>
          </label>

          {accounts.map((a) => (
            <label
              key={a.id}
              className="flex cursor-pointer items-start gap-2.5 border-b border-rule-soft px-3 py-2.5 transition-colors last:border-0 hover:bg-field"
            >
              <CheckboxInput
                className="mt-0.5"
                checked={selected.includes(a.id)}
                onChange={() => toggle(a.id)}
              />
              <span className="min-w-0 flex-1">
                <span className="flex items-center gap-2">
                  <span className="min-w-0 flex-1 truncate text-sm text-ink">{a.address}</span>
                  <span className="shrink-0 text-micro text-ink-400 tnum">{countFor(a.id)}</span>
                </span>
                <span className="mt-0.5 block text-micro text-ink-400">
                  {PROVIDER_LABEL[a.provider]} · synced {smartTime(a.lastSyncAt)}
                </span>
              </span>
            </label>
          ))}

          {store.can('connect_sources') && (
            <button
              onClick={() => {
                close()
                onManage()
              }}
              className="flex w-full items-center gap-2 border-t border-rule px-3 py-2.5 text-left text-sm text-ink-600 transition-colors hover:bg-field hover:text-ink"
            >
              <Settings2 className="h-4 w-4 shrink-0 text-ink-400" />
              Manage accounts and auto-ingest
            </button>
          )}
        </div>
      )}
    </Dropdown>
  )
}

/**
 * Auto-ingest lives here rather than in the picker: it is a per-account setting the
 * admin changes rarely, not a filter staff touch while reading mail.
 */
function ManageAccountsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const store = useStore()
  return (
    <Dialog
      open={open}
      onClose={onClose}
      width="md"
      title="Connected accounts"
      description="Auto-ingest sends every resume attachment straight to the OCR pipeline, with no manual step."
      footer={
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      }
    >
      <ul className="divide-y divide-rule-soft">
        {store.mailAccounts.map((a) => (
          <li key={a.id} className="flex items-start gap-4 py-3 first:pt-0 last:pb-0">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium text-ink">{a.address}</p>
              <p className="mt-0.5 text-micro text-ink-400">
                {PROVIDER_LABEL[a.provider]} · synced {smartTime(a.lastSyncAt)}
              </p>
              {a.failedCount > 0 && (
                <p className="mt-1 text-micro text-stamp-600">
                  {a.failedCount} files failed OCR on this account
                </p>
              )}
            </div>
            <div className="shrink-0">
              <Switch
                checked={a.autoIngest}
                onChange={() => store.notify('Mail accounts are not connected yet', 'warn')}
                label="Auto-ingest"
                description="Send attachments straight to OCR"
              />
            </div>
          </li>
        ))}
      </ul>
      <p className="mt-4 border-t border-rule-soft pt-3 text-micro leading-relaxed text-ink-400">
        With auto-ingest off, mail still arrives in the Inbox and you can raise a candidate by
        hand with Create candidate on any attachment.
      </p>
    </Dialog>
  )
}
