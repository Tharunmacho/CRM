import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, Download, Link2, Mail, MapPin, MessageCircle, Pencil, Phone, PhoneCall, UserPlus } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, dateTimeLabel, initials, money, smartTime } from '@/shared/lib/format'
import { DOCUMENT_HELP, newestFileVersions } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { api } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import { closureLabels, stageByNo } from '@/shared/workflow/stages'
import { Badge, EmptyState, Panel, StageChip, StatusPill } from '@/shared/ui/display'
import { Button, Field, RadioRow, Textarea } from '@/shared/ui/primitives'
import { DocumentSlotRow, FileLink } from '@/shared/ui/file-drop'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { MapToOrderDialog } from '@/features/candidates/components/map-dialog'
import { CandidateSections, type CandidateOcr } from '@/features/candidates/components/profile-sections'
import { ProfileCard } from '@/features/candidates/components/fact-section'
import { CandidateEditDialog } from '@/features/candidates/components/candidate-edit-dialog'
import { CandidateAssignmentHistory } from '@/features/candidates/components/assignment-history'
import { buildProfileModel, type ContactChip, type ProfileFormat } from '@/features/candidates/profile-view'
import { EmailEvidenceHistory } from '@/features/inbox/email-evidence-history'
import { CandidateIntakeEvidence } from '@/features/candidates/components/candidate-intake-evidence'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { linkedConversationId, mapToOrderBlocker } from '@/features/candidates/profile-actions'
import { conversationHref } from '@/features/whatsapp/chat-types'

const FORMAT: ProfileFormat = { money: (amount) => money(amount), date: (value) => dateLabel(value) }

/* ------------------------------------------------------------------- Tab bar */

interface ProfileTab {
  id: string
  label: string
}

/**
 * The tab bar follows the sections that actually rendered: each `[data-profile-tab]` group with
 * content becomes a tab, so a section with no data never gets one. The active tab follows scroll.
 */
function useProfileTabs(root: RefObject<HTMLElement>, scroller: RefObject<HTMLElement>) {
  const [tabs, setTabs] = useState<ProfileTab[]>([])
  const [active, setActive] = useState('')
  const lockUntil = useRef(0)

  // Runs after every render of the page; the state only changes when the set of sections does.
  useLayoutEffect(() => {
    const container = root.current
    if (!container) return
    const found = [...container.querySelectorAll<HTMLElement>('[data-profile-tab]')]
      .filter((el) => el.childElementCount > 0)
      .map((el) => ({ id: el.id, label: el.dataset.profileTab ?? '' }))
    setTabs((previous) => (JSON.stringify(previous) === JSON.stringify(found) ? previous : found))
  })

  const tabKey = tabs.map((tab) => tab.id).join('|')
  useEffect(() => {
    if (!tabKey) { setActive(''); return }
    const ids = tabKey.split('|')
    let frame = 0
    const update = () => {
      frame = 0
      if (Date.now() < lockUntil.current) return
      let current = ids[0]
      for (const id of ids) {
        const el = document.getElementById(id)
        if (!el) continue
        if (el.getBoundingClientRect().top <= 190) current = id
        else break
      }
      if (window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4) current = ids[ids.length - 1]
      setActive(current)
    }
    const onScroll = () => { if (!frame) frame = window.requestAnimationFrame(update) }
    window.addEventListener('scroll', onScroll, { passive: true })
    window.addEventListener('resize', onScroll)
    update()
    return () => {
      window.removeEventListener('scroll', onScroll)
      window.removeEventListener('resize', onScroll)
      if (frame) window.cancelAnimationFrame(frame)
    }
  }, [tabKey])

  // Keep the active tab visible when the bar scrolls sideways on a narrow screen.
  useEffect(() => {
    const bar = scroller.current
    if (!bar || !active || bar.scrollWidth <= bar.clientWidth) return
    const button = bar.querySelector<HTMLElement>(`[data-tab-id="${active}"]`)
    if (!button) return
    bar.scrollTo({ left: button.offsetLeft - (bar.clientWidth - button.offsetWidth) / 2, behavior: 'smooth' })
  }, [active, scroller])

  const go = useCallback((id: string) => {
    const el = document.getElementById(id)
    if (!el) return
    setActive(id)
    lockUntil.current = Date.now() + 1000
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    el.scrollIntoView({ behavior: reduce ? 'auto' : 'smooth', block: 'start' })
  }, [])

  return { tabs, active, go }
}

/* ------------------------------------------------------------------ Identity */

