import type { ReactNode } from 'react'
import { Check, ShieldAlert, ShieldCheck, X } from 'lucide-react'
import { Badge } from '@/shared/ui/display'
import { cn } from '@/shared/lib/cn'
import { CardBlock, FactGrid, ProfileCard, type Fact } from './fact-section'
import { DataTree, Disclosure } from './data-tree'
import type { AadhaarOcr, CandidateOcr, MrzCheckDigits, PassportOcr, ResumeOcr } from './ocr-types'
import {
  aadhaarMasker,
  aadhaarNumberView,
  buildTree,
  confidenceLabel,
  countValues,
  extraEntries,
  humanDate,
  humanizeKey,
  normalizeOcr,
  pageList,
  type InspectionView,
  type OcrDocumentEntry,
} from '../ocr-view'

const capitalise = (v?: string | null) => (v ? v.charAt(0).toUpperCase() + v.slice(1) : '')
const text = (v?: string | number | null) => (v === null || v === undefined || v === '' ? '' : String(v))
const ms = (v?: number | null) => (typeof v === 'number' ? `${v.toLocaleString('en-US')} ms` : '')

const ident = (v?: string | null) => (v ? <span className="ident">{v}</span> : null)

/** An ISO date reads "12 Mar 2030"; free text such as "25th May 1976" is tidied the same way. */
const dateText = (v?: string | null) => humanDate(v)

const pill = 'rounded-full px-3 py-1 text-[14px]'

function WarningList({ warnings }: { warnings?: string[] }) {
  const shown = (warnings ?? []).map((w) => w.trim()).filter(Boolean)
  if (shown.length === 0) return null
  return (
    <CardBlock title="Warnings from the reader">
      <ul className="space-y-2">
        {shown.map((w, i) => (
          <li key={i} className="flex items-start gap-2 text-[16px] leading-6 text-amber-700">
            <ShieldAlert className="mt-1 h-4 w-4 shrink-0" />
            <span className="min-w-0 break-words">{w}</span>
          </li>
        ))}
      </ul>
    </CardBlock>
  )
}

function CheckBadge({ label, value }: { label: string; value?: boolean | null }) {
  const tone = value === true ? 'navy' : value === false ? 'stamp' : 'neutral'
  return (
    <Badge tone={tone} className={pill}>
      {value === true ? <Check className="h-3.5 w-3.5" /> : value === false ? <X className="h-3.5 w-3.5" /> : null}
      {label}
      {value === null || value === undefined ? ' · not checked' : value ? ' · valid' : ' · failed'}
    </Badge>
  )
}

const CHECK_LABELS: [keyof MrzCheckDigits, string][] = [
  ['passport_number', 'Passport number'],
  ['date_of_birth', 'Date of birth'],
  ['expiry_date', 'Expiry date'],
  ['personal_number', 'Personal number'],
  ['composite', 'Composite'],
]

const th = 'pb-2 pr-4 text-[14px] font-semibold text-ink-600'

/* ---------------------------------------------------------------------- Passport */

/** The passport details Veris read, as plain facts; nothing about how the reading was done. */
export function PassportReading({ passport }: { passport?: PassportOcr }) {
  if (!passport) return null
  const mrz = passport.mrz ?? undefined

  const facts: Fact[] = [
    ['Passport number', ident(mrz?.passport_number)],
    ['Surname', text(mrz?.surname)],
    ['Given names', text(mrz?.given_names)],
    ['Nationality', text(mrz?.nationality)],
    ['Issuing country', text(mrz?.issuing_country)],
    ['Date of birth', dateText(mrz?.date_of_birth)],
    ['Sex', text(mrz?.sex)],
    ['Date of issue', dateText(mrz?.date_of_issue)],
    ['Date of expiry', dateText(mrz?.expiry_date)],
    ['Personal number', ident(mrz?.personal_number)],
  ]
  if (!facts.some(([, v]) => v)) return null

  return (
    <ProfileCard
      title="Passport"
      action={
        mrz?.all_check_digits_valid ? (
          <Badge tone="navy" className={pill}>
            <ShieldCheck className="h-3.5 w-3.5" />
            MRZ verified
          </Badge>
        ) : undefined
      }
    >
      <FactGrid facts={facts} />
    </ProfileCard>
  )
}

/* ---------------------------------------------------------------------- Aadhaar */

