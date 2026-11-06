import { useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from 'react'
import { choosesRecruiter } from '@/features/candidates/candidate-access'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ClipboardList, Briefcase, BriefcaseBusiness, Camera, Check, CheckCircle2, CalendarClock, FileText, Gauge, Image as ImageIcon, PhoneCall, Fingerprint, FolderOpen, GraduationCap, IdCard, LoaderCircle, Share2, ShieldCheck, Sparkles, Target, UserRound, Contact, Trash2 } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, ApiError, type OcrScan } from '@/shared/api/client'
import { PageHeader } from '@/shared/ui/display'
import { Button, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { DOCUMENT_HELP } from '@/shared/lib/document-files'
import { dateTimeLabel } from '@/shared/lib/format'
import { DatePicker } from '@/shared/ui/date-picker'
import { hasOcrExtraction, mergeOcrForm, ocrSavePayload, type DocumentScans, type ScanKind } from '@/features/candidates/ocr-form'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { passportExpiryNotice } from '@/features/candidates/passport-expiry'
import { scansFromJobs, useDocumentIntake, MAX_FILES_PER_KIND } from '@/features/candidates/use-document-intake'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { DocumentReadReport } from '@/features/candidates/components/document-read-report'
import { IntakeRecovery } from '@/features/candidates/components/intake-recovery'
import { EntrySection, TagField } from '@/features/candidates/components/entry-section'
import { EntryField, FormSection, InterestCards, PhoneField, ReadBadge, SameAs } from '@/features/candidates/components/entry-fields'
import { EntryRowsEditor } from '@/features/candidates/components/entry-rows-editor'
import { EntryProgress } from '@/features/candidates/components/entry-progress'
import { entryCompleteness, nextDocument, screeningMissing } from '@/features/candidates/entry-completeness'
import { effectiveNativePlace, effectiveWhatsapp, screeningPayload, whatsappIsSame } from '@/features/candidates/entry-extras'
import { useFormOptions, useFormSuggestions } from '@/features/form-options/use-form-options'
import { activeOptions, choicesFor, FORM_OPTION_CATEGORIES as OPT } from '@/features/form-options/form-options-model'
import { frequentValues } from '@/shared/lib/suggestions'
import { MultiFileSlot } from '@/features/candidates/components/multi-file-slot'
import { EntryAvatar } from '@/features/candidates/components/entry-avatar'
import { DuplicateDialog, RecoveryDialog } from '@/features/candidates/components/entry-dialogs'
import { appliedFieldLabels, fieldSources, liveDuplicateQuery, SCAN_KIND_LABEL, type DuplicateMatch } from '@/features/candidates/entry-signals'
import { candidateEntryErrors, draftSnapshot, EMPTY_CANDIDATE_FORM as EMPTY, restoredCandidateDraft } from '@/features/candidates/entry-model'
import { EntryFormPreview } from '@/features/application-form/components/entry-form-preview'
import { educationPayload, historyPayload, previewFormFromEntry } from '@/features/application-form/entry-bridge'
import { applicationFormHref } from '@/features/application-form/form-model'
import { fileHref } from '@/shared/api/client'
import { useReferenceAgencies } from '@/features/clients/agency-api'
import '@/styles/application-form.css'
import '@/styles/add-candidate-v2.css'
import '@/styles/application-entry.css'

type FormKey = keyof typeof EMPTY
type SavedResult = { ref?: string; name?: string; ownerName: string | null; merged: boolean; at: number }
type LiveCheck = { status: 'idle' | 'checking' | 'none' | 'found' | 'conflict' | 'unavailable'; match?: DuplicateMatch; message?: string }
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

export default function AddCandidatePage() {
  const store = useStore()
  // Admin and manager choose the recruiter; a staff member who adds a candidate keeps it.
  const picksRecruiter = choosesRecruiter(store.me.role)
  const navigate = useNavigate()
  const intake = useDocumentIntake(store.me!.id)
  // The dropdown and card lists come from the database (Agency settings > Form options); the hook falls back to a built-in list only if the request fails.
  const { options: formOptions } = useFormOptions()
  const served = useFormSuggestions()
  // The blank form: empty, except for the nationality the agency chose to pre-fill (none unless an admin sets one).
  const blank = useMemo(() => ({ ...EMPTY, nationality: store.settings.defaultNationality ?? '' }), [store.settings.defaultNationality])
  const [form, setForm] = useState(blank)
  const [saving, setSaving] = useState(false)
  const [bundleSource, setBundleSource] = useState<string | null>(null)
  const [savedCandidateId, setSavedCandidateId] = useState<string | null>(null)
  const [savedResult, setSavedResult] = useState<SavedResult | null>(null)
  const [pendingRecovery, setPendingRecovery] = useState<string | null>(null)
  const [recovering, setRecovering] = useState(false)
  const [uploadErrors, setUploadErrors] = useState<Record<string, string>>({})
  const [saveError, setSaveError] = useState('')
  const [previewing, setPreviewing] = useState(false)
  const [attached, setAttached] = useState(false)

  const [duplicate, setDuplicate] = useState<{ candidate: DuplicateMatch; matchedOn: string[] } | null>(null)
  const [live, setLive] = useState<LiveCheck>({ status: 'idle' })
  const liveTicket = useRef(0)
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

  // Only the keys the draft endpoint knows are sent (the screening details included, under the server names).
  const snapshotDraft = (candidateId = savedCandidateId) => ({ ...draftSnapshot(form, edited.current), savedCandidateId: candidateId })
  const draftRef = useRef(snapshotDraft())
  draftRef.current = snapshotDraft()
  useEffect(() => {
    if (!intake.jobs.length) return
    const timer = setTimeout(() => { void intake.saveDraft(draftRef.current).catch(() => {}) }, 700)
    return () => clearTimeout(timer)
  }, [form, intake.jobs.length, savedCandidateId])

  // Live duplicate check: the same rule the save uses (phone, email, passport number), run once the typing settles.
  const liveKey = JSON.stringify(liveDuplicateQuery(form))
  useEffect(() => {
    const query = liveDuplicateQuery(form)
    const ticket = ++liveTicket.current
    if (!query || savedCandidateId) { setLive({ status: 'idle' }); return }
    setLive((previous) => previous.status === 'found' ? previous : { status: 'checking' })
    const timer = setTimeout(async () => {
      try {
        const result = await api.post<{ duplicate: boolean; candidate?: DuplicateMatch }>('/candidates/check-duplicate', query)
        if (ticket !== liveTicket.current || !store.isCurrentSession()) return
        setLive(result.duplicate && result.candidate ? { status: 'found', match: result.candidate } : { status: 'none' })
      } catch (error) {
        if (ticket !== liveTicket.current || !store.isCurrentSession()) return
        setLive(error instanceof ApiError && error.status === 409 ? { status: 'conflict', message: error.message } : { status: 'unavailable' })
      }
    }, 700)
    return () => clearTimeout(timer)
  }, [liveKey, savedCandidateId])

  /** Originals are durable before reading; choosing a replacement keeps human edits. */
  const choose = (kind: ScanKind) => async (file: File) => {
    if (readController.current || saving || savedCandidateId) return
    await intake.upload(kind, file)
    if (!store.isCurrentSession()) return
    const { [kind]: _, ...remaining } = scansRef.current
    scansRef.current = remaining
    setScans(remaining)
    setForm((previous) => mergeOcrForm(previous, blank, remaining, edited.current))
    setReadErrors(({ [kind]: _, ...rest }) => rest)
    setReadMessage('Your file is saved. You can leave this page and carry on later.')
    await intake.saveDraft(draftRef.current).catch(() => {})
    // Every resume, passport or Aadhaar copy is read as soon as it is saved, so the form fills in while you work and a
    // passport added after the resume fills in too. Values you typed are never replaced (see mergeOcrForm).
    setReadMessage(`Your file is saved. We are reading the ${kind === 'resume' ? 'resume' : kind === 'passport' ? 'passport' : 'Aadhaar copy'} to fill in the form.`)
    void readDocuments(true)
  }

  const chosen = intake.jobs.filter((job) => SCAN_KINDS.some((kind) => job.kind === kind)).map((job) => job.kind)
  // The server accepts a blank name or phone exactly when an upload is attached (intake_group_id is sent).
  const hasDocuments = intake.jobs.length > 0
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
        setForm(blank); edited.current = new Set(); scansRef.current = {}; setScans({})
        setSavedCandidateId(null); setPendingRecovery(null)
        return
      }
      const read = scansFromJobs(recovered.jobs)
      const draft = recovered.draft
      const restored = restoredCandidateDraft(draft, blank)
      edited.current = restored.edited
      scansRef.current = read
      setScans(read)
      setForm(mergeOcrForm(restored.form, blank, read, edited.current))
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
      // A failed autosave must never stop the document from being read; the draft banner already tells the user.
      await intake.saveDraft(draftRef.current).catch(() => {})
      const jobs = await intake.read(controller.signal)
      if (readController.current !== controller || !store.isCurrentSession()) return
      const read = scansFromJobs(jobs)
      const failures: Partial<Record<ScanKind, string>> = {}
      for (const job of jobs) if (job.status === 'failed' && SCAN_KINDS.some((kind) => job.kind === kind)) failures[job.kind as ScanKind] = job.lastError ?? 'We could not read this file. Try again, or type the details yourself.'
      scansRef.current = read; setScans(read); setReadErrors(failures)
      setForm((previous) => mergeOcrForm(previous, blank, read, edited.current))
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
    setForm((prev) => mergeOcrForm(prev, blank, scansRef.current, edited.current))
    setReadMessage('You stopped waiting. We keep reading in the background. Come back later, or type the details yourself.')
  }


  const save = async () => {
    if (saving || reading || intake.uploading || recovering) return
    const next = savedCandidateId ? {} : candidateEntryErrors(form, undefined, hasDocuments)
    setErrors(next)
    if (Object.keys(next).length) {
      setSaveError(VALIDATION_MESSAGE)
      // The form is long; bring the first problem into view.
      requestAnimationFrame(() => document.querySelector('.ac-f[data-invalid]')?.scrollIntoView({ block: 'center', behavior: 'smooth' }))
      return
    }

    setSaving(true)
    setSaveError('')
    try {
      if (savedCandidateId) await uploadDocuments(savedCandidateId)
      else await doSave()
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && typeof error.detail === 'object' && error.detail && 'candidate' in error.detail) {
        const candidate = (error.detail as { candidate: DuplicateMatch }).candidate
        setDuplicate({ candidate, matchedOn: candidate.matchedOn })
      }
      if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'We could not save this candidate. Please try again.')
    } finally {
      setSaving(false)
    }
  }

  const doSave = async () => {
    const result = await api.post<{ duplicate: boolean; candidate?: DuplicateMatch }>('/candidates/check-duplicate', payload())
    if (result.duplicate && result.candidate) {
      setDuplicate({ candidate: result.candidate, matchedOn: result.candidate.matchedOn })
      return
    }
    await commit()
  }

  /** From the live match card: the same decision dialog, but only once the required details are valid (a combine sends the whole form). */
  const reviewMatch = () => {
    if (live.status !== 'found' || !live.match) return
    const next = candidateEntryErrors(form, undefined, hasDocuments)
    setErrors(next)
    if (Object.keys(next).length) { setSaveError(VALIDATION_MESSAGE); return }
    setDuplicate({ candidate: live.match, matchedOn: live.match.matchedOn })
  }

  const payload = () => ({
      name: form.name.trim(),
      phone: form.phone.trim(),
      whatsapp: effectiveWhatsapp(form) || undefined,
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
      // Application form (docx) details.
      father_name: form.fatherName.trim() || undefined,
      marital_status: form.maritalStatus || undefined,
      current_address: form.currentAddress.trim() || undefined,
      experience_india: Number(form.experienceIndia) || 0,
      education: educationPayload(form.education),
      work_history: historyPayload(form.workHistory),
      ...screeningPayload(form),
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
      // An admin or manager may name the recruiter right here; a staff member's own entry is assigned to that staff member (the server decides).
      : await store.addCandidate({ ...payload(), ...(picksRecruiter && form.assignTo ? { assign_to: form.assignTo } : {}) }) as { id: string; ref?: string; name?: string; ownerName?: string | null } | null
    if (!created) return
    const ownerName = created.ownerName ?? null
    if (mergeId) store.notify(`Combined with ${created.ref ?? 'the existing candidate'}${ownerName ? `. This candidate stays with ${ownerName}` : ''}. Empty fields were filled in. No new task was created.`)
    else store.notify(ownerName ? `Saved. ${picksRecruiter ? `Assigned to ${ownerName}` : 'Assigned to you'}. The initial call is due within 48 hours.` : 'Saved. No recruiter is free right now, so a supervisor will assign this candidate.', ownerName ? undefined : 'warn')
    setSavedResult({ ref: created.ref, name: created.name, ownerName, merged: Boolean(mergeId), at: Date.now() })
    setSavedCandidateId(created.id)
    setDuplicate(null)
    await intake.saveDraft(snapshotDraft(created.id)).catch(() => {})
    await uploadDocuments(created.id)
  }

  const uploadDocuments = async (candidateId: string) => {
    await intake.saveDraft(snapshotDraft(candidateId)).catch(() => {})
    const failed: Record<string, string> = await intake.attach(candidateId)
    if (!store.isCurrentSession()) return
    setUploadErrors(failed)
    // Stay on the saved result and offer the application form straight away (and the profile beside it).
    if (!Object.keys(failed).length) { await store.refresh({ keepCurrent: true }).catch(() => {}); setAttached(true) }
  }

  const openSavedProfile = async (candidateId: string) => {
    // Read after uploads so the profile contains the newly saved original files.
    // A failed read leaves this recovery screen mounted with the saved record ID.
    await store.refresh({ keepCurrent: true })
    if (store.isCurrentSession()) navigate(`/candidates/${candidateId}`)
  }

  const combine = async (id: string) => {
    setSaving(true); setSaveError('')
    try { await commit(id) } catch (error) { setSaveError(error instanceof Error ? error.message : 'We could not combine them. Nothing you typed was lost.') } finally { setSaving(false) }
  }

  // The saved result (owner, retry buttons) sits above the form; make sure it is what the user sees.
  useEffect(() => {
    if (savedCandidateId) document.querySelector('.ac-saved')?.scrollIntoView({ block: 'start', behavior: 'smooth' })
  }, [savedCandidateId])

  const busyAny = saving || reading || intake.uploading || recovering
  const unfilled = [!form.name.trim() && 'full name', !form.phone.trim() && 'phone'].filter(Boolean) as string[]
  // With a document nothing is required: whatever it did not give stays blank and the candidate is flagged for review.
  const missing = hasDocuments ? [] : unfilled
  const steps = [
    { label: 'Upload documents', detail: 'Add a resume and the passport. We fill in the form.', done: chosen.length > 0 },
    { label: 'Check the details', detail: hasDocuments ? 'Nothing is required while a document is attached.' : 'Name and mobile number are required.', done: unfilled.length === 0 },
    { label: picksRecruiter ? 'Save and assign' : 'Save candidate', detail: picksRecruiter ? 'A free recruiter gets 48 hours to make the initial call, unless you choose one.' : 'This candidate will be assigned to you. You have 48 hours to make the initial call.', done: Boolean(savedCandidateId) },
  ]
  // The step the recruiter is on: documents first, then the details, then save.
  const currentStep = savedCandidateId || missing.length === 0 ? 2 : chosen.length > 0 || form.name.trim() || form.phone.trim() ? 1 : 0
  const sourceHint = form.source === 'referral' ? 'Referred by' : form.source === 'other' ? 'Tell us where from' : 'More about the source (optional)'
  // Reference agency names are offered as suggestions for a referral; what is typed stays free text.
  const agencyNames = useReferenceAgencies(form.source === 'referral').items?.map((agency) => agency.agencyName) ?? []
  const sourcePlaceholder = form.source === 'referral' ? 'Name of the person who referred them' : form.source === 'other' ? 'Where did this candidate come from?' : form.source === 'walk_in' ? 'Branch or event, e.g. Kochi office' : 'Optional note'
  const failedUploads = Object.keys(uploadErrors).length
  const kindLabel = (kind: ScanKind) => kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'
  /** The row icon follows the uploaded file's type; an empty slot keeps its own icon. */
  const slotIcon = (fallback: typeof FileText, filename?: string) => filename ? (IMAGE_FILE.test(filename) ? ImageIcon : FileText) : fallback
  const jobFile = (kind: string) => {
    const job = intake.jobs.find((item) => item.kind === kind)
    return job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null
  }
  const sources = fieldSources(scans, edited.current)
  const filledCount = Object.keys(sources).length
  const expiryNotice = passportExpiryNotice(form.passportExpiry)
  const photoJob = intake.jobs.find((item) => item.kind === 'photo')
  const photoInput = useRef<HTMLInputElement>(null)
  const addPhoto = async (file: File) => {
    if (saving || reading || savedCandidateId) return
    await intake.upload('photo', file)
    await intake.saveDraft(draftRef.current).catch(() => {})
  }
  // The paper form for what has been entered so far; the same button opens the live form once the candidate is saved.
  const previewForm = previewFormFromEntry({ ...form, whatsapp: effectiveWhatsapp(form) }, {
    documents: intake.jobs.map((job) => ({ kind: job.kind, filename: job.filename, url: job.originalUrl, sizeKb: job.sizeKb })),
    sources: sources as Partial<Record<string, 'resume' | 'passport' | 'aadhaar'>>,
    edited: edited.current as ReadonlySet<string>,
    source: ({ walk_in: 'Walk-in', referral: 'Referral', email: 'Email', whatsapp: 'WhatsApp', other: 'Other' } as Record<string, string>)[form.source],
  })
  const openApplicationForm = () => { if (savedCandidateId) navigate(applicationFormHref(savedCandidateId)) }
  /** The completeness of the paper form for what is typed, read and uploaded so far. */
  const completeness = entryCompleteness(form, {
    resume: intake.jobs.some((job) => job.kind === 'resume'),
    passport: intake.jobs.some((job) => job.kind === 'passport'),
    photo: Boolean(photoJob),
  })
  const documentsAdded = { resume: intake.jobs.some((job) => job.kind === 'resume'), passport: intake.jobs.some((job) => job.kind === 'passport'), photo: Boolean(photoJob) }
  const passportAvailable = Boolean(form.passportNo.trim()) || documentsAdded.passport
  const jump = (anchor: string) => document.getElementById(anchor)?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  /** "Upload passport": bring the slot into view and open its file picker. */
  const promptUpload = (kind: 'passport' | 'resume' | 'photo') => {
    const slot = document.getElementById(`slot-${kind}`)
    slot?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    window.setTimeout(() => slot?.querySelector<HTMLButtonElement>('button:not(:disabled)')?.click(), 350)
  }
  const sectionMissing = (...labels: string[]) => {
    const count = completeness.missing.filter((item) => labels.includes(item.key)).length
    return count ? <span className="ac-sec-count">{count} to add</span> : <span className="ac-sec-count is-done"><Check size={12} strokeWidth={3} aria-hidden />Complete</span>
  }
  const disabledAll = saving || recovering || Boolean(savedCandidateId)
  /** One labelled field of the form, with the "Read from" badge when a document filled it and nobody typed over it. */
  const F = (key: FormKey, label: string, control: ReactElement, o: { cls?: string; required?: boolean; hint?: ReactNode; aux?: ReactNode } = {}) => (
    <EntryField label={label} className={o.cls} required={o.required} error={errors[key]} hint={o.hint} aux={o.aux} source={sources[key]}>{control}</EntryField>
  )
  const text = (key: FormKey, label: string, o: { cls?: string; required?: boolean; placeholder?: string; hint?: ReactNode; type?: string } = {}) =>
    F(key, label, <Input type={o.type} value={form[key]} onChange={(e) => set(key, e.target.value)} placeholder={o.placeholder} autoComplete="off" />, o)
  const years = (key: FormKey, label: string, cls: string) =>
    F(key, label, <Input type="number" min="0" step="1" value={form[key]} onChange={(e) => set(key, e.target.value)} placeholder="Years" />, { cls })
  const choice = (key: FormKey, label: string, category: string, cls: string, empty = 'Not recorded') => (
    F(key, label, <Select value={form[key]} onChange={(e) => set(key, e.target.value)}>
      <option value="">{empty}</option>
      {choicesFor(formOptions, category, form[key]).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
    </Select>, { cls })
  )
  const labels = (category: string) => activeOptions(formOptions, category).map((option) => option.label)
  // Suggestions come from what is already saved (the server's lists); the page's own records stand in until they arrive.
  const countrySuggestions = useMemo(() => served.countries.length ? served.countries : frequentValues([...store.orders.map((order) => order.country), ...store.clients.map((client) => client.country), ...store.companies.map((company) => company.country)]), [served.countries, store.orders, store.clients, store.companies])
  const skillSuggestions = useMemo(() => served.skills.length ? served.skills : frequentValues(store.candidates.flatMap((candidate) => candidate.skills ?? []), 200), [served.skills, store.candidates])
  const companySuggestions = useMemo(() => served.companies.length ? served.companies : frequentValues(store.companies.map((company) => company.name)), [served.companies, store.companies])
  const designationSuggestions = useMemo(() => served.jobPreferences.length ? served.jobPreferences : store.designations.map((designation) => designation.title), [served.jobPreferences, store.designations])
  const staff = store.users.filter((user) => user.role === 'staff' && user.active && !user.onLeave)
  const docStatus = (kind: ScanKind) => {
    const job = intake.jobs.find((item) => item.kind === kind)
    if (!job) return kind === 'aadhaar' ? 'Optional. Read for name, number and address.' : 'Not added yet. You can add it later.'
    if (reading && !scans[kind]) return 'Reading it now…'
    if (hasOcrExtraction(scans[kind])) {
      const count = Object.values(sources).filter((value) => value === kind).length
      return count ? `Read. ${count} ${count === 1 ? 'field' : 'fields'} filled below.` : 'Read. Nothing new to fill in.'
    }
    return 'Saved. We read it to fill in the form.'
  }

  return (
    <>
      <PageHeader
        title="Candidate"
        description="Add a candidate. Upload the documents first and we fill in the application form. Check it, complete what is missing, then save."
        actions={<Button icon={<ClipboardList />} onClick={() => (savedCandidateId ? openApplicationForm() : setPreviewing(true))}>{savedCandidateId ? 'Open application form' : 'Application form'}</Button>}
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
          {bundleSource && <DocumentBundleReview sourceType="manual" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={async result => { intake.reset(); setForm(blank); edited.current = new Set(); scansRef.current = {}; setScans({}); setSavedCandidateId(null); setSavedResult(null); setErrors({}); setReadErrors({}); setUploadErrors({}); setSaveError(''); setReadMessage(`${result.candidateIds.length} candidate profiles saved from the reviewed document pages. This form is ready for a new candidate.`); await Promise.allSettled([store.refresh({ keepCurrent: true }), intake.reload()]) }} />}
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
                  <small>{savedResult.ownerName ? (savedResult.merged ? 'Combining does not change who owns this candidate.' : picksRecruiter ? 'Chosen automatically from the recruiters who are free, or the person you picked.' : 'A candidate you add is assigned to you.') : 'No recruiter is free right now. A supervisor will assign this candidate.'}</small>
                </div>
                <div className="ac-result-tile">
                  <span className="ac-result-label">Initial call</span>
                  <span className="ac-result-owner"><span className="ac-result-icon"><PhoneCall size={16} aria-hidden /></span><strong>{savedResult.merged || !savedResult.ownerName ? 'No new task' : `Due by ${dateTimeLabel(new Date(savedResult.at + SLA_MS))}`}</strong></span>
                  <small>{savedResult.merged ? 'Combining only fills empty fields. It does not create a new task.' : savedResult.ownerName ? 'The recruiter has 48 hours to make the first call.' : 'The 48-hour clock starts once someone is assigned.'}</small>
                </div>
              </div>}
              <p className="ac-muted">{attached && failedUploads === 0 ? 'Everything is attached. Open the application form to check what was read, add a missing document, or go to the profile.' : 'Finish attaching the documents, or open the profile. Anything already attached stays attached.'}</p>
              {failedUploads > 0 && <ul className="ac-failed">{Object.entries(uploadErrors).map(([slot, error]) => <li key={slot}>{error}</li>)}</ul>}
              <div className="ac-actions">
                <Button variant="primary" icon={<ClipboardList />} disabled={saving || recovering} onClick={openApplicationForm}>Open application form</Button>
                {(!attached || failedUploads > 0) && <Button disabled={saving || recovering} onClick={save}>{saving ? 'Please wait…' : failedUploads ? 'Try the failed uploads again' : 'Finish attaching documents'}</Button>}
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

          <EntryProgress completeness={completeness} reading={reading} filledByDocuments={filledCount} next={nextDocument(documentsAdded)} onJump={jump} onUpload={promptUpload} />

          <div className="ac-form-card">
            <fieldset disabled={disabledAll} className="ac-fieldset">
              {/* ------------------------------------------------------------------ Documents first */}
              <FormSection id="candidate-documents" icon={FolderOpen} title="Upload documents" subtitle="Start here. We read the resume and the passport and fill in the form below. Add any of them later; every field can also be typed.">
                <div className="ac-docs">
                  <p className="ac-subhead">We read these to fill in the form</p>
                  <div className="ac-slots">
                    {SCAN_KINDS.map((kind) => {
                      const job = intake.jobs.find((item) => item.kind === kind)
                      const pill = documentPill(job, reading, scans[kind], true)
                      return <div key={kind} id={`slot-${kind}`} className={`ac-slot ${pill ? 'has-pill' : ''}`}>
                        <FileDrop variant="row" optional icon={slotIcon(kind === 'resume' ? FileText : kind === 'passport' ? ShieldCheck : Fingerprint, job?.filename)}
                          label={kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport copy' : 'Aadhaar copy'}
                          value={jobFile(kind)}
                          onFile={choose(kind)} disabled={reading || saving || intake.uploading} busy={reading && !!job} busyLabel="reading…"
                          help={docStatus(kind)} />
                        {pill && <span className={`ac-pill ac-slot-pill is-${pill.tone}`}><i aria-hidden />{pill.label}</span>}
                      </div>
                    })}
                  </div>
                  <p className="ac-subhead">Kept on the profile when you save</p>
                  <div className="ac-slots">
                    {(() => {
                      const job = intake.jobs.find((item) => item.kind === 'photo')
                      const pill = documentPill(job, false, undefined, false)
                      return <div id="slot-photo" className={`ac-slot ${pill ? 'has-pill' : ''}`}>
                        <FileDrop variant="row" optional icon={slotIcon(Camera, job?.filename)} label="Passport-size photo" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                          value={jobFile('photo')}
                          help={job ? 'Shown on the application form.' : 'Not added yet. You can add it later.'}
                          onFile={async (file) => { await intake.upload('photo', file); await intake.saveDraft(draftRef.current).catch(() => {}) }} />
                        {pill && <span className={`ac-pill ac-slot-pill is-${pill.tone}`}><i aria-hidden />{pill.label}</span>}
                      </div>
                    })()}
                    {([
                      { slot: 'education', label: 'Education certificates', icon: GraduationCap },
                      { slot: 'experience', label: 'Experience certificates', icon: Briefcase },
                    ] as const).map(({ slot, label, icon }) => (
                      <div key={slot} className="ac-slot ac-slot-multi">
                        <MultiFileSlot label={label} icon={icon} help="More than one file is fine. They are added to the profile when you save." max={MAX_FILES_PER_KIND}
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
                  <div className="ac-doc-foot">
                    <div className="ac-doc-status" aria-live="polite">
                      {(reading || intake.uploading) && <span className="ac-progress" role="progressbar" aria-label={reading ? 'Reading documents' : 'Saving your file'}><i /></span>}
                      <span>
                        {reading
                          ? <><LoaderCircle size={14} className="ac-spin" aria-hidden /> We keep reading in the background if you leave. Choose Stop waiting to carry on without it.</>
                          : intake.uploading ? <><LoaderCircle size={14} className="ac-spin" aria-hidden /> Saving your file…</>
                          : `${DOCUMENT_HELP} If the passport and the resume disagree, we use the passport for fields you have not changed.`}
                      </span>
                    </div>
                    <div className="ac-doc-buttons">
                      {intake.jobs[0]?.groupId && <Button disabled={reading || saving || intake.uploading} onClick={() => setBundleSource(intake.jobs[0].groupId)}>Check people in documents</Button>}
                      {reading && <Button variant="ghost" onClick={cancelReading}>Stop waiting</Button>}
                      <Button variant="primary" onClick={() => void readDocuments()} disabled={reading || saving || intake.uploading || chosen.length === 0} icon={<Sparkles className="h-4 w-4" />}>
                        {reading ? 'Reading…' : 'Read again'}
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
              </FormSection>

              {/* ------------------------------------------------------------------ Personal information */}
              <FormSection id="candidate-personal" icon={UserRound} title="Personal information" subtitle="Name and mobile number are required unless a document is attached." aside={sectionMissing('name', 'fatherName', 'dob', 'maritalStatus', 'gender', 'nationality', 'languages', 'phone', 'currentAddress')}>
                <div className="ac-personal">
                  <div className="ac-photo-box">
                    <div className="ac-photo-frame">
                      {photoJob ? <img src={fileHref(photoJob.originalUrl)} alt="Passport-size photo" /> : <><Camera size={22} aria-hidden /><span>Passport-size photo</span></>}
                    </div>
                    <div className="ac-photo-actions">
                      <button type="button" className="ac-photo-btn" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)} onClick={() => photoInput.current?.click()}>{photoJob ? 'Replace' : 'Upload photo'}</button>
                      {photoJob && <button type="button" className="ac-photo-btn is-quiet" aria-label="Remove photo" title="Remove photo" disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                        onClick={async () => { await intake.remove(photoJob.id); await intake.saveDraft(draftRef.current).catch(() => {}) }}><Trash2 size={14} /></button>}
                    </div>
                    <input ref={photoInput} type="file" accept="image/jpeg,image/png,image/webp,image/heic,.jpg,.jpeg,.png,.webp,.heic" className="sr-only" tabIndex={-1} aria-label="Choose a passport-size photo"
                      onChange={(event) => { const file = event.currentTarget.files?.[0]; event.currentTarget.value = ''; if (file) void addPhoto(file) }} />
                  </div>
                  <div className="ac-g">
                    {text('name', 'Candidate name', { cls: 's-3', required: true, placeholder: 'As written in the passport' })}
                    {text('fatherName', 'Father’s name', { cls: 's-3' })}
                    {F('dob', 'Date of birth', <DatePicker value={form.dob} onChange={(value) => set('dob', value)} />, { cls: 's-3' })}
                    {choice('maritalStatus', 'Marital status', OPT.maritalStatus, 's-3')}
                    {choice('gender', 'Gender', OPT.gender, 's-3')}
                    {text('nationality', 'Nationality', { cls: 's-3' })}
                    <div className="ac-f s-6" data-from={sources.languages ? '' : undefined}>
                      <TagField label="Language known" value={form.languages} onChange={(value) => set('languages', value)} suggestions={labels(OPT.language)} placeholder="Type a language and press Enter" aux={sources.languages && <ReadBadge kind={sources.languages} />} />
                    </div>
                    {text('currentCity', 'Current location', { cls: 's-6', placeholder: 'City or town you live in now' })}
                    {F('nativePlace', 'Native place', <Input value={effectiveNativePlace(form)} disabled={form.nativeSame === 'yes'} onChange={(e) => set('nativePlace', e.target.value)} autoComplete="off" placeholder="Hometown or village" />,
                      { cls: 's-6', aux: <SameAs label="Same as current" checked={form.nativeSame === 'yes'} onChange={(checked) => set('nativeSame', checked ? 'yes' : '')} /> })}
                    {F('currentAddress', 'Current address', <Textarea rows={2} value={form.currentAddress} onChange={(e) => set('currentAddress', e.target.value)} placeholder="House / street, city, state, PIN" />, { cls: 's-12' })}
                  </div>
                </div>
                {live.status === 'found' && live.match && !savedCandidateId && (
                  <div className="ac-match" role="status">
                    <EntryAvatar name={live.match.name} size="lg" />
                    <div className="ac-match-copy">
                      <p className="ac-match-title">Looks like {live.match.name} is already in the Data Bank</p>
                      <p className="ac-match-sub"><span className="tnum">{live.match.ref}</span> · Same {live.match.matchedOn.join(' and ')} · Recruiter: {live.match.ownerName ?? 'not assigned yet'}</p>
                    </div>
                    <div className="ac-match-actions">
                      <Button size="sm" onClick={() => navigate(`/candidates/${live.match!.id}`)}>Open candidate</Button>
                      <Button size="sm" variant="primary" onClick={reviewMatch} disabled={busyAny}>Review match</Button>
                    </div>
                  </div>
                )}
                {live.status === 'conflict' && !savedCandidateId && (
                  <p className="ac-match-note is-warn" role="status"><AlertTriangle size={14} aria-hidden /><span>{live.message || 'The phone, email or passport belong to different candidates. Sort this out before you save.'}</span></p>
                )}
                {live.status === 'checking' && !savedCandidateId && <p className="ac-match-note" role="status"><LoaderCircle size={14} className="ac-spin" aria-hidden /><span>Checking the Data Bank for this person…</span></p>}
              </FormSection>

              {/* ------------------------------------------------------------------ Contact details */}
              <FormSection id="candidate-contact" icon={Contact} title="Contact details" subtitle="How to reach the candidate and a family member, and when to call.">
                <div className="ac-g">
                  {F('phone', 'Mobile number', <PhoneField value={form.phone} onChange={(value) => set('phone', value)} invalid={Boolean(errors.phone)} />, { cls: 's-4', required: true })}
                  {F('whatsapp', 'WhatsApp number', <PhoneField value={effectiveWhatsapp(form)} disabled={whatsappIsSame(form)} onChange={(value) => set('whatsapp', value)} showHint={false} />,
                    { cls: 's-4', hint: whatsappIsSame(form) ? 'Messages go to the mobile number.' : undefined, aux: <SameAs label="Same as primary" checked={whatsappIsSame(form)} onChange={(checked) => set('whatsappSame', checked ? 'yes' : 'no')} /> })}
                  {F('familyNumber', 'Family member number', <PhoneField value={form.familyNumber} onChange={(value) => set('familyNumber', value)} showHint={false} />, { cls: 's-4', hint: 'A relative who can be reached if the candidate cannot.' })}
                  {text('email', 'E-mail', { cls: 's-6', type: 'email', placeholder: 'candidate@example.com' })}
                  {choice('bestTimeToContact', 'Best time to contact', OPT.bestTime, 's-6', 'No preference noted')}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Passport */}
              <FormSection id="candidate-passport" icon={IdCard} title="Passport details" subtitle="Read from the passport copy. The original passport is collected at stage 7."
                aside={<span className={`ac-pill ${passportAvailable ? 'is-good' : 'is-muted'}`}><i aria-hidden />{passportAvailable ? 'Passport available' : 'No passport yet'}</span>}>
                <div className="ac-g">
                  {text('passportNo', 'Passport number', { cls: 's-3', placeholder: 'M1234567' })}
                  {text('passportPlace', 'Place of issue', { cls: 's-3' })}
                  {F('passportIssue', 'Issue date', <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />, { cls: 's-3' })}
                  {F('passportExpiry', 'Expiry date', <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />, { cls: 's-3', hint: expiryNotice ? `${expiryNotice.text}. Most Gulf countries want six months left.` : undefined })}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Education */}
              <FormSection id="candidate-education" icon={GraduationCap} title="Educational details" subtitle="Course, passed year and institute, newest first." aside={sectionMissing('education')}>
                <EntryRowsEditor kind="education" value={form.education} onChange={(value) => set('education', value)} source={sources.education} suggestions={{ level: labels(OPT.educationLevel), institute: served.institutes }} />
              </FormSection>

              {/* ------------------------------------------------------------------ Work experience */}
              <FormSection id="candidate-experience" icon={BriefcaseBusiness} title="Previous experience" subtitle="Total, in India and abroad (whole years), then each employer." aside={sectionMissing('totalExperience', 'workHistory')}>
                <div className="ac-g">
                  {years('totalExperience', 'Total experience', 's-4')}
                  {years('experienceIndia', 'Experience in India', 's-4')}
                  {years('gulfExperience', 'Experience abroad', 's-4')}
                </div>
                <EntryRowsEditor kind="history" value={form.workHistory} onChange={(value) => set('workHistory', value)} source={sources.workHistory}
                  suggestions={{ company: companySuggestions, designation: designationSuggestions, country: countrySuggestions }} />
              </FormSection>

              {/* ------------------------------------------------------------------ Job preference */}
              <FormSection id="candidate-work" icon={Target} title="Job preference" subtitle="Job titles and skills help us search the Data Bank and match candidates to orders.">
                <div className="ac-g">
                  <div className="ac-f s-6" data-from={sources.designation ? '' : undefined}>
                    <TagField label="Job preference" value={form.designation} onChange={(value) => set('designation', value)} suggestions={designationSuggestions}
                      placeholder="Pick from the list, or type and press Enter" aux={sources.designation && <ReadBadge kind={sources.designation} />} />
                  </div>
                  <div className="ac-f s-6" data-from={sources.skills ? '' : undefined}>
                    <TagField label="Skills" value={form.skills} onChange={(value) => set('skills', value)} suggestions={skillSuggestions} placeholder="e.g. TIG welding, then Enter" aux={sources.skills && <ReadBadge kind={sources.skills} />} />
                  </div>
                  <div className="ac-f s-4">
                    <TagField label="Country looking for" value={form.countryPreference} onChange={(value) => set('countryPreference', value)} suggestions={countrySuggestions} placeholder="Pick a country, or type one" />
                  </div>
                  {choice('jobDurationPreference', 'Job duration preference', OPT.jobDuration, 's-4', 'Not discussed')}
                  {F('expectedSalary', 'Expected salary', <Input type="number" min="0" step="0.01" value={form.expectedSalary} onChange={(e) => set('expectedSalary', e.target.value)} placeholder="Per month" />, { cls: 's-4' })}
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Assessment */}
              <FormSection id="candidate-assessment" icon={Gauge} title="Assessment & preferences" subtitle="From the screening conversation. The recruiter still records the initial call on the profile.">
                <div className="ac-g">
                  {F('serviceChargesStatus', 'Service charges', <Select value={form.serviceChargesStatus} onChange={(e) => set('serviceChargesStatus', e.target.value)}>
                    {choicesFor(formOptions, OPT.serviceCharges, form.serviceChargesStatus).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                  </Select>, { cls: 's-4' })}
                  <EntryField label="Interest level" className="s-12" hint="Tap the chosen card again to clear it.">
                    <InterestCards value={form.interestLevel} options={choicesFor(formOptions, OPT.interestLevel, form.interestLevel)} onChange={(value) => set('interestLevel', value)} />
                  </EntryField>
                </div>
              </FormSection>

              {/* ------------------------------------------------------------------ Source, follow-up, assignment */}
              <FormSection id="candidate-source" icon={Share2} title="Source & follow-up" subtitle="Where the candidate came from, who looks after them and when to follow up. The client, country and position come from the order once the candidate is matched.">
                <div className="ac-g">
                  <EntryField label="Source" className="s-3">
                    <Select value={form.source} onChange={(e) => set('source', e.target.value)}>
                      {choicesFor(formOptions, OPT.source, form.source).map((option) => <option key={option.value} value={option.value}>{option.label}</option>)}
                    </Select>
                  </EntryField>
                  <EntryField label={sourceHint} className="s-3" aux={agencyNames.length > 0 ? <datalist id="reference-agency-names">{[...new Set(agencyNames)].map((name) => <option key={name} value={name} />)}</datalist> : undefined}>
                    <Input value={form.sourceDetail} onChange={(e) => set('sourceDetail', e.target.value)} placeholder={sourcePlaceholder} maxLength={255} list={agencyNames.length ? 'reference-agency-names' : undefined} />
                  </EntryField>
                  <EntryField label="Assigned recruiter" className="s-3" hint={picksRecruiter ? 'Leave on automatic to give it to a free recruiter.' : 'A candidate you add is assigned to you, so you do the work and complete the stages.'}>
                    {picksRecruiter
                      ? <Select value={form.assignTo} onChange={(e) => set('assignTo', e.target.value)}>
                          <option value="">Automatic: a free recruiter</option>
                          {staff.map((user) => <option key={user.id} value={user.id}>{user.name}</option>)}
                        </Select>
                      : <Input value={store.me.name ? `${store.me.name} (you)` : 'You'} readOnly disabled />}
                  </EntryField>
                  <EntryField label="Next follow-up date" className="s-3" hint="Adds a follow-up to the recruiter’s to-do list.">
                    <DatePicker value={form.nextFollowUp} onChange={(value) => set('nextFollowUp', value)} />
                  </EntryField>
                </div>
              </FormSection>
            </fieldset>

            {!savedCandidateId && <footer className="ac-savebar">
              <div className="ac-savebar-copy">
                <span className={`ac-pill ${missing.length ? 'is-warn' : 'is-good'}`}><i aria-hidden />{missing.length ? 'Missing details' : 'Ready'}</span>
                <span>
                  <strong>{missing.length ? `Still needed: ${missing.join(' and ')}.` : unfilled.length ? `Ready to save. No ${unfilled.join(' or ')} yet, so this candidate will be marked for review.` : 'Ready to save.'}</strong>
                  {' '}<span className="ac-savebar-note">{picksRecruiter ? 'We check the Data Bank for a duplicate first, then give the candidate to a free recruiter.' : 'We check the Data Bank for a duplicate first. The candidate is then assigned to you.'}</span>
                </span>
              </div>
              <div className="ac-savebar-actions">
                <Button variant="ghost" disabled={saving} onClick={() => navigate('/data-bank')}>Cancel</Button>
                <Button icon={<ClipboardList />} onClick={() => setPreviewing(true)}>Application form</Button>
                <Button variant="primary" onClick={save} disabled={busyAny} title={`${picksRecruiter ? 'Save and assign' : 'Save candidate'} (Ctrl+Enter)`}>
                  {saving ? 'Saving…' : reading ? 'Reading documents…' : picksRecruiter ? 'Save and assign' : 'Save candidate'}
                </Button>
              </div>
            </footer>}
          </div>

          <IntakeRecovery intake={intake} disabled={busyAny} onRecover={requestRecovery} onSaveDraft={() => intake.saveDraft(snapshotDraft())} />
        </div>
      </div>

      <EntryFormPreview open={previewing} onClose={() => setPreviewing(false)} form={previewForm} savedCandidateId={savedCandidateId}
        onOpenSaved={() => { setPreviewing(false); openApplicationForm() }} />
      <RecoveryDialog open={Boolean(pendingRecovery)} recovering={recovering} hasFiles={intake.jobs.length > 0} intake={intake}
        onStay={() => setPendingRecovery(null)} onOpen={() => { if (pendingRecovery) void recoverIntake(pendingRecovery) }} />
      <DuplicateDialog duplicate={duplicate} form={form} saving={saving} error={duplicate ? saveError : ''}
        onCancel={() => setDuplicate(null)} onOpen={(id) => { setDuplicate(null); navigate(`/candidates/${id}`) }} onMerge={(id) => void combine(id)} />
    </>
  )
}