const chipClass = 'inline-flex max-w-full items-center gap-2 rounded-full bg-field px-4 py-2 text-[15px] leading-6 text-ink-700 transition-colors'
const phoneDigits = (value: string) => value.replace(/[^\d+]/g, '')

function ContactChips({ contacts }: { contacts: ContactChip[] }) {
  if (contacts.length === 0) return null
  return (
    <ul className="mt-4 flex flex-wrap gap-2.5">
      {contacts.map((contact) => {
        const icon = contact.kind === 'email' ? <Mail className="h-4 w-4 shrink-0 text-ink-500" /> : contact.kind === 'location' ? <MapPin className="h-4 w-4 shrink-0 text-ink-500" /> : contact.kind === 'whatsapp' ? <MessageCircle className="h-4 w-4 shrink-0 text-ink-500" /> : <Phone className="h-4 w-4 shrink-0 text-ink-500" />
        const body = <>{icon}<span className="min-w-0 break-all">{contact.value}</span></>
        const href = contact.kind === 'email' ? `mailto:${contact.value}` : contact.kind === 'phone' ? `tel:${phoneDigits(contact.value)}` : contact.kind === 'whatsapp' ? `https://wa.me/${contact.value.replace(/\D/g, '')}` : ''
        return (
          <li key={`${contact.kind}-${contact.value}`} className="min-w-0 max-w-full">
            {href ? (
              <a href={href} target={contact.kind === 'whatsapp' ? '_blank' : undefined} rel="noreferrer" className={cn(chipClass, 'hover:bg-rule-soft')}>{body}</a>
            ) : (
              <span className={chipClass}>{body}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

const pillClass = 'inline-flex items-center gap-2 whitespace-nowrap rounded-full border border-rule bg-white px-3.5 py-1.5 text-[14px] font-medium leading-5 text-ink-700'

/* ---------------------------------------------------------------------- Page */

export default function CandidateProfilePage() {
  const { id } = useParams()
  const store = useStore()
  const navigate = useNavigate()
  const cand = store.candidates.find((c) => c.id === id)

  const [openApp, setOpenApp] = useState<string | null>(null)
  const [calling, setCalling] = useState(false)
  const [mapping, setMapping] = useState(false)
  const [editing, setEditing] = useState(false)
  const [reassigning, setReassigning] = useState(false)
  const [whatsappConversation, setWhatsappConversation] = useState<{ candidateId: string; conversationId: string } | null>(null)
  const refresh = useRef({ ensureFresh: store.ensureFresh, refresh: store.refresh })
  refresh.current = { ensureFresh: store.ensureFresh, refresh: store.refresh }
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [loadError, setLoadError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let live = true
    setLoadedId(null); setLoadError('')
    setOpenApp(null); setCalling(false); setMapping(false); setEditing(false); setReassigning(false)
    const request = retry ? refresh.current.refresh({ keepCurrent: true }) : refresh.current.ensureFresh()
    request.then(() => {
      if (live) setLoadedId(id ?? null)
    }).catch(error => {
      if (live) setLoadError(error instanceof Error ? error.message : 'The candidate could not be refreshed.')
    })
    return () => { live = false }
  }, [id, retry])

  // The stored OCR output is not in the bootstrap, so the profile fetches it for itself.
  const [ocr, setOcr] = useState<CandidateOcr>({})
  const [evidenceError, setEvidenceError] = useState('')
  const [evidenceLoading, setEvidenceLoading] = useState(false)
  const [evidenceRetry, setEvidenceRetry] = useState(0)
  const [fullText, setFullText] = useState<{ id: string; text: string } | null>(null)
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setEvidenceLoading(true); setEvidenceError('')
    Promise.all([
      api.get<Candidate>(`/candidates/${id}`, { signal: controller.signal }),
      api.get<CandidateOcr>(`/candidates/${id}/ocr`, { signal: controller.signal }),
    ]).then(([profile, extracted]) => {
      if (!controller.signal.aborted) { setFullText({ id, text: profile.resumeText }); setOcr(extracted ?? {}) }
    }).catch(cause => {
      if (!controller.signal.aborted) setEvidenceError(cause instanceof Error ? cause.message : 'Document evidence could not be loaded.')
    }).finally(() => { if (!controller.signal.aborted) setEvidenceLoading(false) })
    return () => controller.abort()
  }, [id, cand, evidenceRetry])
  useEffect(() => { setOcr({}); setFullText(null) }, [id])
  // The WhatsApp button exists only when a conversation is linked; one cheap read, never part of bootstrap.
  useEffect(() => {
    if (!id) return
    const controller = new AbortController()
    setWhatsappConversation(null)
    api.get<{ conversation: { id: string } | null }>(`/whatsapp/candidates/${id}/conversation`, { signal: controller.signal })
      .then(result => { const conversationId = linkedConversationId(result); if (!controller.signal.aborted && conversationId) setWhatsappConversation({ candidateId: id, conversationId }) })
      .catch(() => { /* No button is better than a misleading one. */ })
    return () => controller.abort()
  }, [id, cand?.whatsapp, cand?.ownerId])

  const model = useMemo(() => (cand ? buildProfileModel(cand, ocr, FORMAT) : null), [cand, ocr])
  const bodyRef = useRef<HTMLDivElement>(null)
  const barRef = useRef<HTMLUListElement>(null)
  const { tabs, active: activeTab, go } = useProfileTabs(bodyRef, barRef)

  if (loadedId !== id) {
    return <Panel><p role={loadError ? 'alert' : 'status'} className="text-sm text-ink-500">{loadError || 'Loading the latest candidate profile…'}</p>
      {loadError && <Button className="mt-3" onClick={() => setRetry(value => value + 1)}>Retry profile</Button>}</Panel>
  }
  if (!cand || !model) {
    return (
      <EmptyState
        title="That candidate does not exist"
        description="They may have been merged into another record."
        action={<Button onClick={() => navigate('/data-bank')}>Back to Data Bank</Button>}
      />
    )
  }

  const owner = store.user(cand.ownerId)
  const apps = store.applicationsFor(cand.id)
  const active = apps.find((a) => a.status === 'active')
  const deployed = apps.some((a) => a.status === 'deployed') || cand.status === 'deployed'
  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id
  const canAssign = store.can('work_any_candidate')
  const ownerActive = !!cand.ownerId && !!store.user(cand.ownerId)?.active
  const initialTask = store.tasks.find(task => task.candidateId === cand.id && task.kind === 'initial_call' && !task.completedAt && !task.cancelledAt)
  const hasOriginals = cand.documents.some(document => document.key === 'source_bundle' && document.versions.length > 0)
  const resumeFile = newestFileVersions(cand.documents.find(document => document.key === 'resume')?.versions ?? [])[0]

  // One filled button for the next thing that moves this candidate forward; the rest stay outlined.
  const primary: 'assign' | 'stage' | 'map' | null = !cand.ownerId && canAssign ? 'assign' : active ? 'stage' : !deployed ? 'map' : null
  const mapBlocker = mapToOrderBlocker(cand, ownerActive)
  const mapButton = !active && !deployed && (
    <span title={mapBlocker ?? undefined} className="inline-flex">
      <Button variant={primary === 'map' ? 'primary' : 'secondary'} className={primary === 'map' ? primaryClass : undefined} disabled={!ownerActive} aria-describedby={mapBlocker && apps.length === 0 ? 'map-blocker' : undefined} icon={<Link2 className="h-4 w-4" />} onClick={() => setMapping(true)}>
        Map to an order
      </Button>
    </span>
  )
  const conversationId = whatsappConversation?.candidateId === cand.id ? whatsappConversation.conversationId : ''

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <Button icon={<ArrowLeft className="h-4 w-4" />} onClick={() => navigate('/data-bank')}>Back to Data Bank</Button>
        <div className="flex flex-wrap items-center gap-2">
          {canWork && <Button icon={<Pencil size={16} />} onClick={() => setEditing(true)}>Correct profile</Button>}
          {conversationId && <Button icon={<MessageCircle className="h-4 w-4" />} onClick={() => navigate(conversationHref(conversationId))}>WhatsApp conversation</Button>}
          {resumeFile && (
            <FileLink
              url={resumeFile.url}
              download
              title={`Download ${resumeFile.name}`}
              className="ui-button ui-button-secondary inline-flex h-10 items-center justify-center gap-2 rounded-control border border-rule bg-white px-4 text-sm font-medium text-ink hover:bg-field hover:text-ink hover:no-underline"
            >
              <Download className="h-4 w-4" />
              Download resume
            </FileLink>
          )}
          {primary !== 'map' && mapButton}
          {primary === 'assign' && <Button variant="primary" className={primaryClass} icon={<UserPlus size={16} />} onClick={() => setReassigning(true)}>Assign recruiter</Button>}
          {primary === 'stage' && active && <Button variant="primary" className={primaryClass} onClick={() => setOpenApp(active.id)}>Open stage {active.currentStage}</Button>}
          {primary === 'map' && mapButton}
        </div>
      </div>

      <section className="rounded-2xl border border-rule-soft bg-white p-5 shadow-soft sm:p-7">
        <div className="flex flex-col gap-5 sm:flex-row sm:items-start sm:gap-7">
          <div aria-hidden className="grid h-20 w-20 shrink-0 place-items-center rounded-2xl bg-field text-[28px] font-semibold text-ink-600 sm:h-24 sm:w-24 sm:text-[34px]">
            {initials(cand.name)}
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
              <h1 className="min-w-0 break-words text-[26px] font-semibold leading-9 text-ink sm:text-[30px] sm:leading-10">{cand.name}</h1>
              <div className="flex flex-wrap items-center gap-2">
                <span className={pillClass}>Candidate ID · <span className="tnum">{cand.ref}</span></span>
                <StatusPill status={cand.status} dot className="rounded-full px-3.5 py-1.5 text-[14px]" />
                {cand.needsReview && (
                  <Badge tone="amber" dot className="rounded-full px-3.5 py-1.5 text-[14px]">
                    <span title="Check the highlighted fields on the call">Needs review</span>
                  </Badge>
                )}
              </div>
            </div>
            {model.designation && <p className="mt-2 break-words text-[17px] font-medium leading-7 text-ink-700">{model.designation}</p>}
            {model.meta.length > 0 && <p className="mt-1 break-words text-[15px] leading-6 text-ink-500">{model.meta.join(' · ')}</p>}
            <ContactChips contacts={model.contacts} />
          </div>
        </div>
      </section>

      <nav aria-label="Profile sections" className="sticky top-[85px] z-10 rounded-2xl border border-rule-soft bg-white shadow-soft">
        <ul ref={barRef} className="scrollbar-thin relative flex gap-1 overflow-x-auto px-3">
          {tabs.map((tab) => (
            <li key={tab.id} className="shrink-0">
              <button
                type="button"
                data-tab-id={tab.id}
                aria-current={activeTab === tab.id ? 'true' : undefined}
                onClick={() => go(tab.id)}
                className={cn(
                  'relative flex items-center gap-2 whitespace-nowrap px-4 py-4 text-[15px] font-medium leading-6 transition-colors',
                  activeTab === tab.id ? 'text-ink' : 'text-ink-500 hover:text-ink-700',
                  activeTab === tab.id && 'after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full after:bg-ink-800',
                )}
              >
                {tab.label}
              </button>
            </li>
          ))}
        </ul>
      </nav>

      <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,1fr)_360px] xl:grid-cols-[minmax(0,1fr)_400px]">
        <div ref={bodyRef} className="flex min-w-0 flex-col gap-6">
          {evidenceLoading && <p role="status" className="text-[15px] text-ink-500">Loading document evidence…</p>}
          {evidenceError && (
            <Panel>
              <p role="alert" className="text-[15px] text-stamp-700">Document evidence could not refresh. {fullText?.id === id ? 'Previously loaded evidence is shown below.' : 'The saved profile is available; extracted document details are not loaded.'} {evidenceError}</p>
              <Button className="mt-3" onClick={() => setEvidenceRetry(value => value + 1)}>Retry document evidence</Button>
            </Panel>
          )}
          <CandidateSections cand={{ ...cand, resumeText: fullText?.id === id ? fullText.text : '' }} model={model} ocr={ocr} canViewFullIdNumbers={canAssign} />
        </div>

        <aside aria-label="Recruitment workflow" className="flex min-w-0 flex-col gap-6 lg:sticky lg:top-[152px] lg:max-h-[calc(100dvh-10rem)] lg:overflow-y-auto lg:pb-1 lg:pr-1">
          <ProfileCard title="Next step" className="sm:p-6">
            <h3 className="mt-4 text-[17px] font-semibold leading-7 text-ink">{!cand.ownerId ? 'Assign a recruiter' : active ? `Continue stage ${active.currentStage}: ${stageByNo(active.currentStage).title}` : deployed ? 'Recruitment completed' : initialTask ? 'Complete the initial call' : 'Match to a suitable order'}</h3>
            <p className="mt-1 text-[14px] leading-6 text-ink-600">{!cand.ownerId ? 'Assigning a recruiter starts the initial-call task.' : active ? 'Stage progress and deadlines are kept.' : deployed ? 'Documents and history stay on this page.' : initialTask ? `${owner?.name || 'The assigned recruiter'} owns the call${initialTask.dueAt ? ` · due ${dateTimeLabel(initialTask.dueAt)}` : ''}.` : 'Choose an open order to start the recruitment stages.'}</p>
            {((!cand.ownerId && canAssign) || (initialTask && canWork)) && (
              <div className="mt-4 flex flex-wrap gap-2">
                {!cand.ownerId && canAssign && <Button variant="primary" className={primaryClass} onClick={() => setReassigning(true)}>Assign recruiter</Button>}
                {initialTask && canWork && <Button onClick={() => navigate(`/tasks?task=${encodeURIComponent(initialTask.id)}`)}>Open initial-call task</Button>}
              </div>
            )}
          </ProfileCard>

          <ProfileCard title="Recruitment pipeline" className="sm:p-6">
            {apps.length === 0 ? (
              <div className="pt-4">
                <p className="text-[15px] font-medium text-ink-700">Not mapped to any order</p>
                {!deployed && mapBlocker && <p id="map-blocker" role="note" className="mt-2 text-[14px] leading-5 text-amber-700">{mapBlocker}</p>}
                {!deployed && ownerActive && <Button variant="primary" className={cn('mt-4', primaryClass)} icon={<Link2 className="h-4 w-4" />} onClick={() => setMapping(true)}>Map to an order</Button>}
              </div>
            ) : (
              <ul className="space-y-2.5 pt-4">
                {apps.map((a) => {
                  const order = store.order(a.orderId)
                  return (
                    <li key={a.id}>
                      <button
                        type="button"
                        onClick={() => setOpenApp(a.id)}
                        className="flex w-full items-center gap-3 rounded-xl border border-rule-soft px-4 py-3 text-left transition-colors hover:bg-field/70"
                      >
                        <StageChip
                          no={a.currentStage}
                          state={a.status === 'closed' ? 'closed' : a.status === 'deployed' ? 'done' : 'current'}
                        />
                        <div className="min-w-0 flex-1">
                          <p className="truncate text-[15px] font-semibold text-ink">{store.orderLabel(a.orderId)}</p>
                          <p className="truncate text-[13px] text-ink-500">
                            {[order?.ref, a.status === 'active' ? stageByNo(a.currentStage).title : '', a.closureReason ? closureLabels[a.closureReason] : ''].filter(Boolean).join(' · ')}
                          </p>
                          <p className="text-[13px] text-ink-500">{dateTimeLabel(a.updatedAt)}</p>
                        </div>
                        <StatusPill status={a.status} className="rounded-full" />
                      </button>
                    </li>
                  )
                })}
              </ul>
            )}
          </ProfileCard>

          <ProfileCard
            title="Initial call"
            className="sm:p-6"
            action={
              canWork && !active && !deployed && (
                <Button size="sm" variant="secondary" icon={<PhoneCall className="h-3.5 w-3.5" />} onClick={() => setCalling(true)}>
                  {cand.callOutcome ? 'Update' : 'Record'}
                </Button>
              )
            }
          >
            <div className="pt-4">
              {cand.callOutcome ? (
                <>
                  <div className="flex flex-wrap items-center gap-2">
                    <Badge tone={cand.callOutcome.interested === 'yes' ? 'navy' : cand.callOutcome.interested === 'no' ? 'amber' : 'neutral'} className="rounded-full px-3 py-1 text-[13px]">
                      {cand.callOutcome.interested === 'yes'
                        ? 'Interested'
                        : cand.callOutcome.interested === 'no'
                          ? 'Not interested'
                          : 'Not reachable'}
                    </Badge>
                    <span className="text-[13px] text-ink-500">{smartTime(cand.callOutcome.recordedAt)}</span>
                  </div>
                  {cand.callOutcome.remarks && <p className="mt-3 whitespace-pre-line text-[15px] leading-6 text-ink-700">{cand.callOutcome.remarks}</p>}
                </>
              ) : (
                <p className="text-[15px] leading-6 text-ink-600">
                  No call recorded yet.
                </p>
              )}
            </div>
          </ProfileCard>

          <ProfileCard
            title="Owner"
            className="sm:p-6"
            action={canAssign && <Button size="sm" onClick={() => setReassigning(true)}>{cand.ownerId ? 'Reassign' : 'Assign'}</Button>}
          >
            <div className="flex flex-wrap items-center gap-3 pt-4">
              <div className="min-w-0">
                <p className="truncate text-[16px] font-semibold text-ink">{owner?.name || 'Needs assignment'}</p>
                {owner?.role && <p className="truncate text-[14px] capitalize text-ink-500">{owner.role}</p>}
              </div>
              <CandidateAssignmentHistory candidateId={cand.id} />
            </div>
          </ProfileCard>

          <ProfileCard title="Documents" meta={canWork ? DOCUMENT_HELP : undefined} className="sm:p-6">
            <div>
              {cand.documents.map((d) => (
                <DocumentSlotRow
                  key={d.key}
                  label={d.label}
                  versions={d.versions}
                  emptyLabel={hasOriginals && ['resume', 'passport', 'aadhaar'].includes(d.key) ? 'In source evidence' : undefined}
                  uploaderName={(userId) => store.user(userId)?.name || 'Former team member'}
                  onUpload={
                    canWork && d.key !== 'source_bundle'
                      ? async (file) => {
                          await api.upload('candidate', cand.id, d.key, file)
                          try {
                            await store.refresh()
                            store.notify(`${d.label} uploaded`)
                          } catch (err) {
                            store.notify(`${d.label} was uploaded. Reload the page to see the saved version.`, 'warn')
                          }
                        }
                      : undefined
                  }
                />
              ))}
              <EmailEvidenceHistory candidateId={cand.id} />
              <CandidateIntakeEvidence candidateId={cand.id} />
            </div>
          </ProfileCard>
        </aside>
      </div>

      <CallDialog open={calling} onClose={() => setCalling(false)} candidateId={cand.id} />
      {editing && <CandidateEditDialog candidate={cand} onClose={() => setEditing(false)} />}
      {mapping && <MapToOrderDialog open onClose={() => setMapping(false)} candidateId={cand.id} />}
      {reassigning && <ReassignCandidatesDialog candidateIds={[cand.id]} onClose={() => setReassigning(false)} description={cand.ownerId ? 'Change the recruiter responsible for this candidate and their open tasks.' : 'Choose an available recruiter. Assigning this saved candidate starts their initial-call task.'} />}
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </div>
  )
}

/** Dark navy fill for the single main action; `enabled:` keeps the muted disabled look. */
const primaryClass = 'enabled:!bg-ink-800 enabled:hover:!bg-ink-900 enabled:!shadow-none'

function CallDialog({
  open,
  onClose,
  candidateId,
}: {
  open: boolean
  onClose: () => void
  candidateId: string
}) {
  const { recordCall } = useStore()
  const [interested, setInterested] = useState('')
  const [remarks, setRemarks] = useState('')
  const [called, setCalled] = useState('yes')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')

  return (
    <Dialog
      open={open}
      onClose={() => { if (!saving) onClose() }}
      title="Record the call outcome"
      description="A confirmed interested or not-interested outcome completes the initial-call task. An unanswered or unmade call remains open."
      footer={
        <>
          <Button variant="ghost" disabled={saving} onClick={onClose}>
            Cancel
          </Button>
          <Button
            variant="primary"
            disabled={saving || !interested}
            onClick={async () => {
              if (saving) return
              setSaving(true); setError('')
              try {
                await recordCall(candidateId, interested, remarks, called === 'yes')
                setInterested(''); setRemarks(''); setCalled('yes'); onClose()
              } catch (error) { setError(error instanceof Error ? error.message : 'The call outcome was not saved.') } finally { setSaving(false) }
            }}
          >
            {saving ? 'Saving…' : 'Save outcome'}
          </Button>
        </>
      }
    >
      <fieldset disabled={saving} className="min-w-0 space-y-4">
        <Field label="Called" required><RadioRow name="called" value={called} onChange={value => { setCalled(value); if (value === 'no') setInterested('unreachable') }} options={[{ value: 'yes', label: 'Yes' }, { value: 'no', label: 'No' }]} /></Field>
        <Field label="Interested" required>
          <RadioRow
            name="interested"
            value={interested}
            onChange={setInterested}
            options={called === 'no' ? [{ value: 'unreachable', label: 'Not contacted yet' }] : [
              { value: 'yes', label: 'Yes' },
              { value: 'no', label: 'No' },
              { value: 'unreachable', label: 'Not reachable' },
            ]}
          />
        </Field>
        {error && <p role="alert" className="text-sm text-stamp-600">{error}</p>}
        <Field label="Remarks">
          <Textarea
            maxLength={10000}
            value={remarks}
            onChange={(e) => setRemarks(e.target.value)}
            placeholder="What the candidate said"
          />
        </Field>
      </fieldset>
    </Dialog>
  )
}
