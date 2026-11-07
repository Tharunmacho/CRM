/**
 * The "Fill details" view of the application form, as plain data: which section holds which field, how a section's fields
 * are laid out on the 12-column grid, how many items a card still needs, where a value came from, and the small
 * read/write helpers over the draft.
 *
 * Nothing here touches React, so it is unit-tested directly (tests/application-form-fill.test.mjs). Labels, "required"
 * and the field keys all come from form-model.ts: the paper sheet and this view read the same definitions, so a field
 * added to the form appears in both, and the test fails if one is left out of a card.
 */
import {
  EDUCATION_FORM_COLUMNS,
  EXPERIENCE_COLUMNS,
  EXPERIENCE_FIELDS,
  PASSPORT_FIELDS,
  PERSONAL_FIELDS,
  isBlank,
  isBlankRow,
  joinInstitution,
  provenanceOf,
  splitInstitution,
  valueAt,
  type FieldDef,
} from '../form-model'
import { passportExpiryNotice } from '@/features/candidates/passport-expiry'
import type { ApplicationFormData, EducationRow, ExperienceRow, FormDraft, MissingItem, SourceDocument } from '../types'

/* ------------------------------------------------------------------ Fields */

export type FillControl = 'text' | 'date' | 'select' | 'phone' | 'textarea' | 'years' | 'languages'

export interface FillField {
  /** The dotted path, identical to `MissingItem.key` and to the key of `fieldSources`; also the `data-field` of the wrapper. */
  key: string
  label: string
  control: FillControl
  /** Columns of 12 on a wide screen. Set by `layoutFields`, which widens a row that would end half empty. */
  span: number
  /** Columns of 12 on a tablet: 6 (two across) or 12. */
  spanMd: number
  required: boolean
  placeholder?: string
  /** One short sentence, only where it prevents a mistake. */
  hint?: string
  /** Lives in `draft.extras` (screening fields). None of the form's own fields do; the helpers below still handle it. */
  extra: boolean
  from?: SourceDocument
  /** The admin-managed option list of a select. */
  category?: string
  uppercase?: boolean
  /** The date cannot be later than today (date of birth). */
  notFuture?: boolean
}

/** Only the fields of CareIndia_Application_Form_Order_Wise.docx: personal, passport and experience. */
const DEFS: FieldDef[] = [...PERSONAL_FIELDS, ...PASSPORT_FIELDS, ...EXPERIENCE_FIELDS]

interface Extras { placeholder?: string; hint?: string; uppercase?: boolean; notFuture?: boolean; label?: string }

function field(path: string, control: FillControl, span: number, extras: Extras = {}): FillField {
  const def = DEFS.find((item) => item.path === path)
  if (!def) throw new Error(`Unknown application form field: ${path}`)
  return {
    key: path,
    label: extras.label ?? def.label,
    control,
    span,
    spanMd: span >= 9 ? 12 : 6,
    required: def.required,
    placeholder: extras.placeholder,
    hint: extras.hint,
    extra: path.startsWith('screening.'),
    from: def.from,
    category: def.category,
    uppercase: extras.uppercase,
    notFuture: extras.notFuture,
  }
}

/* ------------------------------------------------------------------ Cards */

export type FillCardKind = 'fields' | 'passport' | 'education' | 'experience'

export interface FillCard {
  /** The paper form's section number: the element id is `af-fill-section-<no>`. */
  no: number
  kind: FillCardKind
  title: string
  /** One short line under the title, or empty when the title says it all. */
  hint: string
  fields: FillField[]
  /** Columns of 12 the fields may use: 9 on the Personal section, where the photo tile takes the first three. */
  lane: number
}

/**
 * The sections a person fills, with the paper form's own numbers and titles (CareIndia_Application_Form_Order_Wise.docx):
 * 1 Personal details, 2 Passport details, 3 Educational details, 4 Previous experience. Sections 5 (Documents),
 * 6 (Client and recruitment) and 7 (Recruitment process tracking) are the shell's. Fields the model keeps but the docx does
 * not have (WhatsApp, email, screening answers) are not shown.
 */
