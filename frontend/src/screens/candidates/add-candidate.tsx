import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Briefcase, BriefcaseBusiness, Camera, Check, CheckCircle2, FileText, Image as ImageIcon, PhoneCall, ChevronRight, Fingerprint, FolderOpen, GraduationCap, IdCard, LoaderCircle, Share2, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, ApiError, type OcrScan } from '@/shared/api/client'
import { Badge, PageHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { DOCUMENT_HELP } from '@/shared/lib/document-files'
import { dateTimeLabel } from '@/shared/lib/format'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { hasOcrExtraction, inspectedPageCount, mergeOcrForm, ocrSavePayload, scanWarnings, type DocumentScans, type ScanKind } from '@/features/candidates/ocr-form'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { scansFromJobs, useDocumentIntake } from '@/features/candidates/use-document-intake'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { IntakeRecovery } from '@/features/candidates/components/intake-recovery'
import { EntrySection, TagField } from '@/features/candidates/components/entry-section'
import { MultiFileSlot } from '@/features/candidates/components/multi-file-slot'
import { MAX_FILES_PER_KIND } from '@/features/candidates/use-document-intake'
import { EntryAvatar } from '@/features/candidates/components/entry-avatar'
import '@/styles/add-candidate-v2.css'
import { candidateEntryErrors, EMPTY_CANDIDATE_FORM as EMPTY, restoredCandidateDraft } from '@/features/candidates/entry-model'

type FormKey = keyof typeof EMPTY
type Duplicate = { id: string; ref: string; name: string; phone: string; ownerId: string | null; ownerName: string | null; matchedOn: string[] }
type SavedResult = { ref?: string; name?: string; ownerName: string | null; merged: boolean; at: number }
const SCAN_KINDS: ScanKind[] = ['resume', 'passport', 'aadhaar']
const SLA_MS = 48 * 3600 * 1000
const IMAGE_FILE = /.(jpe?g|png|webp|heic|heif|tiff?)$/i
type Pill = { tone: 'good' | 'warn' | 'bad' | 'busy' | 'muted'; label: string }
/** The reading state shown on every document row, in plain words. */
function documentPill(job: { status: string; kind: string } | undefined, reading: boolean, scan: OcrScan | undefined, scanned: boolean): Pill | null {
  if (!job) return null
  if (!scanned) return job.status === 'attached' ? { tone: 'good', label: 'Attached' } : { tone: 'muted', label: 'Saved' }
  if (reading && !scan) return { tone: 'busy', label: 'Reading' }
  if (scan) return hasOcrExtraction(scan) ? { tone: 'good', label: 'Ready' } : { tone: 'warn', label: 'Please check' }
  if (job.status === 'failed') return { tone: 'bad', label: 'Try again' }
  if (job.status === 'needs_review') return { tone: 'warn', label: 'Please check' }
  if (job.status === 'processing') return { tone: 'busy', label: 'Reading' }
  if (job.status === 'pending' || job.status === 'retry') return { tone: 'muted', label: 'Waiting' }
  if (job.status === 'completed') return { tone: 'good', label: 'Ready' }
  return { tone: 'muted', label: 'Saved' }
}
const VALIDATION_MESSAGE = 'Please fix the fields marked in red, then save again. Nothing you typed was lost.'
/** Names shown for the fields a document filled in, so the reviewer sees what to double-check. */
const FIELD_LABELS: Partial<Record<FormKey, string>> = {
  name: 'Full name', phone: 'Phone', whatsapp: 'WhatsApp', email: 'Email', dob: 'Date of birth', gender: 'Gender', nationality: 'Nationality',
  currentCity: 'Current city', languages: 'Languages', passportNo: 'Passport number', passportIssue: 'Issue date', passportExpiry: 'Expiry date',
  passportPlace: 'Place of issue', designation: 'Job titles', totalExperience: 'Total experience', gulfExperience: 'Gulf experience', skills: 'Skills',
}
function appliedFieldLabels(scan: OcrScan, edited: ReadonlySet<FormKey>): string[] {
  if (!hasOcrExtraction(scan)) return []
  return Object.entries(scan.fields).flatMap(([key, value]) => {
    const label = FIELD_LABELS[key as FormKey]
    const text = Array.isArray(value) ? value.join('') : value == null ? '' : String(value)
    return label && text.trim() && !edited.has(key as FormKey) ? [label] : []
  })
}

function DocumentReadReport({ kind, scan, applied }: { kind: ScanKind; scan: OcrScan; applied: string[] }) {
  const [showPages, setShowPages] = useState(false)
  const label = kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'
  const inspection = scan.inspection
  const extracted = hasOcrExtraction(scan)
  const warnings = scanWarnings(scan)
  const selected = inspection?.selected_pages
    ?? (kind === 'resume' ? inspection?.resume_pages : kind === 'passport' ? inspection?.passport_pages : inspection?.aadhaar_pages)
  const list = (pages: number[] | undefined) => pages?.length ? pages.join(', ') : 'None'
  const title = scan.extraction_status === 'no_resume'
    ? 'No resume found'
    : !extracted ? 'Please check' : 'Details read'

  return (
    <section
      aria-label={`What we read from the ${label.toLowerCase()}`}
      className={`ac-report ${extracted ? 'is-ok' : 'is-warn'}`}
    >
      <p className="ac-report-head">
        <strong>{label}</strong>
        <span className={`ac-pill ${extracted ? 'is-good' : 'is-warn'}`}><i aria-hidden />{title}</span>
      </p>
      <p className="ac-report-copy">
        {extracted
          ? 'Check the details we filled in below before you save. Anything you typed yourself is kept.'
          : 'We could not fill in any details. Try a clearer file, or type the details yourself.'}
      </p>
      {applied.length > 0 && (
        <div className="ac-applied">
          <span className="ac-applied-label">Filled from this document</span>
          <ul>{applied.map((name) => <li key={name}>{name}</li>)}</ul>
        </div>
      )}
      {warnings.length > 0 && (
        <ul className="ac-report-warnings">
          {warnings.map((warning, index) => (
            <li key={index}>
              <AlertTriangle aria-hidden size={14} />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
      {inspection && (
        <div className="ac-report-details">
          <button type="button" className="ac-report-toggle" aria-expanded={showPages} onClick={() => setShowPages((open) => !open)}>
            <ChevronRight size={14} aria-hidden />Page details
          </button>
          {showPages && <div className="ac-report-more">
            <dl>
              <div><dt>Pages checked</dt><dd>{inspectedPageCount(inspection)} of {inspection.page_count}</dd></div>
              <div><dt>Pages used for the {kind}</dt><dd>{list(selected)}</dd></div>
              <div><dt>Pages that look like a resume</dt><dd>{list(inspection.resume_pages)}</dd></div>
              <div><dt>Pages we could not read</dt><dd>{list(inspection.unread_pages)}</dd></div>
              <div><dt>Pages we are not sure about</dt><dd>{list(inspection.unknown_pages)}</dd></div>
            </dl>
            <p>Page numbers match the original file. A page we checked may still be hard to read or unclear.</p>
            {inspection.pages.length > 0 && (
              <ol className="ac-report-pages">
                {inspection.pages.map((page) => (
                  <li key={page.page_number}>
                    <strong>Page {page.page_number}</strong>
                    {' · '}{page.status}; {page.document_type === 'unknown' ? 'type not clear' : page.document_type}
                    {page.reason && <p>{page.reason}</p>}
                  </li>
                ))}
              </ol>
            )}
          </div>}
        </div>
      )}
    </section>
  )
}

export default function AddCandidatePage() {
  const store = useStore()
  const navigate = useNavigate()
  const intake = useDocumentIntake(store.me!.id)
  const [form, setForm] = useState(EMPTY)
  const [saving, setSaving] = useState(false)
  const [bundleSource, setBundleSource] = useState<string | null>(null)
  const [savedCandidateId, setSavedCandidateId] = useState<string | null>(null)
  const [savedResult, setSavedResult] = useState<SavedResult | null>(null)
  const [pendingRecovery, setPendingRecovery] = useState<string | null>(null)
  const [recovering, setRecovering] = useState(false)
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({})
  const [saveError, setSaveError] = useState('')

  const [duplicate, setDuplicate] = useState<{ candidate: Duplicate; matchedOn: string[] } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  // Inspection can succeed without extraction, so it has its own visible result.
  const [scans, setScans] = useState<DocumentScans>({})
  const scansRef = useRef<DocumentScans>({})
  const edited = useRef(new Set<FormKey>())
  const readController = useRef<AbortController | null>(null)
  const [readErrors, setReadErrors] = useState<Partial<Record<ScanKind, string>>>({})
  const [readMessage, setReadMessage] = useState('')
  const [reading, setReading] = useState(false)

  useEffect(() => () => {
    readController.current?.abort()
    readController.current = null
  }, [])

  const set = (k: FormKey, v: string) => {
    // A ref also catches changes made after a scan starts but before it returns.
    edited.current.add(k)
    setForm((f) => ({ ...f, [k]: v }))
    setErrors((e) => ({ ...e, [k]: '' }))
    setSaveError('')
  }

  const snapshotDraft = (candidateId = savedCandidateId) => ({ form, editedFields: [...edited.current], savedCandidateId: candidateId })
  const draftRef = useRef(snapshotDraft())
  draftRef.current = snapshotDraft()
  useEffect(() => {
    if (!intake.jobs.length) return
    const timer = setTimeout(() => { void intake.saveDraft(draftRef.current).catch(() => {}) }, 700)
    return () => clearTimeout(timer)
  }, [form, intake.jobs.length, savedCandidateId])

  /** Originals are durable before reading; choosing a replacement keeps human edits. */
  const choose = (kind: ScanKind) => async (file: File) => {
    if (readController.current || saving || savedCandidateId) return
    const hadOtherScanDocument = intake.jobs.some((job) => job.kind !== kind && SCAN_KINDS.some((value) => value === job.kind))
    await intake.upload(kind, file)
    if (!store.isCurrentSession()) return
    const { [kind]: _, ...remaining } = scansRef.current
    scansRef.current = remaining
    setScans(remaining)
    setForm((previous) => mergeOcrForm(previous, EMPTY, remaining, edited.current))
    setReadErrors(({ [kind]: _, ...rest }) => rest)
    setReadMessage('Your file is saved. You can leave this page and carry on later.')
    await intake.saveDraft(draftRef.current).catch(() => {})
    // PRD: a resume uploaded first pre-fills the form. Passport and Aadhaar wait for Read documents.
    if (kind === 'resume' && !hadOtherScanDocument) {
      setReadMessage('Your file is saved. We are reading the resume to fill in the form.')
      void readDocuments(true)
    }
  }

  const chosen = intake.jobs.filter((job) => SCAN_KINDS.some((kind) => job.kind === kind)).map((job) => job.kind)
  const recoverIntake = async (groupId: string) => {
    if (saving || reading || intake.uploading || recovering) return
    setRecovering(true)
    try {
      await intake.saveDraft(snapshotDraft())
      const recovered = await intake.recover(groupId)
      if (!store.isCurrentSession()) return
      if (recovered.jobs.some(job => job.draft?.splitCandidateIds?.length)) {
        setBundleSource(groupId)
        intake.reset()
        setForm(EMPTY); edited.current = new Set(); scansRef.current = {}; setScans({})
        setSavedCandidateId(null); setPendingRecovery(null)
        return
      }
      const read = scansFromJobs(recovered.jobs)
      const draft = recovered.draft
      const restored = restoredCandidateDraft(draft)
      edited.current = restored.edited
      scansRef.current = read
      setScans(read)
      setForm(mergeOcrForm(restored.form, EMPTY, read, edited.current))
      setSavedCandidateId(draft?.savedCandidateId ?? recovered.jobs.find((job) => job.candidateId)?.candidateId ?? null)
      setReadErrors({}); setSaveError(''); setUploadErrors({}); setErrors({}); setDuplicate(null); setPendingRecovery(null)
      setReadMessage('Your saved files, what we had already read, and your form have been brought back.')
    } catch { /* The intake panel keeps the recovery error visible. */ }
    finally { setRecovering(false) }
  }

  const requestRecovery = async (groupId: string) => {
    if (edited.current.size || intake.jobs.length) setPendingRecovery(groupId)
    else await recoverIntake(groupId)
  }

  const readDocuments = async (auto = false) => {
    if (readController.current || saving || savedCandidateId) return
    if (!auto && (intake.uploading || chosen.length === 0)) return
    const controller = new AbortController()
    readController.current = controller
    setReading(true); setReadErrors({}); if (!auto) setReadMessage('')
    try {
      await intake.saveDraft(draftRef.current)
      const jobs = await intake.read(controller.signal)
      if (readController.current !== controller || !store.isCurrentSession()) return
      const read = scansFromJobs(jobs)
      const failures: Partial<Record<ScanKind, string>> = {}
      for (const job of jobs) if (job.status === 'failed' && SCAN_KINDS.some((kind) => job.kind === kind)) failures[job.kind as ScanKind] = job.lastError ?? 'We could not read this file. Try again, or type the details yourself.'
      scansRef.current = read; setScans(read); setReadErrors(failures)
      setForm((previous) => mergeOcrForm(previous, EMPTY, read, edited.current))
      setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => edited.current.has(key as FormKey))))
      // The reminder about highlighted fields is stale once documents have filled them in.
      setSaveError((message) => message === VALIDATION_MESSAGE ? '' : message)
    } catch (error) {
      if (readController.current === controller && store.isCurrentSession()) setReadMessage(error instanceof Error ? error.message : 'We could not finish reading. Your file is saved.')
    } finally {
      if (readController.current === controller) { readController.current = null; setReading(false) }
    }
  }

  const cancelReading = () => {
    readController.current?.abort()
    readController.current = null
    setReading(false)
    setForm((prev) => mergeOcrForm(prev, EMPTY, scansRef.current, edited.current))
    setReadMessage('You stopped waiting. We keep reading in the background. Come back later, or type the details yourself.')
  }


  const save = async () => {
    if (saving || reading || intake.uploading || recovering) return
    const next = savedCandidateId ? {} : candidateEntryErrors(form)
    setErrors(next)
    if (Object.keys(next).length) {
      setSaveError(VALIDATION_MESSAGE)
      // The form is long; bring the first problem into view.
      requestAnimationFrame(() => document.querySelector('.ac-fields .ui-field > .text-stamp-600')?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
      return
    }

    setSaving(true)
    setSaveError('')
    try {
      if (savedCandidateId) await uploadDocuments(savedCandidateId)
      else await doSave()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && typeof error.detail === 'object' && error.detail && 'candidate' in error.detail) {
        const candidate = (error.detail as { candidate: Duplicate }).candidate
        setDuplicate({ candidate, matchedOn: candidate.matchedOn })
      }
      if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'We could not save this candidate. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const doSave = async () => {
    const result = await api.post<{ duplicate: boolean; candidate?: Duplicate }>('/candidates/check-duplicate', payload())
    if (result.duplicate && result.candidate) {
      setDuplicate({ candidate: result.candidate, matchedOn: result.candidate.matchedOn })
      return
    }
    await commit()
  }

  const payload = () => ({
      name: form.name.trim(),
      phone: form.phone.trim(),
      whatsapp: form.whatsapp.trim() || undefined,
      email: form.email.trim() || undefined,
      dob: form.dob || undefined,
      gender: form.gender || undefined,
      nationality: form.nationality,
      current_city: form.currentCity,
      languages: form.languages.split(',').map((s) => s.trim()).filter(Boolean),
      passport_no: form.passportNo.trim() || undefined,
      passport_issue: form.passportIssue || undefined,
      passport_expiry: form.passportExpiry || undefined,
      passport_place: form.passportPlace || undefined,
      designations: normalizedSkills(form.designation.split(',')),
      total_experience: Number(form.totalExperience) || 0,
      gulf_experience: Number(form.gulfExperience) || 0,
      skills: normalizedSkills(form.skills.split(/[,;\n]/)),
      expected_salary: form.expectedSalary.trim() ? Number(form.expectedSalary) : undefined,
      source: form.source,
      source_detail: form.sourceDetail.trim() || undefined,
      // The server prefers the scanned resume's own text; this covers a hand-filled form.
      resume_text: `${form.name} — ${form.designation}, ${form.totalExperience || 0} years. ${form.skills}`,
      // Verbatim Veris results plus their provenance under `_sources`.
      ocr_data: ocrSavePayload(scans),
      intake_group_id: intake.jobs[0]?.groupId,
    })

  const commit = async (mergeId?: string) => {
    const created = mergeId
      ? await api.post<{ id: string; ref?: string; name?: string; ownerName?: string | null }>(`/candidates/${mergeId}/merge`, payload())
      : await store.addCandidate(payload()) as { id: string; ref?: string; name?: string; ownerName?: string | null } | null
    if (!created) return
    const ownerName = created.ownerName ?? null
    if (mergeId) store.notify(`Combined with ${created.ref ?? 'the existing candidate'}${ownerName ? `. This candidate stays with ${ownerName}` : ''}. Empty fields were filled in. No new task was created.`)
    else store.notify(ownerName ? `Saved. Assigned to ${ownerName}. The initial call is due within 48 hours.` : 'Saved. No recruiter is free right now, so a supervisor will assign this candidate.', ownerName ? undefined : 'warn')
    setSavedResult({ ref: created.ref, name: created.name, ownerName, merged: Boolean(mergeId), at: Date.now() })
    setSavedCandidateId(created.id)
    setDuplicate(null)
    await intake.saveDraft(snapshotDraft(created.id))
    await uploadDocuments(created.id)
  }

  const uploadDocuments = async (candidateId: string) => {
    await intake.saveDraft(snapshotDraft(candidateId))
    const failed: Record<string, string> = await intake.attach(candidateId)
    if (!store.isCurrentSession()) return
    setUploadErrors(failed)
    if (!Object.keys(failed).length) await openSavedProfile(candidateId)
  }

  const openSavedProfile = async (candidateId: string) => {
    // Read after uploads so the profile contains the newly saved original files.
    // A failed read leaves this recovery screen mounted with the saved record ID.
    await store.refresh({ keepCurrent: true })
    if (store.isCurrentSession()) navigate(`/candidates/${candidateId}`)
  }

  // The saved result (owner, retry buttons) sits above the form; make sure it is what the user sees.
  useEffect(() => {
    if (savedCandidateId) document.querySelector('.ac-saved')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [savedCandidateId])

  const busyAny = saving || reading || intake.uploading || recovering
  const missing = [!form.name.trim() && 'full name', !form.phone.trim() && 'phone'].filter(Boolean) as string[]
  const steps = [
    { label: 'Candidate details', detail: 'Name and phone are required.', done: missing.length === 0 },
    { label: 'Documents (optional)', detail: 'Add a resume and we fill in the details.', done: chosen.length > 0 },
    { label: 'Save and assign', detail: 'A free recruiter gets 48 hours to make the initial call.', done: Boolean(savedCandidateId) },
  ]
  // The step the recruiter is on: the required details, then (documents are optional) save.
  const currentStep = savedCandidateId || missing.length === 0 ? 2 : 0
  const sourceHint = form.source === 'referral' ? 'Referred by' : form.source === 'other' ? 'Tell us where from' : 'More about the source (optional)'
  const sourcePlaceholder = form.source === 'referral' ? 'Name of the person who referred them' : form.source === 'other' ? 'Where did this candidate come from?' : form.source === 'walk_in' ? 'Branch or event, e.g. Kochi office' : 'Optional note'
  const failedUploads = Object.keys(uploadErrors).length
  const kindLabel = (kind: ScanKind) => kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'
  /** The row icon follows the uploaded file's type; an empty slot keeps its own icon. */
  const slotIcon = (fallback: typeof FileText, filename?: string) => filename ? (IMAGE_FILE.test(filename) ? ImageIcon : FileText) : fallback
  const jobFile = (kind: string) => {
    const job = intake.jobs.find((item) => item.kind === kind)
    return job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null
  }
  const mergeBlocked = !!duplicate && !store.can('work_any_candidate') && duplicate.candidate.ownerId !== store.me!.id

  return (
    <>
      <PageHeader
        title="Add candidate"
        description="Fill in the details, add documents if you have them, then save. A recruiter then gets the initial call to make."
      />

      <div
        className="candidate-entry ac"
        onKeyDown={(event) => {
          if (event.key === 'Enter' && (event.ctrlKey || event.metaKey) && !savedCandidateId && !event.defaultPrevented) { event.preventDefault(); void save() }
        }}
      >
        <ol className="ac-steps" aria-label="Steps to add a candidate">
          {steps.map((step, index) => (
            <li key={step.label} className={`${step.done ? 'is-done' : ''} ${index === currentStep ? 'is-current' : ''}`} aria-current={index === currentStep ? 'step' : undefined}>
              <span aria-hidden>{step.done ? <Check size={14} strokeWidth={2.75} /> : index + 1}</span>
              <div><strong>{step.label}</strong><small>{step.detail}</small></div>
            </li>
          ))}
        </ol>

        <div className="ac-stack">
          {bundleSource && <DocumentBundleReview sourceType="manual" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={async result => { intake.reset(); setForm(EMPTY); edited.current = new Set(); scansRef.current = {}; setScans({}); setSavedCandidateId(null); setSavedResult(null); setErrors({}); setReadErrors({}); setUploadErrors({}); setSaveError(''); setReadMessage(`${result.candidateIds.length} candidate profiles saved from the reviewed document pages. This form is ready for a new candidate.`); await Promise.allSettled([store.refresh({ keepCurrent: true }), intake.reload()]) }} />}
          {saveError && <p role="alert" className="ac-alert"><AlertTriangle size={16} aria-hidden /><span>{saveError}</span></p>}
          {savedCandidateId && <EntrySection icon={CheckCircle2} className="ac-saved"
            title={savedResult?.merged ? 'Combined with the existing candidate' : failedUploads ? 'Candidate saved. Some documents are not attached yet.' : 'Candidate saved'}
            meta={savedResult ? `${savedResult.name ?? 'This candidate'}${savedResult.ref ? ` · ${savedResult.ref}` : ''}` : 'Finish attaching the documents, or open the profile.'}>
            <div className="ac-body" role="alert">
              {savedResult && <div className="ac-result">
                <div className="ac-result-tile">
                  <span className="ac-result-label">{savedResult.merged ? 'Stays with' : 'Assigned to'}</span>
                  <span className="ac-result-owner">
                    {savedResult.ownerName ? <><EntryAvatar name={savedResult.ownerName} /><strong>{savedResult.ownerName}</strong></> : <strong>No recruiter yet</strong>}
                  </span>
                  <small>{savedResult.ownerName ? (savedResult.merged ? 'Combining does not change who owns this candidate.' : 'Chosen automatically from the recruiters who are free.') : 'No recruiter is free right now. A supervisor will assign this candidate.'}</small>
                </div>
                <div className="ac-result-tile">
                  <span className="ac-result-label">Initial call</span>
                  <span className="ac-result-owner"><span className="ac-result-icon"><PhoneCall size={16} aria-hidden /></span><strong>{savedResult.merged || !savedResult.ownerName ? 'No new task' : `Due by ${dateTimeLabel(new Date(savedResult.at + SLA_MS))}`}</strong></span>
                  <small>{savedResult.merged ? 'Combining only fills empty fields. It does not create a new task.' : savedResult.ownerName ? 'The recruiter has 48 hours to make the first call.' : 'The 48-hour clock starts once someone is assigned.'}</small>
                </div>
              </div>}
              <p className="ac-muted">Finish attaching the documents, or open the profile. Anything already attached stays attached.</p>
              {failedUploads > 0 && <ul className="ac-failed">{Object.entries(uploadErrors).map(([slot, error]) => <li key={slot}>{error}</li>)}</ul>}
              <div className="ac-actions">
                <Button variant="primary" disabled={saving || recovering} onClick={save}>{saving ? 'Please wait…' : failedUploads ? 'Try the failed uploads again' : 'Finish attaching documents'}</Button>
                <Button disabled={saving || recovering} onClick={async () => {
                  setSaving(true)
                  try { await openSavedProfile(savedCandidateId) }
                  catch (error) { if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'We could not open the profile. Please try again.') }
                  finally { setSaving(false) }
                }}>Open saved profile</Button>
              </div>
              {failedUploads > 0 && <p className="ac-muted ac-small">Your files are still kept under Saved uploads. Open that upload to try attaching them again.</p>}
            </div>
          </EntrySection>}

          <fieldset disabled={saving || recovering || Boolean(savedCandidateId)} className="ac-grid">
            <div className="ac-hint-bar">
              <Sparkles size={16} aria-hidden />
              <p>Have a resume? Add it under Documents and we will fill in these fields. You can still change anything.</p>
              <button type="button" onClick={() => document.getElementById('candidate-documents')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>Go to Documents</button>
            </div>
            <EntrySection id="candidate-basic" className="ac-s-12" icon={UserRound} title="Candidate details" meta="Name and phone are required.">
              <div className="ac-fields ac-fields-basic">
                <Field label="Full name" required error={errors.name}>
                  <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="As written in the passport" />
                </Field>
                <Field label="Email" error={errors.email}>
                  <Input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="candidate@example.com" />
                </Field>
                <Field label="Phone" required error={errors.phone}>
                  <Input ident value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+91 98400 00000" />
                </Field>
                <Field label="WhatsApp number">
                  <Input ident value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} placeholder="If different from phone" />
                </Field>
                <Field label="Current city">
                  <Input value={form.currentCity} onChange={(e) => set('currentCity', e.target.value)} />
                </Field>
                <Field label="Date of birth" error={errors.dob}>
                  <DatePicker value={form.dob} onChange={(value) => set('dob', value)} />
                </Field>
                <Field label="Gender">
                  <Select value={form.gender} onChange={(e) => set('gender', e.target.value)}>
                    <option value="">Not recorded</option>
                    <option value="male">Male</option>
                    <option value="female">Female</option>
                  </Select>
                </Field>
                <Field label="Nationality">
                  <Input value={form.nationality} onChange={(e) => set('nationality', e.target.value)} />
                </Field>
                <TagField label="Languages" className="ac-lang" value={form.languages} onChange={(value) => set('languages', value)} placeholder="Add a language" />
              </div>
            </EntrySection>

            <EntrySection id="candidate-passport" className="ac-s-12" icon={IdCard} title="Passport details" meta="Add a copy under Documents. The original passport is collected at stage 7.">
              <div className="ac-fields ac-fields-passport">
                <Field label="Passport number" className="ac-pp-no">
                  <Input ident value={form.passportNo} onChange={(e) => set('passportNo', e.target.value)} placeholder="M1234567" />
                </Field>
                <Field label="Place of issue" className="ac-pp-pl">
                  <Input value={form.passportPlace} onChange={(e) => set('passportPlace', e.target.value)} />
                </Field>
                <Field label="Issue date" className="ac-pp-is">
                  <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />
                </Field>
                <Field label="Expiry date" error={errors.passportExpiry} className="ac-pp-ex">
                  <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />
                </Field>
              </div>
            </EntrySection>

            <EntrySection id="candidate-work" className="ac-s-12" icon={BriefcaseBusiness} title="Work profile" meta="Skills help us search the Data Bank and match candidates to orders.">
              <div className="ac-fields ac-fields-work">
                <TagField label="Preferred job titles" className="ac-w-des" value={form.designation} onChange={(value) => set('designation', value)}
                  suggestions={store.designations.map((designation) => designation.title)} placeholder="Pick from the list, or type and press Enter" />
                <Field label="Total experience" error={errors.totalExperience} className="ac-w-tot">
                  <Input type="number" min="0" step="1" value={form.totalExperience} onChange={(e) => set('totalExperience', e.target.value)} placeholder="Years" />
                </Field>
                <Field label="Gulf or overseas experience" error={errors.gulfExperience} className="ac-w-gulf">
                  <Input type="number" min="0" step="1" value={form.gulfExperience} onChange={(e) => set('gulfExperience', e.target.value)} placeholder="Years" />
                </Field>
                <Field label="Expected salary" error={errors.expectedSalary} className="ac-w-sal">
                  <Input type="number" min="0" step="0.01" value={form.expectedSalary} onChange={(e) => set('expectedSalary', e.target.value)} placeholder="Per month" />
                </Field>
                <Field label="Skills" hint="Separate with commas or new lines." className="ac-w-skills">
                  <Textarea value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG welding, Pipe fitting, Blueprint reading" />
                </Field>
              </div>
            </EntrySection>

            <EntrySection id="candidate-source" className="ac-s-12" icon={Share2} title="Source" meta="How this candidate found us.">
              <div className="ac-fields ac-fields-source">
                <Field label="Source" className="ac-src-a">
                  <Select value={form.source} onChange={(e) => set('source', e.target.value)}>
                    <option value="walk_in">Walk-in</option><option value="referral">Referral</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="other">Other</option>
                  </Select>
                </Field>
                <Field label={sourceHint} className="ac-src-b">
                  <Input value={form.sourceDetail} onChange={(e) => set('sourceDetail', e.target.value)} placeholder={sourcePlaceholder} maxLength={255} />
                </Field>
              </div>
            </EntrySection>

            <EntrySection id="candidate-documents" className="ac-s-12 ac-docs" icon={FolderOpen} title="Documents" meta="Optional. Add the resume first and we will fill in the details above.">
              <div className="ac-body">
                <p className="ac-muted ac-small">{DOCUMENT_HELP}</p>
                <div className="ac-doc-group">
                  <p className="ac-subhead">We read these to fill in the form</p>
                  <div className="ac-slots">
                    {SCAN_KINDS.map((kind) => {
                      const job = intake.jobs.find((item) => item.kind === kind)
                      const pill = documentPill(job, reading, scans[kind], true)
                      return <div key={kind} className={`ac-slot ${pill ? 'has-pill' : ''}`}>
                        <FileDrop variant="row" optional icon={slotIcon(kind === 'resume' ? FileText : kind === 'passport' ? ShieldCheck : Fingerprint, job?.filename)}
                          label={kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport copy' : 'Aadhaar copy'}
                          value={jobFile(kind)}
                          onFile={choose(kind)} disabled={reading || saving || intake.uploading} busy={reading && !!job} busyLabel="reading…"
                          help={kind === 'resume' ? 'Saved now. We read it to fill in the form.' : 'Saved now. We read it to fill in the form.'} />
                        {pill && <span className={`ac-pill ac-slot-pill is-${pill.tone}`}><i aria-hidden />{pill.label}</span>}
                      </div>
                    })}
                  </div>
                </div>
                <div className="ac-doc-group">
                  <p className="ac-subhead">Added to the profile when you save</p>
                  <div className="ac-slots">
                    {(() => {
                      const job = intake.jobs.find((item) => item.kind === 'photo')
                      const pill = documentPill(job, false, undefined, false)
                      return <div className={`ac-slot ${pill ? 'has-pill' : ''}`}>
                        <FileDrop variant="row" optional icon={slotIcon(Camera, job?.filename)} label="Photo" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                          value={jobFile('photo')}
                          help="Added when you save."
                          onFile={async (file) => { await intake.upload('photo', file); await intake.saveDraft(draftRef.current).catch(() => {}) }} />
                        {pill && <span className={`ac-pill ac-slot-pill is-${pill.tone}`}><i aria-hidden />{pill.label}</span>}
                      </div>
                    })()}
                    {([
                      { slot: 'education', label: 'Education certificates', icon: GraduationCap },
                      { slot: 'experience', label: 'Experience certificates', icon: Briefcase },
                    ] as const).map(({ slot, label, icon }) => (
                      <div key={slot} className="ac-slot ac-slot-multi">
                        <MultiFileSlot label={label} icon={icon} help="You can add more than one file. They are added to the profile when you save." max={MAX_FILES_PER_KIND}
                          disabled={saving || reading || Boolean(savedCandidateId)}
                          files={intake.jobs.filter((item) => item.kind === slot).map((item) => ({ id: item.id, name: item.filename, sizeKb: item.sizeKb, url: item.originalUrl, status: documentPill(item, false, undefined, false) ? { label: documentPill(item, false, undefined, false)!.label, tone: documentPill(item, false, undefined, false)!.tone } : undefined }))}
                          onAdd={async (files) => {
                            for (const file of files) await intake.upload(slot, file)
                            await intake.saveDraft(draftRef.current).catch(() => {})
                          }}
                          onRemove={async (id) => { await intake.remove(id); await intake.saveDraft(draftRef.current).catch(() => {}) }} />
                      </div>
                    ))}
                  </div>
                </div>
                <div className="ac-doc-foot">
                  <div className="ac-doc-status" aria-live="polite">
                    {(reading || intake.uploading) && <span className="ac-progress" role="progressbar" aria-label={reading ? 'Reading documents' : 'Saving your file'}><i /></span>}
                    <span>
                      {reading
                        ? <><LoaderCircle size={14} className="ac-spin" aria-hidden /> We keep reading in the background if you leave. Choose Stop waiting to carry on without it.</>
                        : intake.uploading ? <><LoaderCircle size={14} className="ac-spin" aria-hidden /> Saving your file…</>
                        : 'If the passport and the resume disagree, we use the passport for fields you have not changed.'}
                    </span>
                  </div>
                  <div className="ac-doc-buttons">
                    {intake.jobs[0]?.groupId && <Button disabled={reading || saving || intake.uploading} onClick={() => setBundleSource(intake.jobs[0].groupId)}>Check people in documents</Button>}
                    {reading && <Button variant="ghost" onClick={cancelReading}>Stop waiting</Button>}
                    <Button
                      variant="primary"
                      onClick={() => void readDocuments()}
                      disabled={reading || saving || intake.uploading || chosen.length === 0}
                      icon={<Sparkles className="h-4 w-4" />}
                    >
                      {reading ? 'Reading…' : 'Read documents'}
                    </Button>
                  </div>
                </div>
                {(readMessage || SCAN_KINDS.some((kind) => readErrors[kind] || scans[kind])) && <div aria-live="polite" aria-atomic="false" className="ac-reports">
                  {readMessage && <p className="ac-muted ac-small ac-read-message">{readMessage}</p>}
                  <div className="ac-report-grid">
                    {SCAN_KINDS.map((kind) => (
                      readErrors[kind] ? (
                        <p key={kind} role="alert" className="ac-report is-warn">
                          <strong>{kindLabel(kind)}:</strong> {readErrors[kind]} You can type the details yourself and save.
                        </p>
                      ) : scans[kind] ? <DocumentReadReport key={kind} kind={kind} scan={scans[kind]!} applied={appliedFieldLabels(scans[kind]!, edited.current)} /> : null
                    ))}
                  </div>
                </div>}
              </div>
            </EntrySection>
          </fieldset>

          <IntakeRecovery intake={intake} disabled={busyAny} onRecover={requestRecovery} onSaveDraft={() => intake.saveDraft(snapshotDraft())} />

          {!savedCandidateId && <div className="ac-savebar">
            <div className="ac-savebar-copy">
              <span className={`ac-pill ${missing.length ? 'is-warn' : 'is-good'}`}><i aria-hidden />{missing.length ? 'Missing details' : 'Ready'}</span>
              <span>
                <strong>{missing.length ? `Still needed: ${missing.join(' and ')}.` : 'Ready to save.'}</strong>
                {' '}When you save, we first check if this candidate is already in the Data Bank, then give them to a free recruiter. If no one is free, a supervisor will assign them.
              </span>
            </div>
            <div className="ac-savebar-actions">
              <Button variant="ghost" disabled={saving} onClick={() => navigate('/data-bank')}>
                Cancel
              </Button>
              <Button variant="primary" onClick={save} disabled={busyAny} title="Save and assign (Ctrl+Enter)">
                {saving ? 'Saving…' : reading ? 'Reading documents…' : 'Save and assign'}
              </Button>
            </div>
          </div>}
        </div>
      </div>

      <Dialog open={Boolean(pendingRecovery)} onClose={() => { if (!recovering) setPendingRecovery(null) }} title="Open a different saved upload?"
        description={intake.jobs.length ? 'Your current form and files will be kept as their own saved upload. The one you pick will open with its own details.' : 'Your current form has no files and is not backed up. Opening the saved upload will replace what you typed.'}
        footer={<div className="ac-dialog-actions"><Button disabled={recovering} onClick={() => setPendingRecovery(null)}>Stay on this form</Button><Button variant="primary" disabled={recovering} onClick={() => { if (pendingRecovery) void recoverIntake(pendingRecovery) }}>{recovering ? 'Opening…' : 'Open saved upload'}</Button></div>}>
        <p className="text-sm text-ink-500">Details of different candidates are kept apart. Check the name and contact details before you save.</p>
        {intake.draftError && <p role="alert" className="mt-3 text-sm text-stamp-600">{intake.draftError}</p>}
      </Dialog>

      <Dialog
        open={!!duplicate}
        onClose={() => { if (!saving) setDuplicate(null) }}
        title="This candidate is already in the Data Bank"
        description={
          duplicate
            ? `The ${duplicate.matchedOn.join(' and ')} already belong to a candidate in the Data Bank. You can combine this entry with that candidate, or cancel.`
            : undefined
        }
        footer={
          <div className="ac-dialog-actions">
            <Button variant="ghost" disabled={saving} onClick={() => setDuplicate(null)}>
              Cancel
            </Button>
            <Button
              disabled={saving}
              onClick={() => {
                const id = duplicate!.candidate.id
                setDuplicate(null)
                navigate(`/candidates/${id}`)
              }}
            >
              Open existing candidate
            </Button>
            <Button variant="primary" disabled={saving || mergeBlocked} onClick={async () => {
              if (!duplicate) return
              setSaving(true); setSaveError('')
              try { await commit(duplicate.candidate.id) } catch (error) { setSaveError(error instanceof Error ? error.message : 'We could not combine them. Nothing you typed was lost.') } finally { setSaving(false) }
            }}>{saving ? 'Combining…' : 'Combine with existing'}</Button>
          </div>
        }
      >
        {duplicate && (
          <div className="ac-dup">
            <div className="ac-dup-card is-existing">
              <EntryAvatar name={duplicate.candidate.name} size="lg" />
              <div className="ac-dup-body">
                <p className="ac-dup-label">Already in the Data Bank</p>
                <p className="ac-dup-name">{duplicate.candidate.name}</p>
                <p className="ac-dup-meta ident" title={duplicate.candidate.phone}>{duplicate.candidate.phone}</p>
                <div className="ac-dup-badges">
                  <Badge>{duplicate.candidate.ref}</Badge>
                  <Badge>Recruiter: {duplicate.candidate.ownerName ?? 'Not assigned yet'}</Badge>
                  {duplicate.matchedOn.map((field) => <Badge key={field} tone="amber">Same {field}</Badge>)}
                </div>
              </div>
            </div>
            <div className="ac-dup-card">
              <EntryAvatar name={form.name.trim() || 'New candidate'} size="lg" />
              <div className="ac-dup-body">
                <p className="ac-dup-label">What you typed</p>
                <p className="ac-dup-name">{form.name.trim() || 'Unnamed'}</p>
                <p className="ac-dup-meta ident">{form.phone.trim() || 'No phone'}</p>
                {form.email.trim() && <p className="ac-dup-meta" title={form.email.trim()}>{form.email.trim()}</p>}
                {form.passportNo.trim() && <p className="ac-dup-meta ident">Passport {form.passportNo.trim()}</p>}
              </div>
            </div>
          </div>
        )}
        <p className="ac-dup-note">
          <AlertTriangle size={14} aria-hidden />
          <span>Combining fills in empty fields and adds your documents as new copies. Details already saved, the recruiter and the stages stay as they are. No new task is created.</span>
        </p>
        {mergeBlocked && duplicate && (
          <p className="ac-dup-note is-warn">Only the recruiter who has this candidate ({duplicate.candidate.ownerName ?? 'nobody yet'}) or a supervisor can combine into this candidate. You can open the candidate instead.</p>
        )}
        {saveError && <p role="alert" className="mt-3 text-sm text-stamp-600">{saveError}</p>}
      </Dialog>
    </>
  )
}
