import { useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, Briefcase, Camera, Check, FileText, Fingerprint, GraduationCap, ShieldCheck, Sparkles } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, ApiError, type OcrScan } from '@/shared/api/client'
import { Badge, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { DOCUMENT_HELP } from '@/shared/lib/document-files'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { hasOcrExtraction, inspectedPageCount, mergeOcrForm, ocrSavePayload, scanWarnings, type DocumentScans, type ScanKind } from '@/features/candidates/ocr-form'
import { normalizedSkills } from '@/features/candidates/candidate-skills'
import { scansFromJobs, useDocumentIntake } from '@/features/candidates/use-document-intake'
import { DocumentBundleReview } from '@/features/candidates/components/document-bundle-review'
import { IntakeRecovery } from '@/features/candidates/components/intake-recovery'
import { candidateEntryErrors, EMPTY_CANDIDATE_FORM as EMPTY, restoredCandidateDraft } from '@/features/candidates/entry-model'

type FormKey = keyof typeof EMPTY
type Duplicate = { id: string; ref: string; name: string; phone: string; ownerId: string | null; ownerName: string | null; matchedOn: string[] }
const SCAN_KINDS: ScanKind[] = ['resume', 'passport', 'aadhaar']

function DocumentReadReport({ kind, scan }: { kind: ScanKind; scan: OcrScan }) {
  const label = kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'
  const inspection = scan.inspection
  const extracted = hasOcrExtraction(scan)
  const warnings = scanWarnings(scan)
  const selected = inspection?.selected_pages
    ?? (kind === 'resume' ? inspection?.resume_pages : kind === 'passport' ? inspection?.passport_pages : inspection?.aadhaar_pages)
  const list = (pages: number[] | undefined) => pages?.length ? pages.join(', ') : 'None'
  const title = scan.extraction_status === 'no_resume'
    ? 'No resume identified'
    : !extracted ? 'Needs review' : 'Details read'

  return (
    <section
      aria-label={`${label} reading result`}
      className={`rounded-control border px-4 py-3 text-[13px] ${extracted ? 'border-navy-100 bg-navy-50 text-navy-700' : 'border-amber-200 bg-amber-50 text-amber-900'}`}
    >
      <p><strong className="font-semibold">{label}: {title}.</strong>{' '}
        {extracted
          ? 'Review the suggested details below before saving. Fields you edited are kept.'
          : 'No details were applied. Try a clearer file or enter the details manually.'}
      </p>
      {warnings.length > 0 && (
        <ul className="mt-2 space-y-1">
          {warnings.map((warning, index) => (
            <li key={index} className="flex items-start gap-2 text-amber-900">
              <AlertTriangle aria-hidden className="mt-px h-3.5 w-3.5 shrink-0 text-amber-700" />
              <span>{warning}</span>
            </li>
          ))}
        </ul>
      )}
      {inspection && (
        <details className="mt-2">
          <summary className="cursor-pointer font-medium">Page details</summary>
          <dl className="mt-2 grid gap-x-4 gap-y-2 sm:grid-cols-2">
            <div><dt className="font-medium">Pages inspected</dt><dd>{inspectedPageCount(inspection)} of {inspection.page_count}</dd></div>
            <div><dt className="font-medium">Pages selected for {kind} extraction</dt><dd className="break-words">{list(selected)}</dd></div>
            <div><dt className="font-medium">Resume pages identified</dt><dd className="break-words">{list(inspection.resume_pages)}</dd></div>
            <div><dt className="font-medium">Unread pages</dt><dd className="break-words">{list(inspection.unread_pages)}</dd></div>
            <div><dt className="font-medium">Uncertain page types</dt><dd className="break-words">{list(inspection.unknown_pages)}</dd></div>
          </dl>
          <p className="mt-2">Page numbers refer to the original document. Inspected pages can still be unread or uncertain.</p>
          {inspection.pages.length > 0 && (
            <ol className="mt-2 max-h-64 space-y-2 overflow-y-auto pr-2">
              {inspection.pages.map((page) => (
                <li key={page.page_number} className="rounded border border-current/10 bg-white/60 px-2 py-1.5">
                  <span className="font-medium">Page {page.page_number}</span>
                  {' — '}{page.status}; {page.document_type === 'unknown' ? 'uncertain type' : page.document_type}
                  {page.reason && <p className="mt-1">{page.reason}</p>}
                </li>
              ))}
            </ol>
          )}
        </details>
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
    await intake.upload(kind, file)
    if (!store.isCurrentSession()) return
    const { [kind]: _, ...remaining } = scansRef.current
    scansRef.current = remaining
    setScans(remaining)
    setForm((previous) => mergeOcrForm(previous, EMPTY, remaining, edited.current))
    setReadErrors(({ [kind]: _, ...rest }) => rest)
    setReadMessage('Original saved. You can leave this page and resume this intake later.')
    await intake.saveDraft(draftRef.current).catch(() => {})
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
      setReadMessage('Saved originals, reading progress and your form draft have been restored.')
    } catch { /* The intake panel keeps the recovery error visible. */ }
    finally { setRecovering(false) }
  }

  const requestRecovery = async (groupId: string) => {
    if (edited.current.size || intake.jobs.length) setPendingRecovery(groupId)
    else await recoverIntake(groupId)
  }

  const readDocuments = async () => {
    if (readController.current || saving || savedCandidateId || intake.uploading || chosen.length === 0) return
    const controller = new AbortController()
    readController.current = controller
    setReading(true); setReadErrors({}); setReadMessage('')
    try {
      await intake.saveDraft(snapshotDraft())
      const jobs = await intake.read(controller.signal)
      if (readController.current !== controller || !store.isCurrentSession()) return
      const read = scansFromJobs(jobs)
      const failures: Partial<Record<ScanKind, string>> = {}
      for (const job of jobs) if (job.status === 'failed' && SCAN_KINDS.some((kind) => job.kind === kind)) failures[job.kind as ScanKind] = job.lastError ?? 'Reading failed. Retry the saved original or enter details manually.'
      scansRef.current = read; setScans(read); setReadErrors(failures)
      setForm((previous) => mergeOcrForm(previous, EMPTY, read, edited.current))
      setErrors((previous) => Object.fromEntries(Object.entries(previous).filter(([key]) => edited.current.has(key as FormKey))))
    } catch (error) {
      if (readController.current === controller && store.isCurrentSession()) setReadMessage(error instanceof Error ? error.message : 'Reading could not finish. Your original and progress are saved.')
    } finally {
      if (readController.current === controller) { readController.current = null; setReading(false) }
    }
  }

  const cancelReading = () => {
    readController.current?.abort()
    readController.current = null
    setReading(false)
    setForm((prev) => mergeOcrForm(prev, EMPTY, scansRef.current, edited.current))
    setReadMessage('Stopped waiting. Your saved reading continues in the background; resume it later or enter details manually.')
  }


  const save = async () => {
    if (saving || reading || intake.uploading || recovering) return
    const next = savedCandidateId ? {} : candidateEntryErrors(form)
    setErrors(next)
    if (Object.keys(next).length) { setSaveError('Review the highlighted fields before saving. Your entry has been kept.'); return }

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
      if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'The candidate could not be saved. Please try again.')
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
      // The server prefers the scanned resume's own text; this covers a hand-filled form.
      resume_text: `${form.name} — ${form.designation}, ${form.totalExperience || 0} years. ${form.skills}`,
      // Verbatim Veris results plus their provenance under `_sources`.
      ocr_data: ocrSavePayload(scans),
      intake_group_id: intake.jobs[0]?.groupId,
    })

  const commit = async (mergeId?: string) => {
    const created = mergeId ? await api.post<{ id: string }>(`/candidates/${mergeId}/merge`, payload()) : await store.addCandidate(payload())
    if (!created) return
    if (!mergeId && 'ownerName' in created) store.notify(created.ownerName ? `Saved and assigned to ${created.ownerName}. Initial call due within 48 hours.` : 'Saved. No recruiter is eligible right now, so a supervisor will assign this candidate.', created.ownerName ? undefined : 'warn')
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

  const busyAny = saving || reading || intake.uploading || recovering
  const steps = [
    { label: 'Candidate details', detail: 'Name and phone are required.', done: Boolean(form.name.trim() && form.phone.trim()) },
    { label: 'Documents (optional)', detail: 'Add a resume, passport or Aadhaar to pre-fill the form.', done: chosen.length > 0 },
    { label: 'Save and assign', detail: 'An available recruiter gets a 48-hour initial call.', done: Boolean(savedCandidateId) },
  ]

  return (
    <>
      <PageHeader
        title="Add candidate"
        eyebrow="Candidate intake"
        description="Enter the candidate’s details, optionally add documents to pre-fill them, then save and assign their initial call."
      />

      <div className="candidate-entry">
        <ol className="candidate-steps" aria-label="Candidate entry steps">
          {steps.map((step, index) => (
            <li key={step.label} className={step.done ? 'is-done' : ''}>
              <span aria-hidden>{step.done ? <Check size={15} /> : index + 1}</span>
              <div><strong>{step.label}</strong><small>{step.detail}</small></div>
            </li>
          ))}
        </ol>

        <div className="candidate-form-sections space-y-4">
          {bundleSource && <DocumentBundleReview sourceType="manual" sourceId={bundleSource} onClose={() => setBundleSource(null)} onCommitted={async result => { intake.reset(); setForm(EMPTY); edited.current = new Set(); scansRef.current = {}; setScans({}); setSavedCandidateId(null); setErrors({}); setReadErrors({}); setUploadErrors({}); setSaveError(''); setReadMessage(`${result.candidateIds.length} candidate profiles saved from the reviewed document pages. This form is ready for a new candidate.`); await Promise.allSettled([store.refresh({ keepCurrent: true }), intake.reload()]) }} />}
          <IntakeRecovery intake={intake} disabled={busyAny} onRecover={requestRecovery} onSaveDraft={() => intake.saveDraft(snapshotDraft())} />
          {saveError && <p role="alert" className="rounded-control border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">{saveError}</p>}
          {savedCandidateId && <Panel>
            <div role="alert">
              <h2 className="font-semibold">{Object.keys(uploadErrors).length ? 'Candidate saved. Some documents still need attaching.' : 'Candidate already saved'}</h2>
              <p className="mt-1 text-sm text-ink-500">Finish attaching the saved originals or open the profile. Continuing uses the same candidate and keeps successful uploads.</p>
              <ul className="my-3 space-y-1 text-sm text-amber-900">{Object.entries(uploadErrors).map(([slot, error]) => <li key={slot}>{error}</li>)}</ul>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="primary" disabled={saving || recovering} onClick={save}>{saving ? 'Please wait…' : Object.keys(uploadErrors).length ? 'Retry failed uploads' : 'Finish saved intake'}</Button>
              <Button disabled={saving || recovering} onClick={async () => {
                setSaving(true)
                try { await openSavedProfile(savedCandidateId) }
                catch (error) { if (store.isCurrentSession()) setSaveError(error instanceof Error ? error.message : 'The profile could not refresh. Please retry.') }
                finally { setSaving(false) }
              }}>Open saved profile</Button>
            </div>
            {Object.keys(uploadErrors).length > 0 && <p className="mt-2 text-xs text-ink-500">Your originals remain in Saved document intake. Resume this intake to retry attaching them.</p>}
          </Panel>}
          <fieldset disabled={saving || recovering || Boolean(savedCandidateId)} className="candidate-entry-grid">
          <div className="candidate-form-col">
          <Panel flush id="candidate-basic" className="candidate-form-section">
            <PanelHeader title="Candidate details" meta="Name and phone are required." />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Full name" required error={errors.name}>
                <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="As written in the passport" />
              </Field>
              <Field label="Email">
                <Input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="candidate@example.com" />
              </Field>
              <Field label="Phone" required error={errors.phone}>
                <Input ident value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+91 98400 00000" />
              </Field>
              <Field label="WhatsApp number">
                <Input ident value={form.whatsapp} onChange={(e) => set('whatsapp', e.target.value)} />
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
              <Field label="Languages" hint="Separate with commas" className="sm:col-span-full">
                <Input value={form.languages} onChange={(e) => set('languages', e.target.value)} placeholder="English, Hindi" />
              </Field>
            </div>
          </Panel>

          <Panel flush id="candidate-passport" className="candidate-form-section">
            <PanelHeader title="Passport details" meta="Original passport collection is recorded separately at stage 7." />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Passport number">
                <Input ident value={form.passportNo} onChange={(e) => set('passportNo', e.target.value)} placeholder="M1234567" />
              </Field>
              <Field label="Place of issue">
                <Input value={form.passportPlace} onChange={(e) => set('passportPlace', e.target.value)} />
              </Field>
              <Field label="Issue date">
                <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />
              </Field>
              <Field label="Expiry date" error={errors.passportExpiry}>
                <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />
              </Field>
            </div>
          </Panel>

          <Panel flush id="candidate-work" className="candidate-form-section">
            <PanelHeader title="Work profile & source" meta="Skills feed the Data Bank and order matching." />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2 xl:grid-cols-3">
              <Field label="Preferred designations" hint="Separate multiple titles with commas." className="sm:col-span-full">
                <Input list="candidate-designations" value={form.designation} onChange={(e) => set('designation', e.target.value)} placeholder="Welder, Fabricator" />
                <datalist id="candidate-designations">{store.designations.map((designation) => <option key={designation.id} value={designation.title} />)}</datalist>
              </Field>
              <Field label="Total experience (years)" error={errors.totalExperience}>
                <Input type="number" min="0" step="1" value={form.totalExperience} onChange={(e) => set('totalExperience', e.target.value)} />
              </Field>
              <Field label="Gulf / overseas experience (years)" error={errors.gulfExperience}>
                <Input type="number" min="0" step="1" value={form.gulfExperience} onChange={(e) => set('gulfExperience', e.target.value)} />
              </Field>
              <Field label="Skills" hint="Separate with commas or new lines." className="sm:col-span-full">
                <Textarea value={form.skills} onChange={(e) => set('skills', e.target.value)} placeholder="TIG welding, Pipe fitting, Blueprint reading" />
              </Field>
              <Field label="Expected salary" error={errors.expectedSalary}>
                <Input type="number" min="0" step="0.01" value={form.expectedSalary} onChange={(e) => set('expectedSalary', e.target.value)} />
              </Field>
              <Field label="Source">
                <Select value={form.source} onChange={(e) => set('source', e.target.value)}>
                  <option value="walk_in">Walk-in</option><option value="referral">Referral</option><option value="email">Email</option><option value="whatsapp">WhatsApp</option><option value="other">Other</option>
                </Select>
              </Field>
            </div>
          </Panel>
          </div>
          <aside className="candidate-docs-col" aria-label="Candidate documents">
          <Panel flush id="candidate-documents" className="candidate-form-section">
            <PanelHeader title="Documents" meta={`Optional. Choose Read documents to pre-fill the form; your own edits are kept. ${DOCUMENT_HELP}`} />
            <div className="ui-file-rows">
              {SCAN_KINDS.map((kind) => {
                const job = intake.jobs.find((item) => item.kind === kind)
                return <FileDrop key={kind} variant="row" optional icon={kind === 'resume' ? FileText : kind === 'passport' ? ShieldCheck : Fingerprint}
                  label={kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport copy' : 'Aadhaar copy'}
                  value={job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null}
                  onFile={choose(kind)} disabled={reading || saving || intake.uploading} busy={reading && !!job} busyLabel="reading…"
                  help="Saved now; used by Read documents." />
              })}
              {([
                { slot: 'photo', label: 'Photo', icon: Camera },
                { slot: 'education', label: 'Education certificates', icon: GraduationCap },
                { slot: 'experience', label: 'Experience certificates', icon: Briefcase },
              ] as const).map(({ slot, label, icon }) => {
                const job = intake.jobs.find((item) => item.kind === slot)
                return <FileDrop key={slot} variant="row" optional icon={icon} label={label} disabled={saving || intake.uploading || reading || Boolean(savedCandidateId)}
                  value={job ? { id: job.id, name: job.filename, sizeKb: job.sizeKb, url: job.originalUrl } : null}
                  help="Attached when you save."
                  onFile={async (file) => { await intake.upload(slot, file); await intake.saveDraft(draftRef.current).catch(() => {}) }} />
              })}
            </div>
            <div className="document-read-actions flex flex-wrap items-center gap-3 border-t border-rule-soft px-5 py-4">
              {intake.jobs[0]?.groupId && <Button disabled={reading || saving || intake.uploading} onClick={() => setBundleSource(intake.jobs[0].groupId)}>Review people in documents</Button>}
              <Button
                variant="primary"
                onClick={readDocuments}
                disabled={reading || saving || intake.uploading || chosen.length === 0}
                icon={<Sparkles className="h-4 w-4" />}
              >
                {reading ? 'Reading…' : 'Read documents'}
              </Button>
              {reading && <Button variant="ghost" onClick={cancelReading}>Continue manually</Button>}
              <span className="text-xs text-ink-400">
                {reading
                  ? 'Reading continues in the background if you leave. Choose Continue manually to stop waiting.'
                  : 'For fields you have not edited, passport details take priority.'}
              </span>
            </div>
            {(readMessage || SCAN_KINDS.some((kind) => readErrors[kind] || scans[kind])) && <div aria-live="polite" aria-atomic="false" className="mx-5 mb-5 space-y-3">
              {readMessage && <p className="text-xs text-ink-500">{readMessage}</p>}
              {SCAN_KINDS.map((kind) => (
                readErrors[kind] ? (
                  <p key={kind} role="alert" className="rounded-control border border-amber-200 bg-amber-50 px-3 py-2.5 text-xs text-amber-900">
                    <strong>{kind === 'resume' ? 'Resume' : kind === 'passport' ? 'Passport' : 'Aadhaar'}:</strong> {readErrors[kind]} You can enter the details and save manually.
                  </p>
                ) : scans[kind] ? <DocumentReadReport key={kind} kind={kind} scan={scans[kind]!} /> : null
              ))}
            </div>}
          </Panel>
          </aside>
          </fieldset>

          {!savedCandidateId && <div className="candidate-save-bar">
            <span><ShieldCheck size={16} aria-hidden /> Saving checks for duplicates, then assigns an available recruiter. If none is eligible, a supervisor assigns it.</span>
            <div className="candidate-save-actions">
              <Button variant="ghost" disabled={saving} onClick={() => navigate('/data-bank')}>
                Cancel
              </Button>
              <Button variant="primary" onClick={save} disabled={busyAny}>
                {saving ? 'Saving…' : reading ? 'Reading documents…' : 'Save and assign'}
              </Button>
            </div>
          </div>}
        </div>
      </div>

      <Dialog open={Boolean(pendingRecovery)} onClose={() => { if (!recovering) setPendingRecovery(null) }} title="Open a different intake?"
        description={intake.jobs.length ? 'Your current form and originals will be saved in their own intake. The selected intake will open with its own details.' : 'The current form has no uploaded documents and is not backed up. Opening this intake replaces those unsaved details.'}
        footer={<><Button disabled={recovering} onClick={() => setPendingRecovery(null)}>Keep current entry</Button><Button variant="primary" disabled={recovering} onClick={() => { if (pendingRecovery) void recoverIntake(pendingRecovery) }}>{recovering ? 'Opening…' : 'Open saved intake'}</Button></>}>
        <p className="text-sm text-ink-500">Details from different candidates stay separate. Review the restored name and contact information before saving.</p>
        {intake.draftError && <p role="alert" className="mt-3 text-sm text-stamp-600">{intake.draftError}</p>}
      </Dialog>

      <Dialog
        open={!!duplicate}
        onClose={() => { if (!saving) setDuplicate(null) }}
        title="Existing candidate found"
        description={
          duplicate
            ? `The ${duplicate.matchedOn.join(' and ')} already belong to a record in the Data Bank.`
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" disabled={saving} onClick={() => setDuplicate(null)}>
              Cancel merge
            </Button>
            <Button
              disabled={saving}
              onClick={() => {
                const id = duplicate!.candidate.id
                setDuplicate(null)
                navigate(`/candidates/${id}`)
              }}
            >
              Open the existing record
            </Button>
            <Button variant="primary" disabled={saving || (!!duplicate && !store.can('work_any_candidate') && duplicate.candidate.ownerId !== store.me.id)} onClick={async () => {
              if (!duplicate) return
              setSaving(true); setSaveError('')
              try { await commit(duplicate.candidate.id) } catch (error) { setSaveError(error instanceof Error ? error.message : 'Merge failed. Your entry has been kept.') } finally { setSaving(false) }
            }}>{saving ? 'Merging…' : 'Merge into existing'}</Button>
          </>
        }
      >
        {duplicate && (
          <div className="rounded-control border border-rule bg-field/50 p-4">
            <p className="text-sm font-semibold text-ink">{duplicate.candidate.name}</p>
            <p className="mt-0.5 text-micro text-ink-400 ident">
              {duplicate.candidate.phone}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge>{duplicate.candidate.ref}</Badge>
              <Badge>Owner: {duplicate.candidate.ownerName ?? 'Unassigned'}</Badge>
            </div>
          </div>
        )}
        <p className="mt-3 flex items-start gap-2 text-xs text-ink-500">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
          Merging fills empty profile fields and adds your documents as new versions. Existing details, ownership and workflow stay in place. Only the assigned recruiter or a supervisor can merge.
        </p>
        {saveError && <p role="alert" className="mt-3 text-sm text-stamp-600">{saveError}</p>}
      </Dialog>
    </>
  )
}