const cardDefs: Array<Omit<FillCard, 'fields'> & { fields: FillField[] }> = [
  {
    no: 1, kind: 'fields', lane: 9,
    title: 'Personal details',
    hint: '',
    fields: [
      field('personal.candidateName', 'text', 3, { placeholder: 'As written in the passport' }),
      field('personal.fatherName', 'text', 3, { placeholder: 'As written in the passport' }),
      field('personal.dateOfBirth', 'date', 3, { notFuture: true }),
      field('personal.maritalStatus', 'select', 3),
      field('personal.gender', 'select', 3),
      field('personal.nationality', 'text', 3, { placeholder: 'For example Indian' }),
      field('personal.languagesKnown', 'languages', 5, { placeholder: 'Type a language and press Enter' }),
      field('personal.mobileNumber', 'phone', 4),
      field('personal.currentAddress', 'textarea', 12, { placeholder: 'House or street, city, state and PIN code' }),
    ],
  },
  {
    no: 2, kind: 'passport', lane: 12,
    title: 'Passport details',
    hint: '',
    fields: [
      field('passport.passportNumber', 'text', 3, { placeholder: 'For example M1234567', uppercase: true }),
      field('passport.placeOfIssue', 'text', 3, { placeholder: 'As written in the passport' }),
      field('passport.issueDate', 'date', 3),
      field('passport.expiryDate', 'date', 3),
    ],
  },
  {
    no: 3, kind: 'education', lane: 12,
    title: 'Educational details',
    hint: 'Newest first.',
    fields: [],
  },
  {
    no: 4, kind: 'experience', lane: 12,
    title: 'Previous experience',
    hint: 'Newest employer first.',
    fields: [
      field('experience.totalExperience', 'years', 4, { placeholder: 'For example 6' }),
      field('experience.experienceIndia', 'years', 4, { placeholder: 'For example 4' }),
      field('experience.experienceAbroad', 'years', 4, { placeholder: 'For example 2' }),
    ],
  },
]

/* ------------------------------------------------------------------ Layout */

/**
 * Packs fields into rows of 12 columns and widens each row that would end half empty (3 + 3 becomes 6 + 6, a lone
 * field spans the row). The tablet layout does the same with halves. A field wider than the lane (the address under
 * the photo) takes a row of its own. Pure: returns new field objects.
 */
export function layoutFields(fields: FillField[], lane = 12): FillField[] {
  const out = fields.map((item) => ({ ...item }))
  const pack = (limit: number, width: (item: FillField) => number, apply: (item: FillField, value: number) => void) => {
    let row: FillField[] = []
    let used = 0
    const close = () => {
      if (row.length && used < limit) {
        const left = limit - used
        const each = left % row.length === 0 ? left / row.length : 0
        row.forEach((item, index) => apply(item, width(item) + (each || (index === row.length - 1 ? left : 0))))
      }
      row = []
      used = 0
    }
    for (const item of out) {
      const columns = width(item)
      if (used + columns > limit) close()
      row.push(item)
      used += columns
      if (used >= limit) close()
    }
    close()
  }
  pack(lane, (item) => item.span, (item, value) => { item.span = value })
  pack(12, (item) => item.spanMd, (item, value) => { item.spanMd = value })
  return out
}

/** The sections of the Fill details view. */
export function cardsFor(): FillCard[] {
  return cardDefs.map((card) => ({ ...card, fields: layoutFields(card.fields, card.lane) }))
}

/** Every field of every section (for completeness checks and tests). */
export const PASSPORT_FILL_FIELDS: FillField[] = layoutFields(cardDefs.find((card) => card.kind === 'passport')!.fields)
export const ALL_FILL_FIELDS: FillField[] = cardDefs.flatMap((card) => card.fields)
export const FILL_KEYS: string[] = ALL_FILL_FIELDS.map((item) => item.key)

/* ------------------------------------------------------------------ Missing counts */

