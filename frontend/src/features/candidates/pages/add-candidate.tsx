import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { AlertTriangle, ArrowRight, Check, FileText, ScanLine, ShieldCheck, Sparkles, UserRound } from 'lucide-react'
import { useStore } from '@/shared/state/crm-store'
import { api, type OcrFields, type OcrScan } from '@/shared/api/client'
import type { Candidate } from '@/shared/model/types'
import { Badge, PageHeader, Panel, PanelHeader } from '@/shared/ui/display'
import { Button, Field, Input, Select, Textarea } from '@/shared/ui/primitives'
import { FileDrop } from '@/shared/ui/file-drop'
import { DatePicker } from '@/shared/ui/date-picker'
import { Dialog } from '@/shared/ui/overlay'
import { RecordIdentity } from '@/shared/ui/workspace'

const EMPTY = {
  name: '',
  phone: '',
  whatsapp: '',
  email: '',
  dob: '',
  gender: '',
  nationality: 'Indian',
  currentCity: '',
  languages: '',
  passportNo: '',
  passportIssue: '',
  passportExpiry: '',
  passportPlace: '',
  designation: '',
  totalExperience: '',
  gulfExperience: '',
  skills: '',
  expectedSalary: '',
  source: 'walk_in',
}

type FormKey = keyof typeof EMPTY
type Scan = 'resume' | 'passport'

function toFormValues(fields: OcrFields): Partial<Record<FormKey, string>> {
  const out: Partial<Record<FormKey, string>> = {}
  for (const [k, v] of Object.entries(fields)) {
    if (!(k in EMPTY) || v == null) continue
    out[k as FormKey] = Array.isArray(v) ? v.join(', ') : String(v)
  }
  return out
}

