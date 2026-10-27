import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type RefObject } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { ArrowLeft, BookUser, Briefcase, ChevronDown, Download, FileText, Flag, GitBranch, History, Link2, Mail, MapPin, MessageCircle, Pencil, Phone, PhoneCall, Trash2, Unlink, UserCog, UserPlus, ListChecks } from 'lucide-react'
import { cn } from '@/shared/lib/cn'
import { dateLabel, dateTimeLabel, money, smartTime } from '@/shared/lib/format'
import { DOCUMENT_HELP, newestFileVersions } from '@/shared/lib/document-files'
import { useStore } from '@/shared/state/crm-store'
import { live } from '@/shared/state/live'
import { api } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import { closureLabels, STAGES, stageByNo } from '@/shared/workflow/stages'
import { Badge, EmptyState, Panel, StatusPill, type Tone } from '@/shared/ui/display'
import { RecordIdentity, SummaryCards } from '@/shared/ui/workspace'
import { Tabs } from '@/shared/ui/overlay'
import { Button } from '@/shared/ui/primitives'
import { FileLink } from '@/shared/ui/file-drop'
import { Dialog } from '@/shared/ui/overlay'
import { StageModal } from '@/features/workflow/components/stage-modal'
import { PersonAvatar } from '@/features/workflow/components/person-avatar'
import { StageCaption, StageRail } from '@/features/workflow/components/stage-rail'
import { CallDialog } from '@/features/candidates/components/call-dialog'
import { MapToOrderDialog } from '@/features/candidates/components/map-dialog'
import { CandidateSections, type CandidateOcr } from '@/features/candidates/components/profile-sections'
import { ProfileCard } from '@/features/candidates/components/fact-section'
import { CandidateEditDialog } from '@/features/candidates/components/candidate-edit-dialog'
import { CandidateAssignmentHistory } from '@/features/candidates/components/assignment-history'
import { buildProfileModel, SOURCE_LABEL, type ContactChip, type ProfileFormat } from '@/features/candidates/profile-view'
import { buildActivity, type ActivityTone } from '@/features/candidates/activity'
import { passportExpiryNotice } from '@/features/candidates/passport-expiry'
import { EmailEvidenceHistory } from '@/features/inbox/email-evidence-history'
import { CandidateIntakeEvidence } from '@/features/candidates/components/candidate-intake-evidence'
import { DocumentList } from '@/features/candidates/components/profile-documents'
import { ReassignCandidatesDialog } from '@/features/staff/reassign-candidates-dialog'
import { canDeleteCandidate, linkedConversationId, mapToOrderBlocker } from '@/features/candidates/profile-actions'
import { DeleteCandidateDialog } from '@/features/candidates/components/delete-candidate-dialog'
import { conversationHref } from '@/features/whatsapp/chat-types'
import '@/styles/candidate-v2.css'

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
      let best = -Infinity
      for (const id of ids) {
        const el = document.getElementById(id)
        if (!el) continue
        const top = el.getBoundingClientRect().top
        if (top <= 150 && top > best) { best = top; current = id }
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
    const bar = scroller.current?.querySelector<HTMLElement>('.ui-tabs')
    if (!bar || !active || bar.scrollWidth <= bar.clientWidth) return
    const button = bar.querySelector<HTMLElement>('[aria-selected="true"]')
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

const phoneDigits = (value: string) => value.replace(/[^\d+]/g, '')

function ContactChips({ contacts }: { contacts: ContactChip[] }) {
  if (contacts.length === 0) return null
  return (
    <ul className="cp-contacts">
      {contacts.map((contact) => {
        const icon = contact.kind === 'email' ? <Mail className="h-4 w-4 shrink-0" aria-hidden="true" /> : contact.kind === 'location' ? <MapPin className="h-4 w-4 shrink-0" aria-hidden="true" /> : contact.kind === 'whatsapp' ? <MessageCircle className="h-4 w-4 shrink-0" aria-hidden="true" /> : <Phone className="h-4 w-4 shrink-0" aria-hidden="true" />
        const body = <>{icon}<span>{contact.value}</span></>
        const href = contact.kind === 'email' ? `mailto:${contact.value}` : contact.kind === 'phone' ? `tel:${phoneDigits(contact.value)}` : contact.kind === 'whatsapp' ? `https://wa.me/${contact.value.replace(/\D/g, '')}` : ''
        return (
          <li key={`${contact.kind}-${contact.value}`} className="min-w-0 max-w-full">
            {href ? (
              <a href={href} target={contact.kind === 'whatsapp' ? '_blank' : undefined} rel="noreferrer" className="cp-contact">{body}</a>
            ) : (
              <span className="cp-contact">{body}</span>
            )}
          </li>
        )
      })}
    </ul>
  )
}

const APP_TONE = { active: 'navy', deployed: 'green', closed: 'stamp' } as const satisfies Record<string, Tone>
const APP_STATUS_LABEL = { active: 'Active', deployed: 'Deployed', closed: 'Closed' } as const
const ACTIVITY_TONE: Record<ActivityTone, string> = { ok: 'is-ok', warn: 'is-warn', bad: 'is-bad', info: 'is-info', plain: '' }

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
  const [deleting, setDeleting] = useState(false)
  const [unmapping, setUnmapping] = useState<string | null>(null)
  const [unmapSaving, setUnmapSaving] = useState(false)
  const [unmapError, setUnmapError] = useState('')
  const [whatsappConversation, setWhatsappConversation] = useState<{ candidateId: string; conversationId: string } | null>(null)
  const refresh = useRef({ ensureFresh: store.ensureFresh, refresh: store.refresh })
  refresh.current = { ensureFresh: store.ensureFresh, refresh: store.refresh }
  const [loadedId, setLoadedId] = useState<string | null>(null)
  const [loadError, setLoadError] = useState('')
  const [retry, setRetry] = useState(0)
  useEffect(() => {
    let live = true
    setLoadedId(null); setLoadError('')
    setOpenApp(null); setCalling(false); setMapping(false); setEditing(false); setReassigning(false); setDeleting(false); setUnmapping(null)
    const request = retry ? refresh.current.refresh({ keepCurrent: true }) : refresh.current.ensureFresh()
    request.then(() => {
      if (live) setLoadedId(id ?? null)
    }).catch(error => {
      if (live) setLoadError(error instanceof Error ? error.message : 'We could not load this candidate. Please try again.')
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
      if (!controller.signal.aborted) setEvidenceError(cause instanceof Error ? cause.message : 'We could not load the documents. Please try again.')
    }).finally(() => { if (!controller.signal.aborted) setEvidenceLoading(false) })
    return () => controller.abort()
  }, [id, cand, evidenceRetry])
  useEffect(() => { setOcr({}); setFullText(null) }, [id])
  // Live updates already refresh `cand` underneath an open form without touching what was typed (each dialog keeps
  // its own state). If a change from the server altered this candidate while a dialog is open, say so once.
  const dialogOpen = editing || calling || mapping || reassigning || deleting || openApp !== null || unmapping !== null
  const dialogOpenRef = useRef(dialogOpen)
  dialogOpenRef.current = dialogOpen
  const liveChangeAt = useRef(0)
  const candidateApps = cand ? store.applicationsFor(cand.id) : null
  const fingerprint = useMemo(() => (cand ? JSON.stringify([cand, candidateApps]) : ''), [cand, candidateApps])
  const lastFingerprint = useRef(fingerprint)
  useEffect(() => live.subscribe(['candidates', 'applications', 'tasks'], () => { liveChangeAt.current = Date.now() }, { debounceMs: 0 }), [])
  useEffect(() => {
    const before = lastFingerprint.current
    lastFingerprint.current = fingerprint
    if (before && fingerprint && before !== fingerprint && dialogOpenRef.current && Date.now() - liveChangeAt.current < 8_000) {
      store.notify('Updated just now: the latest version of this candidate is loaded. Anything you have typed is untouched.')
    }
  }, [fingerprint])
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
  const barRef = useRef<HTMLDivElement>(null)
  const { tabs, active: activeTab, go } = useProfileTabs(bodyRef, barRef)

  if (loadedId !== id) {
    return <Panel><p role={loadError ? 'alert' : 'status'} className="text-sm text-ink-500">{loadError || 'Loading the candidate…'}</p>
      {loadError && <Button className="mt-3" onClick={() => setRetry(value => value + 1)}>Try again</Button>}</Panel>
  }
  if (!cand || !model) {
    return (
      <EmptyState
        title="We could not find this candidate"
        description="They may have been combined with another candidate."
        action={<Button onClick={() => navigate('/data-bank')}>Back to Data Bank</Button>}
      />
    )
  }

  const owner = store.user(cand.ownerId)
  const apps = store.applicationsFor(cand.id)
  const active = apps.find((a) => a.status === 'active')
  const deployed = apps.some((a) => a.status === 'deployed') || cand.status === 'deployed'
  const canWork = store.can('work_any_candidate') || cand.ownerId === store.me.id
  const canAssign = store.can('reassign')
  const ownerActive = !!cand.ownerId && !!store.user(cand.ownerId)?.active
  const initialTask = store.tasks.find(task => task.candidateId === cand.id && task.kind === 'initial_call' && !task.completedAt && !task.cancelledAt)
  const hasOriginals = cand.documents.some(document => document.key === 'source_bundle' && document.versions.length > 0)
  const resumeFile = newestFileVersions(cand.documents.find(document => document.key === 'resume')?.versions ?? [])[0]

  // One filled button for the next thing that moves this candidate forward; the rest stay outlined.
  const primary: 'assign' | 'stage' | 'map' | null = !cand.ownerId && canAssign ? 'assign' : active ? 'stage' : !deployed ? 'map' : null
  const mapBlocker = mapToOrderBlocker(cand, ownerActive)
  const mapButton = !active && !deployed && (
    <span title={mapBlocker ?? undefined} className="inline-flex">
      <Button variant={primary === 'map' ? 'primary' : 'secondary'} icon={<Link2 />} disabled={!ownerActive} aria-describedby={mapBlocker && apps.length === 0 ? 'map-blocker' : undefined} onClick={() => setMapping(true)}>
        Match to an order
      </Button>
    </span>
  )
  const conversationId = whatsappConversation?.candidateId === cand.id ? whatsappConversation.conversationId : ''
  const photo = newestFileVersions(cand.documents.find(document => document.key === 'photo')?.versions ?? [])[0]
  const unmapApp = apps.find((a) => a.id === unmapping)
  const experience = cand.totalExperience > 0 ? `${cand.totalExperience} ${cand.totalExperience === 1 ? 'year' : 'years'}` : 'Not recorded'
  const overseas = cand.gulfExperience > 0 ? `${cand.gulfExperience} ${cand.gulfExperience === 1 ? 'year' : 'years'} overseas` : 'No overseas experience'
  const pipelineLast = apps.length ? apps[apps.length - 1] : undefined
  const expiry = passportExpiryNotice(cand.passportExpiry)
  // The application the header's stage rail follows: the live one, else the most recent.
  const tracked = active ?? pipelineLast
  const trackedOrder = tracked ? store.order(tracked.orderId) : undefined
  const trackedReached = tracked ? [...new Set(tracked.stages.filter(record => !record.reopenedReason).map(record => record.stage))] : undefined
  const ownerRole = owner ? `${owner.role[0].toUpperCase()}${owner.role.slice(1)}` : ''
  const stats = [
    { label: 'Recruiter', value: owner?.name || 'Not assigned', detail: owner ? `${ownerRole}${initialTask ? ' · initial call to do' : ''}` : 'Needs a recruiter', icon: UserCog, tone: owner ? undefined : 'amber' as const },
    { label: 'Progress', value: active ? `Stage ${active.currentStage} of ${STAGES.length}` : deployed ? 'Deployed' : 'No order yet', detail: active ? `${stageByNo(active.currentStage).title} · ${store.orderLabel(active.orderId)}` : pipelineLast?.closureReason ? `Last: ${closureLabels[pipelineLast.closureReason]}` : deployed ? 'Recruitment finished' : 'Match to an open order', icon: GitBranch, tone: deployed ? 'green' as const : undefined },
    { label: 'Experience', value: experience, detail: overseas, icon: Briefcase },
    { label: 'Passport', value: cand.passportNo || 'Not saved', detail: expiry ? `${expiry.tone === 'expired' ? 'Expired' : 'Expires'} ${dateLabel(cand.passportExpiry)}` : cand.passportExpiry ? `Expires ${dateLabel(cand.passportExpiry)}` : 'No expiry saved', icon: BookUser, tone: expiry ? (expiry.tone === 'expired' ? 'red' as const : 'amber' as const) : undefined },
  ]
  const activity = buildActivity(cand, apps, {
    stageTitle: (no) => stageByNo(no).title,
    closureLabel: (reason) => closureLabels[reason],
    orderLabel: (orderId) => store.orderLabel(orderId),
    userName: (userId) => (userId ? store.user(userId)?.name || 'Former team member' : ''),
    sourceLabel: (source) => SOURCE_LABEL[source] ?? source,
  })

  return (
    <div className="cp">
      <button type="button" className="cp-back" onClick={() => navigate('/data-bank')}><ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />Back to Data Bank</button>

      <header className="cp-head">
        <div className="cp-head-main">
          <div className="cp-id">
            <PersonAvatar name={cand.name} photoUrl={photo?.url} size={72} />
            <div className="cp-id-copy">
              <p className="cp-eyebrow">Candidate · <span className="tnum">{cand.ref}</span></p>
              <h1>{cand.name}</h1>
              {(model.designation || model.meta.length > 0) && <p className="cp-sub">{[model.designation, ...model.meta].filter(Boolean).join(' · ')}</p>}
              <div className="cp-pills">
                <span title={cand.status === 'needs_review' ? 'Check these details on your next call' : undefined}><StatusPill status={cand.status} /></span>
                {cand.needsReview && cand.status !== 'needs_review' && <span title="Check these details on your next call"><Badge tone="amber" variant="dot">Needs review</Badge></span>}
                {expiry && <Badge tone={expiry.tone === 'expired' ? 'stamp' : 'amber'} variant="dot">{expiry.text}</Badge>}
              </div>
            </div>
          </div>
          <div className="cp-actions">
            {canWork && <Button icon={<Pencil />} onClick={() => setEditing(true)}>Edit profile</Button>}
            {conversationId && <Button icon={<MessageCircle />} onClick={() => navigate(conversationHref(conversationId))}>Open WhatsApp chat</Button>}
            {resumeFile && (
              <FileLink url={resumeFile.url} download title={`Download ${resumeFile.name}`} className="cp-linkbtn">
                <Download className="h-4 w-4" aria-hidden="true" />Download resume
              </FileLink>
            )}
            {primary !== 'map' && mapButton}
            {primary === 'assign' && <Button variant="primary" icon={<UserPlus />} onClick={() => setReassigning(true)}>Assign recruiter</Button>}
            {primary === 'stage' && active && <Button variant="primary" icon={<ListChecks />} onClick={() => setOpenApp(active.id)}>Open stage {active.currentStage}</Button>}
            {primary === 'map' && mapButton}
          </div>
        </div>
        <ContactChips contacts={model.contacts} />
        {tracked && (
          <div className="cp-track">
            <div className="cp-track-top">
              <span className="cp-track-order" title={store.orderLabel(tracked.orderId)}><b>{store.orderLabel(tracked.orderId)}</b>{trackedOrder?.ref && <span className="tnum">{trackedOrder.ref}</span>}</span>
              <StageCaption no={tracked.currentStage} status={tracked.status} />
            </div>
            <StageRail status={tracked.status} currentStage={tracked.currentStage} reached={trackedReached} numbered />
          </div>
        )}
      </header>

      <SummaryCards items={stats} />

      <div ref={barRef} className="cp-tabs">
        <nav aria-label="Profile sections">
          <Tabs tabs={tabs.map((tab) => ({ key: tab.id, label: tab.label }))} active={activeTab} onChange={go} />
        </nav>
      </div>

      {/* The details read from the CV come first; the workflow cards sit beside them. Nothing is pinned. */}
      <div ref={bodyRef} className="cp-grid">
        <div className="cp-main">
          {evidenceLoading && <p role="status" className="cp-note">Loading documents…</p>}
          {evidenceError && (
            <Panel>
              <p role="alert" className="text-[14px] text-stamp-700">We could not refresh the documents. {fullText?.id === id ? 'Showing what we loaded before.' : 'The profile is shown, but the details read from the documents are missing.'} {evidenceError}</p>
              <Button className="mt-3" onClick={() => setEvidenceRetry(value => value + 1)}>Try again</Button>
            </Panel>
          )}
          <CandidateSections cand={{ ...cand, resumeText: fullText?.id === id ? fullText.text : '' }} model={model} ocr={ocr} canViewFullIdNumbers={canAssign} />
        </div>
        <aside aria-label="Recruitment workflow" className="cp-aside">
          <ProfileCard title="Next step" icon={<Flag size={17} />} accent>
            <h3 className="cp-next-title">{!cand.ownerId ? 'Assign a recruiter' : active ? `Continue stage ${active.currentStage}: ${stageByNo(active.currentStage).title}` : deployed ? 'Recruitment finished' : initialTask ? 'Make the initial call' : 'Match to a suitable order'}</h3>
            <p className="cp-next-text">{!cand.ownerId ? 'Once a recruiter is assigned, the initial call task starts.' : active ? 'Your progress and deadlines are saved.' : deployed ? 'Documents and history stay on this page.' : initialTask ? `${owner?.name || 'The assigned recruiter'} will make the call${initialTask.dueAt ? ` · due ${dateTimeLabel(initialTask.dueAt)}` : ''}.` : 'Pick an open order to start the 11 stages.'}</p>
            {((!cand.ownerId && canAssign) || (initialTask && canWork) || (active && canWork)) && (
              <div className="cp-next-actions">
                {!cand.ownerId && canAssign && <Button variant="primary" onClick={() => setReassigning(true)}>Assign recruiter</Button>}
                {active && <Button variant="primary" onClick={() => setOpenApp(active.id)}>Open stage {active.currentStage}</Button>}
                {initialTask && canWork && <Button onClick={() => navigate(`/tasks?task=${encodeURIComponent(initialTask.id)}`)}>Open the initial call task</Button>}
              </div>
            )}
          </ProfileCard>

          <ProfileCard title="Recruitment pipeline" icon={<GitBranch size={17} />} meta={apps.length ? `${apps.length} ${apps.length === 1 ? 'order' : 'orders'}. Open one to work on its 11 stages.` : undefined}>
            {apps.length === 0 ? (
              <div>
                <p className="cp-empty-title">Not matched to any order</p>
                <p className="cp-empty-text">Until this candidate is matched to an order, there are no stages to work on.</p>
                {!deployed && mapBlocker && <p id="map-blocker" role="note" className="cp-warn-text">{mapBlocker}</p>}
                {!deployed && ownerActive && <Button variant="primary" className="mt-4" icon={<Link2 />} onClick={() => setMapping(true)}>Match to an order</Button>}
              </div>
            ) : (
              <ul className="cp-apps">
                {apps.map((a) => {
                  const order = store.order(a.orderId)
                  return (
                    <li key={a.id} className={cn('cp-app', `is-${a.status}`)}>
                      <button type="button" onClick={() => setOpenApp(a.id)} className="cp-app-open" aria-label={`Open ${store.orderLabel(a.orderId)}, ${APP_STATUS_LABEL[a.status]}, stage ${a.currentStage}`}>
                        <span className="cp-app-top">
                          <span className="cp-app-copy">
                            <b title={store.orderLabel(a.orderId)}>{store.orderLabel(a.orderId)}</b>
                            <span>{[order?.ref, a.closureReason ? closureLabels[a.closureReason] : ''].filter(Boolean).join(' · ') || 'Order'}</span>
                          </span>
                          <Badge tone={APP_TONE[a.status]} variant="dot">{APP_STATUS_LABEL[a.status]}</Badge>
                        </span>
                        <StageRail status={a.status} currentStage={a.currentStage} reached={[...new Set(a.stages.filter(record => !record.reopenedReason).map(record => record.stage))]} />
                        <span className="cp-app-foot">
                          <StageCaption no={a.currentStage} status={a.status} />
                          <span className="cp-app-when">{dateTimeLabel(a.updatedAt)}</span>
                        </span>
                      </button>
                      {a.status === 'active' && store.can('map_candidates') && (
                        <div className="cp-app-actions">
                          <Button size="sm" variant="dangerGhost" icon={<Unlink />} onClick={() => { setUnmapError(''); setUnmapping(a.id) }}>Remove from this order</Button>
                        </div>
                      )}
                    </li>
                  )
                })}
              </ul>
            )}
          </ProfileCard>

          <ProfileCard
           
            title="Initial call"
            icon={<PhoneCall size={17} />}
            meta="Did you call, are they interested, and your notes"
            action={
              canWork && (
                <Button size="sm" variant="secondary" icon={<PhoneCall />} onClick={() => setCalling(true)}>
                  {cand.callOutcome ? 'Update' : 'Record'}
                </Button>
              )
            }
          >
            {cand.callOutcome ? (
              <>
                <div className="cp-call-result">
                  <Badge tone={cand.callOutcome.called ? 'navy' : 'amber'} variant="dot">Called: {cand.callOutcome.called ? 'Yes' : 'No'}</Badge>
                  <Badge tone={cand.callOutcome.interested === 'yes' ? 'green' : cand.callOutcome.interested === 'no' ? 'amber' : 'neutral'} variant="dot">
                    {cand.callOutcome.interested === 'yes' ? 'Interested' : cand.callOutcome.interested === 'no' ? 'Not interested' : 'Not reachable'}
                  </Badge>
                  <span className="cp-when">{smartTime(cand.callOutcome.recordedAt)}</span>
                </div>
                {cand.callOutcome.remarks && <p className="cp-call-note">{cand.callOutcome.remarks}</p>}
              </>
            ) : (
              <p className="cp-empty-text is-plain">
                No call recorded yet.{initialTask ? ' The initial call task is done once you save the result.' : ''}
              </p>
            )}
          </ProfileCard>

          <ProfileCard
           
            title="Recruiter"
            icon={<UserCog size={17} />}
            action={canAssign && <Button size="sm" onClick={() => setReassigning(true)}>{cand.ownerId ? 'Reassign' : 'Assign'}</Button>}
          >
            <div className="cp-owner">
              {owner ? <RecordIdentity name={owner.name} detail={ownerRole} /> : <RecordIdentity name="Needs assignment" detail="No recruiter yet" icon={UserCog} />}
              <CandidateAssignmentHistory candidateId={cand.id} />
            </div>
          </ProfileCard>

          <ProfileCard title="Documents" icon={<FileText size={17} />} meta={canWork ? DOCUMENT_HELP : undefined}>
            <div className="cp-docs">
              <DocumentList
                documents={cand.documents}
                uploaderName={(userId) => store.user(userId)?.name || 'Former team member'}
                emptyFor={(slot) => hasOriginals && ['resume', 'passport', 'aadhaar'].includes(slot.key) ? 'In the original upload' : undefined}
                onUpload={canWork ? (slot) => {
                  if (slot.key === 'source_bundle') return undefined
                  const multi = slot.key === 'education' || slot.key === 'experience'
                  return async (file) => {
                    await api.upload('candidate', cand.id, slot.key, file)
                    try {
                      await store.refresh()
                      store.notify(multi ? `${file.name} added to ${slot.label.toLowerCase()}` : `${slot.label} uploaded`)
                    } catch {
                      store.notify(multi ? `${file.name} was uploaded. Reload the page to see it.` : `${slot.label} was uploaded. Reload the page to see the saved version.`, 'warn')
                    }
                  }
                } : undefined}
              />
              <EmailEvidenceHistory candidateId={cand.id} />
              <CandidateIntakeEvidence candidateId={cand.id} />
            </div>
          </ProfileCard>
          <div id="profile-activity" data-profile-tab="Activity" className="cp-group empty:hidden">
            <ActivityCard events={activity} />
          </div>
        </aside>
      </div>

      {canDeleteCandidate(store.me.role) && (
        <section className="cp-danger" aria-label="Danger area">
          <div>
            <h2>Danger area</h2>
            <p>Deleting removes this candidate, their documents and their history for good.</p>
          </div>
          <Button variant="dangerGhost" icon={<Trash2 />} onClick={() => setDeleting(true)}>Delete candidate</Button>
        </section>
      )}

      <CallDialog open={calling} onClose={() => setCalling(false)} candidateId={cand.id} />
      {editing && <CandidateEditDialog candidate={cand} onClose={() => setEditing(false)} />}
      {deleting && <DeleteCandidateDialog open candidateId={cand.id} name={cand.name} onClose={() => setDeleting(false)}
        onDeleted={() => { navigate('/data-bank', { replace: true }) }} />}
      {mapping && <MapToOrderDialog open onClose={() => setMapping(false)} candidateId={cand.id} />}
      {reassigning && <ReassignCandidatesDialog candidateIds={[cand.id]} onClose={() => setReassigning(false)} description={cand.ownerId ? 'Change the recruiter responsible for this candidate and their open tasks.' : 'Pick a free recruiter. Once assigned, the initial call task starts.'} />}
      <Dialog
        open={!!unmapApp}
        onClose={() => { if (!unmapSaving) setUnmapping(null) }}
        title="Remove from this order?"
        description={unmapApp ? `${cand.name} will be taken off ${store.orderLabel(unmapApp.orderId)}.` : undefined}
        footer={<>
          <Button variant="ghost" disabled={unmapSaving} onClick={() => setUnmapping(null)}>Keep on this order</Button>
          <Button variant="danger" disabled={unmapSaving} onClick={async () => {
            if (!unmapApp || unmapSaving) return
            setUnmapSaving(true); setUnmapError('')
            try { await store.unmap(unmapApp.id); setUnmapping(null) }
            catch (cause) { setUnmapError(cause instanceof Error ? cause.message : 'We could not remove the candidate from the order. Please try again.') }
            finally { setUnmapSaving(false) }
          }}>{unmapSaving ? 'Removing…' : 'Remove from order'}</Button>
        </>}
      >
        {unmapApp && <div className="cp-unmap">
          <div className="cp-unmap-route"><StageRail status={unmapApp.status} currentStage={unmapApp.currentStage} reached={[...new Set(unmapApp.stages.filter(record => !record.reopenedReason).map(record => record.stage))]} /><StageCaption no={unmapApp.currentStage} status={unmapApp.status} /></div>
          <p className="text-sm leading-6 text-ink-600">This match is closed because the candidate was removed from the order. Its open tasks are cancelled and the candidate goes back to the Data Bank. You can match them to another order later. That starts again at stage 1.</p>
        </div>}
        {unmapError && <p role="alert" className="mt-3 text-sm text-stamp-700">{unmapError}</p>}
      </Dialog>
      <StageModal applicationId={openApp} open={!!openApp} onClose={() => setOpenApp(null)} />
    </div>
  )
}

function ActivityCard({ events }: { events: ReturnType<typeof buildActivity> }) {
  const [all, setAll] = useState(false)
  const shown = all ? events : events.slice(0, 6)
  return (
    <ProfileCard title="Activity" icon={<History size={17} />} meta="Newest first">
      <ol className="cp-activity">
        {shown.map((event) => (
          <li key={event.id} className={ACTIVITY_TONE[event.tone]}>
            <span className="cp-act-dot" aria-hidden="true" />
            <div className="cp-act-body">
              <p className="cp-act-title">{event.title}</p>
              {event.detail && <p className="cp-act-detail">{event.detail}</p>}
              <p className="cp-act-meta">{[event.by, dateTimeLabel(event.at)].filter(Boolean).join(' · ')}</p>
            </div>
          </li>
        ))}
      </ol>
      {events.length > 6 && (
        <Button size="sm" className="cp-act-more" icon={<ChevronDown className={cn('transition-transform', all && 'rotate-180')} />} aria-expanded={all} onClick={() => setAll((value) => !value)}>
          {all ? 'Show less' : 'Show all'}
        </Button>
      )}
    </ProfileCard>
  )
}