/** The items of `missing` that belong to this section. Education and work history are whole-section items; documents and passport items belong to the Documents block (the shell's). */
export function cardMissing(card: Pick<FillCard, 'kind' | 'fields'>, missing: MissingItem[]): MissingItem[] {
  const keys = new Set(card.fields.map((item) => item.key))
  return missing.filter((item) => {
    if (card.kind === 'education') return item.key === 'education'
    if (card.kind === 'experience' && item.key === 'experience.history') return true
    return keys.has(item.key)
  })
}

/** "3 to add". Empty when nothing is missing, so a complete card shows no chip of this kind. */
export function toAddText(count: number): string {
  return count > 0 ? `${count} to add` : ''
}

/** Whether a card has anything the server counts as required (so a finished card can say "Complete"). */
export function cardCounts(card: Pick<FillCard, 'kind' | 'fields'>): boolean {
  if (card.kind === 'education') return true
  if (card.kind === 'experience') return true
  return card.fields.some((item) => item.required)
}

/* ------------------------------------------------------------------ Reading and writing the draft */

const isList = (control: FillControl) => control === 'languages'

/** The current value of a field in the draft: text, or a list of words for languages. */
export function readField(draft: FormDraft, item: Pick<FillField, 'key' | 'control' | 'extra'>): string | string[] {
  if (item.extra) return draft.extras[item.key] ?? ''
  const value = valueAt(draft, item.key)
  if (isList(item.control)) return Array.isArray(value) ? (value as string[]) : []
  return value == null ? '' : String(value)
}

/** A new draft with one field changed; nothing else is touched, so the paper view re-renders only from this change. */
export function writeField(draft: FormDraft, item: Pick<FillField, 'key' | 'extra'>, value: string | string[]): FormDraft {
  if (item.extra) return { ...draft, extras: { ...draft.extras, [item.key]: String(value) } }
  const [section, name] = item.key.split('.')
  const current = (draft as unknown as Record<string, Record<string, unknown>>)[section]
  return { ...draft, [section]: { ...current, [name]: value } } as FormDraft
}

/** Languages and the list-like screening fields travel as comma separated text in the extras; one place for both directions. */
export const listToText = (list: string[]): string => list.join(', ')
export const textToList = (text: string): string[] => text.split(',').map((part) => part.trim()).filter(Boolean)

/* ------------------------------------------------------------------ Where a value came from */

export type SourceChip = 'resume' | 'passport' | 'document'

export const SOURCE_CHIP_TEXT: Record<SourceChip, string> = { resume: 'From resume', passport: 'From passport', document: 'From a document' }

const same = (a: unknown, b: unknown) => JSON.stringify(a ?? '') === JSON.stringify(b ?? '')

/**
 * The "From passport" / "From resume" chip of a field: only while the value is still the one a document filled in. Once a
 * person types over it the draft differs from the saved form, so the chip goes and the field is treated as typed by hand.
 */
export function sourceChipFor(form: Pick<ApplicationFormData, 'fieldSources' | 'documents'>, saved: FormDraft, draft: FormDraft, item: Pick<FillField, 'key' | 'from' | 'control' | 'extra'>): SourceChip | null {
  const value = readField(draft, item)
  if (isBlank(value)) return null
  if (!same(readField(saved, item), value)) return null
  const kind = provenanceOf(form, item.key, true, item.from)
  return kind === 'resume' || kind === 'passport' ? kind : kind === 'extracted' ? 'document' : null
}

/** The chip of the education or work history list: the list is as the document filled it in. */
export function rowsSourceChip(form: Pick<ApplicationFormData, 'fieldSources' | 'documents'>, saved: FormDraft, draft: FormDraft, key: 'education' | 'experience.history'): SourceChip | null {
  const rows = key === 'education' ? draft.education : draft.experience.history
  const before = key === 'education' ? saved.education : saved.experience.history
  const strip = (list: Array<{ rowId: string }>) => list.map(({ rowId: _id, ...rest }) => rest)
  if (rows.every((row) => isBlankRow(row)) || !same(strip(rows), strip(before))) return null
  const kind = provenanceOf(form, key, true, 'resume')
  return kind === 'resume' || kind === 'passport' ? kind : kind === 'extracted' ? 'document' : null
}