/** Every Aadhaar field Veris returned. The full number is shown only when `canReveal`. */
export function AadhaarReading({ aadhaar, canReveal }: { aadhaar?: AadhaarOcr; canReveal: boolean }) {
  if (!aadhaar) return null
  const a = aadhaar.aadhaar ?? undefined
  const number = aadhaarNumberView(a, canReveal)
  const vid = a?.vid ? (canReveal ? a.vid : aadhaarMasker()([], 'vid', a.vid)) : ''
  const valid = a?.aadhaar_number_valid

  const facts: Fact[] = [
    ['Name', text(a?.name)],
    ['Aadhaar number', ident(number.masked)],
    ['Aadhaar number (full)', ident(number.full)],
    ['Date of birth', dateText(a?.date_of_birth)],
    ['Year of birth', text(a?.year_of_birth)],
    ['Gender', capitalise(a?.gender)],
    ['Mobile number', ident(a?.mobile_number)],
    ['Care of', text(a?.care_of)],
    ['Address', text(a?.address)],
    ['Pincode', ident(a?.pincode)],
    ['VID', ident(vid)],
    ['Enrolment ID', ident(a?.enrollment_id)],
  ]
  if (!facts.some(([, v]) => v)) return null

  return (
    <ProfileCard
      title="Aadhaar"
      meta={canReveal ? undefined : 'The Aadhaar number is masked for your role'}
      action={
        valid === true ? (
          <Badge tone="navy" className={pill}>
            <ShieldCheck className="h-3.5 w-3.5" />
            Number valid
          </Badge>
        ) : valid === false ? (
          <Badge tone="amber" className={pill}>
            <ShieldAlert className="h-3.5 w-3.5" />
            Number failed validation
          </Badge>
        ) : undefined
      }
    >
      <FactGrid facts={facts} />
    </ProfileCard>
  )
}

/* ------------------------------------------------------------- Source documents */