export default function AddCandidatePage() {
  const store = useStore()
  const navigate = useNavigate()
  const [form, setForm] = useState(EMPTY)
  // Files are chosen before the candidate exists, so they are held until it does.
  const [pending, setPending] = useState<Record<string, File>>({})
  const [saving, setSaving] = useState(false)

  const hold = (slot: string) => (f: File) => setPending((p) => ({ ...p, [slot]: f }))
  const [duplicate, setDuplicate] = useState<{ candidate: Candidate; matchedOn: string[] } | null>(null)
  const [errors, setErrors] = useState<Record<string, string>>({})

  // The Veris output for each document read, saved on the candidate with the form.
  const [ocr, setOcr] = useState<Partial<Record<Scan, Record<string, unknown>>>>({})
  const [warnings, setWarnings] = useState<Partial<Record<Scan, string[]>>>({})
  const [reading, setReading] = useState(false)

  const set = (k: FormKey, v: string) => {
    setForm((f) => ({ ...f, [k]: v }))
    setErrors((e) => ({ ...e, [k]: '' }))
  }

  /** A replacement file makes the last reading of that document stale until read again. */
  const choose = (kind: Scan) => (file: File) => {
    hold(kind)(file)
    setOcr(({ [kind]: _, ...rest }) => rest)
    setWarnings(({ [kind]: _, ...rest }) => rest)
  }

  const chosen = (['resume', 'passport'] as Scan[]).filter((k) => pending[k])

  /**
   * Reads every chosen document and fills what it can. The passport is laid over the
   * resume, so its name, date of birth, gender and nationality win: the passport is
   * what the visa is issued against.
   */
  const readDocuments = async () => {
    setReading(true)
    const results = await Promise.allSettled(chosen.map((k) => api.ocr(k, pending[k])))
    const read: Partial<Record<Scan, OcrScan>> = {}
    results.forEach((r, i) => {
      const kind = chosen[i]
      if (r.status === 'fulfilled') read[kind] = r.value
      else store.notify(`${pending[kind].name}: ${(r.reason as Error).message}`, 'warn')
    })

    const values = {
      ...toFormValues(read.resume?.fields ?? {}),
      ...toFormValues(read.passport?.fields ?? {}),
    }
    setForm((prev) => ({ ...prev, ...values }))
    setErrors((e) => ({ ...e, ...Object.fromEntries(Object.keys(values).map((k) => [k, ''])) }))
    setOcr(Object.fromEntries(chosen.filter((k) => read[k]).map((k) => [k, read[k]!.raw])))
    setWarnings(Object.fromEntries(chosen.filter((k) => read[k]).map((k) => [k, read[k]!.warnings])))
    setReading(false)
  }

  const scanned = (Object.keys(ocr) as Scan[]).filter((k) => ocr[k])
  const ocrWarnings = scanned.flatMap((k) => warnings[k] ?? [])

  /**
   * The last ten digits, so "+91 98400 11111", "098400 11111" and "9840011111" are
   * recognised as the same number.
   */
  const phoneTail = (v: string) => v.replace(/\D/g, '').slice(-10)

  const findDuplicate = (): { candidate: Candidate; matchedOn: string[] } | null => {
    const tail = phoneTail(form.phone)

    if (form.dob && tail.length === 10) {
      const hit = store.candidates.find(
        (c) => c.dob?.slice(0, 10) === form.dob && phoneTail(c.phone) === tail,
      )
      if (hit) return { candidate: hit, matchedOn: ['date of birth', 'phone number'] }
    }

    // A passport match alone is enough — it is the strongest identity a candidate has,
    // and two records for one passport collide later when the original is collected.
    if (form.passportNo.trim()) {
      const hit = store.candidates.find(
        (c) => c.passportNo?.toUpperCase() === form.passportNo.trim().toUpperCase(),
      )
      if (hit) return { candidate: hit, matchedOn: ['passport number'] }
    }

    return null
  }

  const save = async () => {
    const next: Record<string, string> = {}
    if (!form.name.trim()) next.name = 'A name is needed to save the record.'
    if (!form.phone.trim()) next.phone = 'A phone number is needed — it is how duplicates are caught.'
    setErrors(next)
    if (Object.keys(next).length) return

    setSaving(true)
    try {
      await doSave()
    } finally {
      setSaving(false)
    }
  }

  const doSave = async () => {
    const dup = findDuplicate()
    if (dup) {
      // A duplicate is refused outright — there is no merge path.
      setDuplicate(dup)
      return
    }
    await commit()
  }

  const commit = async () => {
    // The server assigns the reference and the owner — round-robin has to be decided
    // under a lock, not guessed here.
    const created = await store.addCandidate({
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
      designations: form.designation ? [form.designation] : [],
      total_experience: Number(form.totalExperience) || 0,
      gulf_experience: Number(form.gulfExperience) || 0,
      skills: form.skills.split(',').map((s) => s.trim()).filter(Boolean),
      expected_salary: Number(form.expectedSalary) || undefined,
      source: form.source,
      // The server prefers the scanned resume's own text; this covers a hand-filled form.
      resume_text: `${form.name} — ${form.designation}, ${form.totalExperience || 0} years. ${form.skills}`,
      ocr_data: scanned.length ? ocr : undefined,
    })
    if (!created) return

    // Upload after the record exists. A failure here is reported but does not undo the
    // candidate — better a saved candidate missing a scan than a lost entry.
    for (const [slot, file] of Object.entries(pending)) {
      try {
        await api.upload('candidate', created.id, slot, file)
      } catch {
        store.notify(`${file.name} could not be uploaded`, 'warn')
      }
    }
    navigate(`/candidates/${created.id}`)
  }

  return (
    <>
      <PageHeader
        title="Add candidate"
        eyebrow="Grow your talent pool"
        description="A great opportunity starts with a great profile. Upload documents or enter the details below."
        actions={<Badge tone="navy"><UserRound size={14} /> New candidate profile</Badge>}
      />

      <div className="candidate-form-layout">
        <aside className="candidate-form-guide">
          <Panel>
            <span className="workspace-eyebrow">New profile</span>
            <RecordIdentity name={form.name.trim() || 'New candidate'} detail={form.phone || 'Add their details to get started'} />
            <nav aria-label="Candidate form sections">
              {[
                { id: 'candidate-documents', label: 'Documents', detail: 'Resume & passport scans', icon: FileText, complete: chosen.length > 0 },
                { id: 'candidate-basic', label: 'Basic details', detail: 'Identity & contact information', icon: UserRound, complete: Boolean(form.name.trim() && form.phone.trim()) },
                { id: 'candidate-passport', label: 'Passport details', detail: 'Travel document information', icon: ShieldCheck, complete: Boolean(form.passportNo.trim()) },
              ].map(({ id, label, detail, icon: Icon, complete }) => (
                <a href={`#${id}`} key={id} className={complete ? 'is-filled' : ''}>
                  <span>{complete ? <Check size={17} /> : <Icon size={17} />}</span>
                  <span><strong>{label}</strong><small>{detail}</small></span>
                  <ArrowRight size={14} />
                </a>
              ))}
            </nav>
          </Panel>
          <div className="candidate-guide-tip"><ScanLine size={21} /><h2>Less typing, more recruiting</h2><p>Upload a resume and passport, then use Read documents to fill in the details. Review everything before saving.</p></div>
        </aside>
        <div className="candidate-form-sections space-y-4">
          <Panel flush id="candidate-documents" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>01</span>Documents</span>} meta="Upload first. Let document reading do the heavy lifting." />
            <div className="grid gap-4 px-5 py-5 sm:grid-cols-2">
              <FileDrop label="Resume" onFile={choose('resume')} disabled={reading} busy={reading && !!pending.resume} busyLabel="reading…" />
              <FileDrop label="Passport copy" onFile={choose('passport')} disabled={reading} busy={reading && !!pending.passport} busyLabel="reading…" />
            </div>
            <div className="document-read-actions flex items-center gap-3 px-5 pb-5">
              <Button
                onClick={readDocuments}
                disabled={reading || chosen.length === 0}
                icon={<Sparkles className="h-4 w-4" />}
              >
                {reading ? 'Reading…' : 'Read documents'}
              </Button>
              <span className="text-xs text-ink-400">
                {reading
                  ? 'This takes 10–20 seconds.'
                  : 'Where both are read, the passport decides the name and date of birth.'}
              </span>
            </div>
            {scanned.length > 0 && (
              <div className="mx-5 mb-5 rounded-control border border-navy-100 bg-navy-50 px-3 py-2.5 text-xs text-navy-700">
                <p className="flex items-start gap-2">
                  <Sparkles className="mt-px h-3.5 w-3.5 shrink-0" />
                  The OCR filled what it could read from the {scanned.join(' and ')}. Check every
                  field before saving — anything it was unsure of is left blank.
                </p>
                {ocrWarnings.length > 0 && (
                  <ul className="mt-2 space-y-1 pl-5">
                    {ocrWarnings.map((w, i) => (
                      <li key={i} className="flex items-start gap-2 text-amber-800">
                        <AlertTriangle className="mt-px h-3 w-3 shrink-0 text-amber-600" />
                        {w}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </Panel>

          <Panel flush id="candidate-basic" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>02</span>Basic details</span>} meta="The essentials that help your team stay in touch." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <Field label="Full name" required error={errors.name} className="sm:col-span-2">
                <Input value={form.name} onChange={(e) => set('name', e.target.value)} placeholder="As written in the passport" />
              </Field>
              <Field label="Phone" required error={errors.phone}>
                <Input ident value={form.phone} onChange={(e) => set('phone', e.target.value)} placeholder="+91 98400 00000" />
              </Field>
              <Field label="Email">
                <Input type="email" value={form.email} onChange={(e) => set('email', e.target.value)} />
              </Field>
              <Field label="Date of birth">
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
              <Field label="Languages" hint="Separate with commas">
                <Input value={form.languages} onChange={(e) => set('languages', e.target.value)} placeholder="English, Hindi" />
              </Field>
            </div>
          </Panel>

          <Panel flush id="candidate-passport" className="candidate-form-section">
            <PanelHeader title={<span className="form-section-title"><span>03</span>Passport details</span>} meta="Needed before stage 7. Collect it early if you can." />
            <div className="grid gap-4 px-4 py-4 sm:grid-cols-2">
              <Field label="Passport number">
                <Input ident value={form.passportNo} onChange={(e) => set('passportNo', e.target.value)} placeholder="M1234567" />
              </Field>
              <Field label="Place of issue">
                <Input value={form.passportPlace} onChange={(e) => set('passportPlace', e.target.value)} />
              </Field>
              <Field label="Issue date">
                <DatePicker value={form.passportIssue} onChange={(value) => set('passportIssue', value)} />
              </Field>
              <Field label="Expiry date">
                <DatePicker value={form.passportExpiry} onChange={(value) => set('passportExpiry', value)} />
              </Field>
            </div>
          </Panel>

          <div className="candidate-save-bar flex items-center justify-end gap-2 pb-2">
            <span><ShieldCheck size={16} /> Name and phone are required</span>
            <Button variant="ghost" onClick={() => navigate('/data-bank')}>
              Cancel
            </Button>
            <Button variant="primary" onClick={save} disabled={saving || reading}>
              {saving ? 'Saving…' : reading ? 'Reading documents…' : 'Save and assign'}
            </Button>
          </div>
        </div>
      </div>

      <Dialog
        open={!!duplicate}
        onClose={() => setDuplicate(null)}
        title="Duplicate found — this candidate is not saved"
        description={
          duplicate
            ? `The ${duplicate.matchedOn.join(' and ')} already belong to a record in the Data Bank.`
            : undefined
        }
        footer={
          <>
            <Button variant="ghost" onClick={() => setDuplicate(null)}>
              Back to the form
            </Button>
            <Button
              variant="primary"
              onClick={() => {
                const id = duplicate!.candidate.id
                setDuplicate(null)
                navigate(`/candidates/${id}`)
              }}
            >
              Open the existing record
            </Button>
          </>
        }
      >
        {duplicate && (
          <div className="rounded-control border border-rule bg-field/50 p-4">
            <p className="text-sm font-semibold text-ink">{duplicate.candidate.name}</p>
            <p className="mt-0.5 text-micro text-ink-400 ident">
              {duplicate.candidate.phone}
              {duplicate.candidate.passportNo ? ` · ${duplicate.candidate.passportNo}` : ''}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              <Badge>{duplicate.candidate.ref}</Badge>
              <Badge>{duplicate.candidate.designations[0] ?? 'No designation'}</Badge>
              <Badge>Owner: {store.user(duplicate.candidate.ownerId)?.name ?? 'Unassigned'}</Badge>
            </div>
          </div>
        )}
        <p className="mt-3 flex items-start gap-2 text-xs text-ink-500">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-600" />
          Nothing was saved. If this is genuinely a different person, change the detail that
          clashes; otherwise work from the existing record so one candidate does not end up with
          two files.
        </p>
      </Dialog>
    </>
  )
}