/* ------------------------------------------------------------------ Passport status */

export type PassportStatus = 'unknown' | 'valid' | 'expiring_soon' | 'expired'

export const PASSPORT_STATUS_TEXT: Record<PassportStatus, string> = {
  unknown: '',
  valid: 'Passport valid',
  expiring_soon: 'Expiring soon',
  expired: 'Passport expired',
}

/** From the date in the draft (not the saved one), so the chip follows what is typed. */
export function passportStatus(expiryDate: string, now: Date = new Date()): PassportStatus {
  if (!expiryDate || Number.isNaN(new Date(expiryDate).getTime())) return 'unknown'
  const notice = passportExpiryNotice(expiryDate, now)
  if (!notice) return 'valid'
  return notice.tone === 'expired' ? 'expired' : 'expiring_soon'
}

/** The sentence under the date of expiry, only when there is something to be careful about. */
export function passportExpiryHint(expiryDate: string, now: Date = new Date()): string {
  const notice = expiryDate ? passportExpiryNotice(expiryDate, now) : null
  return notice ? `${notice.text}. Most destinations want six months of validity left.` : ''
}

/* ------------------------------------------------------------------ Rows */

export interface FillColumn { key: string; label: string; placeholder: string; /** Columns of 12; the tablet layout derives from it (see `columnSpanMd`). */ span: number; inputMode?: 'numeric'; maxLength?: number }

/** Education: the form's own columns and keys (the server keeps one text for the institution; it is shown as two boxes). */
export const EDUCATION_COLUMNS_FILL: FillColumn[] = [
  { key: 'level', label: 'Level of education or course', placeholder: 'For example B.Tech', span: 3 },
  { key: 'passedYear', label: 'Year passed', placeholder: 'YYYY', span: 3, inputMode: 'numeric', maxLength: 4 },
  { key: 'institutionName', label: 'Name of institution', placeholder: 'For example ABC College', span: 6 },
  { key: 'institutionAddress', label: 'Address of institution', placeholder: 'For example Chennai, Tamil Nadu', span: 12 },
]

export const EXPERIENCE_COLUMNS_FILL: FillColumn[] = [
  { key: 'company', label: 'Name of company', placeholder: 'For example XYZ Ltd', span: 3 },
  { key: 'designation', label: 'Designation', placeholder: 'For example Welder', span: 3 },
  { key: 'country', label: 'Country', placeholder: 'For example India', span: 3 },
  { key: 'period', label: 'Period of employment', placeholder: 'Jan 2018 to Mar 2020', span: 3 },
]

/** Tablet: two across, and anything half the row or wider takes the whole row. */
export const columnSpanMd = (column: Pick<FillColumn, 'span'>): number => (column.span >= 6 ? 12 : 6)

export interface EducationView { rowId: string; level: string; passedYear: string; institutionName: string; institutionAddress: string }

/** One stored text, two boxes. */
export function educationView(row: EducationRow): EducationView {
  const { name, address } = splitInstitution(row.institute)
  return { rowId: row.rowId, level: row.level, passedYear: row.passedYear, institutionName: name, institutionAddress: address }
}

/** The row after one box changed; the institution boxes are joined back into the stored text. */
export function changeEducationRow(row: EducationRow, key: string, value: string): EducationRow {
  if (key === 'institutionName') return { ...row, institute: joinInstitution(value, splitInstitution(row.institute).address) }
  if (key === 'institutionAddress') return { ...row, institute: joinInstitution(splitInstitution(row.institute).name, value) }
  return { ...row, [key]: value }
}

export const changeExperienceRow = (row: ExperienceRow, key: string, value: string): ExperienceRow => ({ ...row, [key]: value })

/** Where the education columns' keys come from, so a test can prove the view and the paper sheet agree. */
export const SHEET_EDUCATION_KEYS: string[] = EDUCATION_FORM_COLUMNS.map((column) => column.key)
export const SHEET_EXPERIENCE_KEYS: string[] = EXPERIENCE_COLUMNS.map((column) => column.key)