function InspectionTable({ view }: { view: InspectionView }) {
  const meta = [view.sourceFormat && view.sourceFormat.toUpperCase(), view.pageCount != null && `${view.pageCount} page${view.pageCount === 1 ? '' : 's'} received`]
    .filter(Boolean)
    .join(' · ')
  return (
    <div className="mt-3 rounded-xl border border-rule-soft">
      <Disclosure
        className="px-4"
        summary="Local page classification"
        meta={view.complete === false ? 'Needs review' : meta || undefined}
      >
        <div className="space-y-4 pb-4">
          {view.groups.length > 0 && (
            <dl className="grid gap-x-6 gap-y-3 sm:grid-cols-2">
              {view.groups.map((g) => (
                <div key={g.label}>
                  <dt className="text-[15px] text-ink-600">{g.label}</dt>
                  <dd className="text-[16px] font-semibold text-ink-700 tnum">Page {g.pages}</dd>
                </div>
              ))}
            </dl>
          )}
          {view.rows.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full min-w-[30rem] text-left text-[16px]">
                <thead>
                  <tr>
                    <th className={th}>Page</th>
                    <th className={th}>Read</th>
                    <th className={th}>Identified as</th>
                    <th className={th}>Method</th>
                    <th className={th}>Confidence</th>
                    <th className={cn(th, 'pr-0')}>Note</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-rule-soft">
                  {view.rows.map((r) => (
                    <tr key={r.page} className="align-top">
                      <td className="py-2 pr-4 font-medium text-ink-700 tnum">{r.page}</td>
                      <td className={cn('py-2 pr-4', r.status === 'unread' ? 'text-amber-700' : 'text-ink-600')}>{capitalise(r.status)}</td>
                      <td className={cn('py-2 pr-4', r.type === 'Unknown' ? 'text-amber-700' : 'text-ink-600')}>{r.type}</td>
                      <td className="py-2 pr-4 text-ink-600">{r.method}</td>
                      <td className="py-2 pr-4 text-ink-600 tnum">{r.confidence}</td>
                      <td className="break-words py-2 text-ink-600">{r.reason}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {view.warnings.length > 0 && (
            <ul className="space-y-1.5">
              {view.warnings.map((w, i) => (
                <li key={i} className="flex items-start gap-2 text-[16px] text-amber-700">
                  <ShieldAlert className="mt-1 h-4 w-4 shrink-0" />
                  <span className="min-w-0 break-words">{w}</span>
                </li>
              ))}
            </ul>
          )}
        </div>
      </Disclosure>
    </div>
  )
}

/** Masks Aadhaar identifiers in page text unless the viewer may see them. */
function pageText(entry: OcrDocumentEntry, raw: string, canReveal: boolean) {
  return entry.kind === 'aadhaar' && !canReveal ? aadhaarMasker()([], 'text', raw) : raw
}

const CHANNEL_LABEL: Record<string, string> = { email: 'Email', whatsapp: 'WhatsApp', walk_in: 'Walk-in', upload: 'Upload' }

function DocumentSource({ entry, canReveal }: { entry: OcrDocumentEntry; canReveal: boolean }) {
  const pages = entry.pagesView
  const inspection = entry.inspection
  if (pages.length === 0 && !inspection) return null
  const readFrom = pageList(pages.map((p) => p.page))
  const { source } = entry
  const received = [
    source.fileName,
    source.channel && `via ${CHANNEL_LABEL[source.channel] ?? humanizeKey(source.channel)}`,
    source.selectedPages.length > 0 && `pages ${pageList(source.selectedPages)} sent to Veris`,
  ].filter(Boolean)
  return (
    <li className="py-5 first:pt-4 last:pb-1">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <p className="text-[17px] font-semibold text-ink-700">{entry.label}</p>
        <p className="text-[15px] text-ink-600 tnum">
          {pages.length > 0
            ? `Read from page${pages.length === 1 ? '' : 's'} ${readFrom} of the received file`
            : 'No page text was stored'}
        </p>
      </div>
      {received.length > 0 && <p className="mt-1 break-words text-[15px] text-ink-500">{received.join(' · ')}</p>}
      {pages.length > 0 && (
        <div className="mt-3 divide-y divide-rule-soft rounded-xl border border-rule-soft px-4">
          {pages.map((p) => (
            <Disclosure
              key={`${p.page}-${p.sent ?? ''}`}
              summary={`Page ${p.page}`}
              meta={[
                p.language && `Language ${p.language}`,
                p.confidence && `Confidence ${p.confidence}`,
                p.sent != null && `Sent as page ${p.sent}`,
              ]
                .filter(Boolean)
                .join(' · ')}
            >
              {p.text.trim() ? (
                <pre className="mb-3 max-h-[28rem] overflow-auto whitespace-pre-wrap break-words rounded-xl bg-field px-4 py-3 font-sans text-[16px] leading-relaxed text-ink-700">
                  {pageText(entry, p.text, canReveal)}
                </pre>
              ) : (
                <p className="mb-3 text-[16px] text-ink-500">This page returned no text.</p>
              )}
            </Disclosure>
          ))}
        </div>
      )}
      {inspection && <InspectionTable view={inspection} />}
    </li>
  )
}

/** The pages each stored document was read from, the OCR text of every page and the local page classification. */
export function SourceDocuments({ ocr, canReveal }: { ocr: CandidateOcr; canReveal: boolean }) {
  const entries = normalizeOcr(ocr).filter((e) => e.pagesView.length > 0 || e.inspection)
  if (entries.length === 0) return null
  return (
    <ProfileCard
      title="Source documents"
      meta="Pages read from the received files, with the OCR text of each page and how the pages were classified"
    >
      <ul className="divide-y divide-rule-soft">
        {entries.map((e) => (
          <DocumentSource key={e.id} entry={e} canReveal={canReveal} />
        ))}
      </ul>
    </ProfileCard>
  )
}

/* -------------------------------------------------------------- Complete data */

function treeOptionsFor(kind: string, canReveal: boolean) {
  return kind === 'aadhaar' && !canReveal ? { transform: aadhaarMasker() } : undefined
}

/** Every stored value of every document, so a field with no dedicated section is still visible. */
export function CompleteData({ ocr, canReveal }: { ocr: CandidateOcr; canReveal: boolean }) {
  const documents = normalizeOcr(ocr).map((e) => ({
    id: e.id,
    label: e.label,
    payload: e.treeValue,
    options: treeOptionsFor(e.kind, canReveal),
  }))
  const extras = extraEntries(ocr).map((e) => ({ id: e.id, label: e.label, payload: e.payload, options: undefined }))
  const all = [...documents, ...extras]
    .map((d) => ({ ...d, count: countValues(buildTree(d.payload, d.options)) }))
    .filter((d) => d.count > 0)
  if (all.length === 0) return null
  return (
    <ProfileCard
      title="Complete extracted data"
      meta="Every value stored from the OCR, field by field. Only the page markup is left out."
    >
      <div className="divide-y divide-rule-soft">
        {all.map((d) => (
          <Disclosure key={d.id} summary={d.label} meta={`${d.count} value${d.count === 1 ? '' : 's'}`}>
            <div className="pb-4 pt-1">
              <DataTree value={d.payload} options={d.options} />
            </div>
          </Disclosure>
        ))}
      </div>
    </ProfileCard>
  )
}
