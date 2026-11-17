/**
 * Pure helpers behind the Import candidates screen: names and colours, document chips, the stepper, the filter, the
 * elapsed-time counter, the result track of an imported candidate and the sticky action bar. No React, no network.
 */
import type { AlreadyInCrm, DuplicateChoice, IdentifiedPerson, IdentifyResult, ImportedStatus, ImportIssue, ImportPlan, ImportResult, MatchStrength, PlannedFile, RepeatedPage, ResultDocument } from './import-api'

// ------------------------------------------------------------------------------ people

/** "Gunavelan Raman" -> "GR", "K.Sivakumar" -> "KS", "alnoor" -> "A", "" -> "?". */
export function initialsOf(name: string): string {
  const words = name.replace(/[._\-/\\]+/g, ' ').replace(/\([^)]*\)/g, ' ').split(/\s+/).filter((word) => /[\p{L}\p{N}]/u.test(word))
  if (words.length === 0) return '?'
  const first = Array.from(words[0].replace(/[^\p{L}\p{N}]/gu, ''))[0] ?? '?'
  if (words.length === 1) return first.toUpperCase()
  const last = Array.from(words[words.length - 1].replace(/[^\p{L}\p{N}]/gu, ''))[0] ?? ''
  return (first + last).toUpperCase()
}

export const AVATAR_TONES = 8

/** The same name always gets the same one of eight colours (0-7), whatever the letter case or spacing. */
export function avatarTone(name: string): number {
  const text = name.trim().toLowerCase().replace(/\s+/g, ' ')
  let hash = 0
  for (let i = 0; i < text.length; i += 1) hash = (hash * 31 + text.charCodeAt(i)) >>> 0
  return hash % AVATAR_TONES
}

// ------------------------------------------------------------------------------ the candidate's name

/** The name is taken from the documents only, never from a folder or file name. Until it is read the candidate is called this. */
export const NAME_NOT_READ = 'Name not read yet'
/** The title of a card in the check whose documents have not been read for a name. */
export const NAME_TO_BE_READ = 'Name will be read from the documents'
/** The note under an imported candidate whose name has not been read yet. */
export const NAME_READ_NOTE = 'The name is read from the documents when you press Read the documents'
/** The caption over the "Will be saved as" column. */
export const FILES_CAPTION = 'Files are named after the candidate once the name is read from the documents. Until then they carry the Care India ID.'
/** What a stored file name starts with while the Care India ID is not known yet (it is assigned at import). */
export const CI_PLACEHOLDER = 'CI-…'

export type NameOrigin = 'documents' | 'sheet' | 'typed' | 'pending'

/** What the server says about where a name came from. The older 'folder' and 'files' (and anything unknown or empty) count as "not read yet". */
export function normalizeNameSource(source: string | null | undefined): NameOrigin {
  return source === 'documents' || source === 'sheet' || source === 'typed' ? source : 'pending'
}

/**
 * The name read from this candidate's documents on the Import screen (the reading of the files), or null when none was read: a card
 * made from the folder or file names has no name yet. A person the reading found but could not name ("Unnamed candidate", an empty
 * name source) is not named either.
 */
export function readName(plan: Pick<ImportPlan, 'name' | 'identified' | 'needsLook'>, person?: Pick<IdentifiedPerson, 'evidence' | 'conflicts' | 'nameSource'>): string | null {
  if (!plan.identified) return null
  if (nameQuality(plan.name, plan, person) === 'unnamed') return null
  if (person && !person.nameSource) return null
  return plan.name.trim() || null
}

/** The quiet tag on a card: the folder the files were picked in, and (when one folder was split into several cards) the group of files the card holds. */
export function folderTag(plan: Pick<ImportPlan, 'folder' | 'parent' | 'identified'>, name: string | null): { folder: string; group: string | null } {
  const folder = plan.parent || plan.folder
  const split = Boolean(plan.parent) && plan.parent !== plan.folder
  const group = split && !plan.identified && !name ? plan.folder.slice(plan.folder.lastIndexOf(' / ') + 3) : null
  return { folder, group }
}

/** What to call a card where a choice is offered (who a file belongs to): the name read from the documents, else the folder or file group it came from. */
export function candidateLabel(plan: Pick<ImportPlan, 'name' | 'folder' | 'parent' | 'identified' | 'needsLook'>, person?: Pick<IdentifiedPerson, 'evidence' | 'conflicts' | 'nameSource'>): string {
  const name = readName(plan, person)
  if (name) return name
  if (plan.identified) return plan.name
  return `Files of "${plan.name}"`
}

export interface ImportedTitle {
  /** The card's title: the name the server gives, or "Name not read yet · CI-167". */
  title: string
  /** The name has been read (from the documents, the sheet or typed). */
  read: boolean
  /** The note under the title when the name is still to be read; null otherwise. */
  note: string | null
}

/** The title of an imported candidate's card, from the status the server gives (it follows the reading of the documents as the status is asked again). */
export function importedTitle(status: Pick<ImportedStatus, 'name' | 'nameSource' | 'reading'> | undefined, careId: string | null | undefined, fallback: string | null): ImportedTitle {
  const id = (careId ?? '').trim()
  if (!status) {
    return fallback ? { title: fallback, read: true, note: null } : { title: `${NAME_NOT_READ}${id ? ` · ${id}` : ''}`, read: false, note: NAME_READ_NOTE }
  }
  const origin = normalizeNameSource(status.nameSource)
  const name = (status.name ?? '').trim()
  if (origin !== 'pending' && name && name.toLowerCase() !== NAME_NOT_READ.toLowerCase()) return { title: name, read: true, note: null }
  const readingDone = status.reading.total > 0 && status.reading.open === 0
  return { title: `${NAME_NOT_READ}${id ? ` · ${id}` : ''}`, read: false, note: readingDone ? 'No name could be read from the documents. Check them on the candidate’s page.' : NAME_READ_NOTE }
}

/** The name of the folder that was picked (the first part of every file's path). */
export function rootNameOf(files: ArrayLike<File>): string {
  const first = files[0] as (File & { webkitRelativePath?: string }) | undefined
  const path = first?.webkitRelativePath ?? ''
  return path.split('/').filter(Boolean)[0] ?? ''
}

// ------------------------------------------------------------------------------ when reading the files is worth offering

/** Words that name a document or a scan, not a person: a file called "scan1.pdf" or "passport copy.jpg" says nothing about whose it is. */
const DOCUMENT_WORDS = new Set((
  'resume cv curriculum vitae passport aadhaar aadhar adhar aadhaaar aadhar card offer signed letter medical pcc police clearance certificate visa ticket air '
  + 'photo scan scanned scans img image images doc docs document documents page pages copy front back education experience degree diploma marksheet sslc hsc '
  + 'form application undertaking emigration insurance invoice stamping stamp whatsapp final new old file files pax and the for jpeg jpg png pdf heic webp docx '
  // short codes and documents that name nobody: "PP", "EVisa-123", "Itinerary AB1CD2", a receipt, a Google review
  + 'pp evisa itinerary stamped receipt advance google review picker '
  // what a scanner, a phone or an office program calls a file when nobody renamed it (the server's list of non-names)
  + 'camscanner scanner untitled dsc dscn pxl pic picture pictures photos screenshot screen adobe office lens noname '
  // trades follow a name in a folder or file name ("Meera Nair Welder")
  + 'steel fixer welder fitter driver helper mason carpenter electrician plumber painter foreman rigger cook chef mechanic operator'
).split(/\s+/))

/** Words a folder is called when it holds a batch rather than a person. */
const FOLDER_WORDS = new Set('batch batches candidate candidates folder folders import imports upload uploads data test sample samples travelled travelled travelers travellers all pending'.split(/\s+/))

/**
 * The name words a file or folder name starts with, read the way the server's `_name_in` reads them: the name runs until the first hyphen or
 * underscore, then up to three words, and ends at the first word that is a document, a trade, a number or a camera counter ("IMG_2031").
 * "K.Sivakumar-offer.jpg" -> ["Sivakumar"], "Rashid khan - PP.pdf" -> ["Rashid", "khan"], "CamScanner 12-10-2026.pdf" -> [].
 */
function nameWordsOfBase(base: string, extra?: Set<string>): string[] {
  const head = base.trim().split(/\s*[-_]\s*/, 1)[0] ?? ''
  const tokens = head.split(/[,()\s]+/).filter(Boolean)
  while (tokens.length > 0) {
    const match = /^((?:[A-Za-z]\.)+)(.*)$/.exec(tokens[0])
    if (!match) break
    if (match[2]) { tokens[0] = match[2]; break }
    tokens.shift()
  }
  if (tokens.length > 0 && /^[A-Za-z]+\d/.test(tokens[0])) return []
  const words: string[] = []
  for (const token of tokens.slice(0, 3)) {
    const lower = token.toLowerCase()
    if (!(/^\p{L}+$/u.test(token) && token.length >= 2 && !DOCUMENT_WORDS.has(lower) && !extra?.has(lower))) break
    words.push(token)
  }
  if (words.length === 0 || words[0].length < 2 || words.join('').length < 4) return []
  return words
}

/** The name words a file's own name starts with ([] when it names nobody). */
export function nameWordsOf(path: string): string[] {
  const base = (path.replace(/\\/g, '/').split('/').pop() ?? path).replace(/\.[A-Za-z0-9]{2,5}$/, '')
  return nameWordsOfBase(base)
}

/** True when the file's name starts with a word that could be a person's name (not a document word, a scanner default, a number or a short code like "PP"). */
export function nameInPath(path: string): boolean {
  return nameWordsOf(path).length > 0
}

/** The longest common run of letters of two strings, as a share of both ("rashid"/"rashiid" -> 0.91): the server tells slips of a letter apart from different people by this. */
function likeness(a: string, b: string): number {
  if (a.length === 0 || b.length === 0) return 0
  let previous = new Array<number>(b.length + 1).fill(0)
  for (let i = 1; i <= a.length; i += 1) {
    const row = new Array<number>(b.length + 1).fill(0)
    for (let j = 1; j <= b.length; j += 1) row[j] = a[i - 1] === b[j - 1] ? previous[j - 1] + 1 : Math.max(previous[j], row[j - 1])
    previous = row
  }
  return (2 * previous[b.length]) / (a.length + b.length)
}

/** Two spellings of one name (spaces and case ignored): equal, or a slip of a letter. "mohamedali" and "mohamedasif" stay apart (the server's `_same_person`). */
const sameSpelling = (a: string, b: string) => a === b || (Math.min(a.length, b.length) >= 5 && likeness(a, b) >= 0.9)
/** One name is the other with more of the name after it, or a spelling of it ("rashid khan" and "rashid khan nawazkhan"). */
const sameOrLonger = (a: string, b: string) => {
  const [short, long] = a.length <= b.length ? [a, b] : [b, a]
  return sameSpelling(a, b) || (short.length >= 3 && long.startsWith(short))
}

const IDENTITY_KINDS = new Set(['passport', 'aadhaar', 'resume'])

/** The name of a file without what only tells its sides apart: "Aadhaar front" and "Aadhaar back" are one document. */
const withoutSides = (path: string) => (path.split('/').pop() ?? path).toLowerCase().replace(/\.[a-z0-9]{2,5}$/, '').replace(/\b(front|back|side|page|pg)\b/g, '').replace(/[^a-z0-9]+/g, '')

/** True when two different files are the same identity document (two passports, two Aadhaar cards, two resumes): the signature of two people in one folder. */
function sameKindTwice(files: Pick<PlannedFile, 'path' | 'kind' | 'kinds' | 'guessed'>[]): boolean {
  const stems = new Map<string, Set<string>>()
  for (const file of files) {
    if (file.guessed) continue
    for (const kind of new Set(file.kinds?.length ? file.kinds : file.kind ? [file.kind] : [])) {
      if (!IDENTITY_KINDS.has(kind)) continue
      const seen = stems.get(kind) ?? new Set<string>()
      seen.add(withoutSides(file.path))
      stems.set(kind, seen)
    }
  }
  return [...stems.values()].some((seen) => seen.size >= 2)
}

/** Why the files of a folder could belong to more than one person. */
export type ReadReason =
  | 'left_out'          // some file's name says nobody's (or two people's) name
  | 'no_name'           // some file names say no name at all ("CamScanner 12-10-2026.pdf", "scan1.pdf")
  | 'names_disagree'    // the file names carry different names
  | 'folder_disagrees'  // the folder is named for one person and the files for another
  | 'first_name_only'   // every file starts with the same first name only, and an identity document appears twice
  | 'same_kind_twice'   // one name, but two passports, two Aadhaar cards or two resumes

/** The amber line over the Read button. */
export const READ_HINT = 'These file names could belong to more than one person'

/** One sentence on why, shown under the amber line. */
export function readReasonDetail(reason: ReadReason | null | undefined): string {
  switch (reason) {
    case 'first_name_only': return 'Every file starts with the same first name and there is more than one passport, Aadhaar card or resume, which usually means two people.'
    case 'same_kind_twice': return 'There is more than one passport, Aadhaar card or resume, which usually means more than one person.'
    case 'names_disagree': return 'The file names start with different names that the check could not tell apart.'
    case 'folder_disagrees': return 'The folder name and the file names do not give the same name.'
    case 'no_name': return 'Some or all file names say no name (they are called like a scanner or a phone calls them).'
    default: return ''
  }
}

/**
 * Why the check could NOT tell whose the files are from their names (null when it could), so reading them (OCR, on this computer) is worth
 * offering and recommending. Takes the candidates the check made from ONE picked folder. A file whose document type is unknown does not
 * count towards "twice": it is read at import anyway. It follows the server's `_units` / `split_people`, which read a name off the start of each file name.
 * - already read by content: no
 * - some file names say nobody's (or two people's) name (`leftOut`): yes
 * - several people told apart by their file names, nothing left over: no
 * - one candidate (a single picked folder; one folder of many is named after its candidate, so generic file names are expected):
 *   yes when some file name says no name (CamScanner, IMG_, scan1), when the file names carry different names or a name the folder
 *   does not, or when every file starts with the same first name only and an identity document (passport, Aadhaar, resume) is there twice;
 *   a full name that is the same in every file (spelled "Rashid khan", "RASHIDKHAN") with no identity document twice is one person: no.
 */
export function personReadingReason(plans: Pick<ImportPlan, 'folder' | 'parent' | 'files' | 'leftOut' | 'identified'>[], options: { pickedFolders?: number } = {}): ReadReason | null {
  if (plans.length === 0) return null
  if (plans.some((plan) => plan.identified)) return null
  if (plans.some((plan) => (plan.leftOut ?? []).length > 0)) return 'left_out'
  if (plans.length > 1) return null
  if ((options.pickedFolders ?? 1) > 1) return null
  const plan = plans[0]
  const files = plan.files.filter((file) => !file.skipped)
  if (files.length < 2) return null
  const names = files.map((file) => nameWordsOf(file.path))
  if (names.some((words) => words.length === 0)) return 'no_name'

  const keys = names.map((words) => words.join('').toLowerCase())
  const longest = keys.reduce((a, b) => (b.length > a.length ? b : a))
  if (keys.some((key) => !sameOrLonger(key, longest))) return 'names_disagree'

  const folderName = (plan.parent || plan.folder).split(' / ')[0]
  const folderKey = nameWordsOfBase(folderName, FOLDER_WORDS).join('').toLowerCase()
  if (folderKey && !sameOrLonger(folderKey, longest)) return 'folder_disagrees'

  // A full name has two or more words in some file; a file that spells it without the spaces ("RASHIDKHAN") counts as the same full name.
  const fullKeys = names.flatMap((words, i) => (words.length >= 2 ? [keys[i]] : []))
  const firstNameOnly = keys.some((key, i) => names[i].length < 2 && !fullKeys.some((full) => sameSpelling(full, key)))
  if (sameKindTwice(files)) return firstNameOnly ? 'first_name_only' : 'same_kind_twice'
  return null
}

/** Whether to offer (and recommend) reading the files; see `personReadingReason`. */
export function needsPersonReading(plans: Pick<ImportPlan, 'folder' | 'parent' | 'files' | 'leftOut' | 'identified'>[], options: { pickedFolders?: number } = {}): boolean {
  return personReadingReason(plans, options) !== null
}

// ------------------------------------------------------------------------------ one person found: give them the files nobody owns

/**
 * The files to offer "Give all unplaced files to <Name>" for: the reading found exactly ONE person in the folder and some of its files are
 * still not given to anyone. Null when there are two or more people (then a person chooses file by file), nothing is unplaced, or the card
 * was not made by reading the files.
 */
export function unplacedForSoleCandidate(plans: Pick<ImportPlan, 'folder' | 'parent' | 'leftOut' | 'identified'>[]): { target: string; parent: string; paths: string[] } | null {
  if (plans.length !== 1) return null
  const [plan] = plans
  const paths = [...new Set(plan.leftOut ?? [])]
  if (!plan.identified || paths.length === 0) return null
  return { target: plan.folder, parent: plan.parent || plan.folder, paths }
}

/** The "Belongs to" choices that give each of these files to one candidate: key "<picked folder>/<path>" -> the candidate's key (what the per-file select sets). */
export function assignAllTo(parent: string, paths: string[], target: string): Record<string, string> {
  return Object.fromEntries(paths.map((path) => [`${parent}/${path}`, target]))
}

/** The sentence beside the button: where the files will be filed, and that they can be moved later. */
export function giveAllNote(count: number, name: string | null): string {
  const who = name ?? 'this candidate'
  return `${count === 1 ? 'This file is' : `These ${count} files are`} not given to anyone yet. ${count === 1 ? 'It' : 'They'} will be filed under ${who}, and you can move ${count === 1 ? 'it' : 'them'} to another candidate later, or leave ${count === 1 ? 'it' : 'any of them'} out in the Files table.`
}

export type NameQuality = 'ok' | 'low' | 'unnamed'

const UNNAMED = /^(unnamed candidate|candidate \d+$)/i

/** "Unnamed candidate (Aadhaar ending 123)" -> "Aadhaar ending 123"; null when there is no such part. */
export function unnamedDetail(name: string): string | null {
  const match = /\(([^)]+)\)\s*$/.exec(name)
  return UNNAMED.test(name.trim()) && match ? match[1].trim() : null
}

/**
 * How far to trust the name of a candidate found by reading the files. "unnamed": no name could be read (the server calls them
 * "Unnamed candidate (...)"); "low": the person needs a look and either one document alone gives the name, or two different names are
 * on their pages, or the name is mostly consonants (OCR noise); "ok" otherwise. Never shown as a proper name when it is not ok.
 */
export function nameQuality(name: string, plan: { needsLook?: boolean }, person?: Pick<IdentifiedPerson, 'evidence' | 'conflicts'>): NameQuality {
  const text = name.trim()
  if (!text || UNNAMED.test(text) || person?.conflicts.some((c) => c.kind === 'no_name')) return 'unnamed'
  if (!plan.needsLook) return 'ok'
  const sources = person ? person.evidence.filter((e) => / name$/.test(e)).length : 0
  const noisy = text.split(/\s+/).some((word) => {
    const letters = word.replace(/[^\p{L}]/gu, '')
    const vowels = (letters.match(/[aeiouyAEIOUY]/g) ?? []).length
    return letters.length >= 5 && (vowels / letters.length < 0.25 || /[^aeiouy]{5}/i.test(letters) || /^(?!s)[^aeiouy][bcdfgjkmpqtvxz]/i.test(letters))
  })
  const differ = person?.conflicts.some((c) => c.kind === 'names_differ') ?? false
  return noisy || differ || (person !== undefined && sources <= 1) ? 'low' : 'ok'
}

export interface SplitIssues { errors: ImportIssue[]; notes: ImportIssue[]; existing: string | null; /** "Checked against the CRM while importing: ...", shown quietly on the card. */ checked: string | null }

/**
 * The plan's issues sorted for the card: real errors (shown prominently), notes (collapsed under "N notes") and, apart, the calm
 * "Already in the CRM as CI-162 (Name)" message, which is information and not an error.
 */
export function splitIssues(issues: ImportIssue[]): SplitIssues {
  const existing = issues.find((issue) => /^already in the crm/i.test(issue.message))?.message ?? null
  const checked = issues.find((issue) => /^checked against the crm/i.test(issue.message))?.message ?? null
  const rest = issues.filter((issue) => !/^already in the crm/i.test(issue.message) && !/^checked against the crm/i.test(issue.message))
  return { errors: rest.filter((issue) => issue.level === 'error'), notes: rest.filter((issue) => issue.level !== 'error'), existing, checked }
}

// ------------------------------------------------------------------------------ document chips

export type ChipTone = 'blue' | 'violet' | 'amber' | 'teal' | 'green' | 'rose' | 'slate'

export interface DocField { label: string; tone: ChipTone }

/** The document fields a candidate has, with the short word a recruiter uses for each and its colour. */
export const DOC_FIELDS: Record<string, DocField> = {
  resume: { label: 'Resume', tone: 'blue' },
  application_form: { label: 'Application form', tone: 'blue' },
  passport: { label: 'Passport', tone: 'violet' },
  aadhaar: { label: 'Aadhaar', tone: 'amber' },
  photo: { label: 'Photo', tone: 'slate' },
  education_certificate: { label: 'Education', tone: 'teal' },
  education: { label: 'Education', tone: 'teal' },
  experience_certificate: { label: 'Experience', tone: 'teal' },
  experience: { label: 'Experience', tone: 'teal' },
  signed_offer: { label: 'Offer', tone: 'green' },
  medical: { label: 'Medical', tone: 'rose' },
  pcc: { label: 'PCC', tone: 'slate' },
  visa: { label: 'Visa', tone: 'violet' },
  visa_stamping: { label: 'Visa stamping', tone: 'violet' },
  air_ticket: { label: 'Air ticket', tone: 'blue' },
  undertaking_letter: { label: 'Undertaking', tone: 'slate' },
  emigration: { label: 'Emigration', tone: 'teal' },
  insurance: { label: 'Insurance', tone: 'green' },
  invoice: { label: 'Invoice', tone: 'amber' },
  google_review: { label: 'Google review', tone: 'amber' },
  advance_receipt: { label: 'Advance receipt', tone: 'green' },
  other: { label: 'Other', tone: 'slate' },
}

/** The key of the chip for files whose name says nothing about what they are. */
export const UNSORTED = 'unsorted'

export function fieldOf(key: string): DocField {
  if (key === UNSORTED) return { label: 'Sorted at import', tone: 'slate' }
  return DOC_FIELDS[key] ?? { label: key.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase()), tone: 'slate' }
}

/** [2, 3] -> "2-3", [1, 3, 4] -> "1, 3-4", [5] -> "5". Order and repeats do not matter. */
export function pageRanges(pages: number[]): string {
  const sorted = [...new Set(pages)].filter((n) => Number.isFinite(n)).sort((a, b) => a - b)
  const parts: string[] = []
  for (let i = 0; i < sorted.length; i += 1) {
    let j = i
    while (j + 1 < sorted.length && sorted[j + 1] === sorted[j] + 1) j += 1
    parts.push(j > i ? `${sorted[i]}-${sorted[j]}` : String(sorted[i]))
    i = j
  }
  return parts.join(', ')
}

/** "page 2", "pages 2-3". */
export function pagesLabel(pages: number[]): string {
  const unique = new Set(pages)
  return `${unique.size === 1 ? 'page' : 'pages'} ${pageRanges(pages)}`
}

export interface DocChip { key: string; label: string; tone: ChipTone; count: number; pages: string | null; guessed: boolean }

/**
 * One chip per document field, in the order the fields first appear, from the files that will be imported. A file that holds several
 * documents counts once under each. A single file cut by page says which ("pages 2-3"); several files only show their number.
 */
export function docChips(files: Pick<PlannedFile, 'kind' | 'kinds' | 'skipped' | 'guessed' | 'pages'>[]): DocChip[] {
  const groups = new Map<string, { count: number; pages: number[][]; guessed: boolean }>()
  for (const file of files) {
    if (file.skipped) continue
    const keys = file.guessed ? [UNSORTED] : file.kinds?.length ? file.kinds : file.kind ? [file.kind] : ['other']
    for (const key of new Set(keys)) {
      const group = groups.get(key) ?? { count: 0, pages: [], guessed: false }
      group.count += 1
      if (file.pages?.length) group.pages.push(file.pages)
      if (file.guessed) group.guessed = true
      groups.set(key, group)
    }
  }
  return [...groups].map(([key, group]) => {
    const field = fieldOf(key)
    return { key, label: field.label, tone: field.tone, count: group.count, guessed: group.guessed, pages: group.count === 1 && group.pages.length === 1 ? pagesLabel(group.pages[0]) : null }
  })
}

/** The files a candidate brings, and the pages when they are known (from reading the files, or from files cut by page). */
export function planCounts(plan: Pick<ImportPlan, 'name' | 'files'>, identified?: Pick<IdentifyResult, 'people' | 'files'>): { documents: number; pages: number | null; leftOut: number } {
  const used = plan.files.filter((file) => !file.skipped)
  const person = identified?.people.find((p: IdentifiedPerson) => p.name === plan.name)
  const pages = person ? person.pageCount : used.length > 0 && used.every((file) => file.pages?.length) ? used.reduce((sum, file) => sum + (file.pages?.length ?? 0), 0) : null
  return { documents: used.length, pages, leftOut: plan.files.length - used.length }
}

// ------------------------------------------------------------------------------ the names the files are saved under

/** The same slug as the server's `name_slug`: every run of anything but letters and digits becomes one "_" ("S. Dinesh Kumar" -> "S_Dinesh_Kumar"). */
export function nameSlug(name: string): string {
  return (name || '').replace(/[^A-Za-z0-9]+/g, '_').replace(/^_+|_+$/g, '') || 'Candidate'
}

/** The lower-cased extension of an original file name, as the server takes it ("X.PDF" -> ".pdf", "scan" -> ""). */
function extensionOf(path: string): string {
  const base = path.replace(/\\/g, '/').replace(/\/+$/, '').split('/').pop() ?? ''
  const dot = base.lastIndexOf('.')
  return dot > 0 && dot < base.length - 1 ? base.slice(dot).toLowerCase() : ''
}

/** The server keeps at most 150 characters of a stored name (`_safe_name`). */
const STORED_NAME_LIMIT = 150

export interface StoredName {
  /** "Name-Document.ext" (or "CI-…-Document.ext" while the name is not read), as the server will save it; null when the document is only known during the import. */
  name: string | null
  /** The file is only sorted during the import (its name says nothing), so its stored name is not known yet. */
  pending?: boolean
  /** A scan with several documents is cut by page: one stored file per document, these are their names (in the order the documents are listed). */
  pieces?: string[]
  note?: string
}

/** Mirrors the server's `import_file_name` + `_safe_name`: "<Name or CI id>-<Document labels joined with _>[-2].<ext>"; adds the name to `taken`. */
function storedNameOf(person: string | null, labels: string[], original: string, taken: Set<string>): string {
  const label = labels.map(nameSlug).join('_') || 'Document'
  const extension = extensionOf(original)
  const stem = `${person ? nameSlug(person) : CI_PLACEHOLDER}-${label}`
  let candidate = `${stem}${extension}`
  let number = 1
  while (taken.has(candidate.toLowerCase())) {
    number += 1
    candidate = `${stem}-${number}${extension}`
  }
  taken.add(candidate.toLowerCase())
  return candidate.slice(0, STORED_NAME_LIMIT).replace(/^[-.]+|[-.]+$/g, '') || 'file'
}

/** The human labels of what a file counts as; empty when that is not known before the import. `override` replaces them (a kind picked in the table). */
function labelsOf(file: Pick<PlannedFile, 'kind' | 'kinds' | 'labels' | 'label' | 'guessed'>, override?: string[]): string[] {
  if (override?.length) return override
  if (file.guessed) return []
  const kinds = file.kinds?.length ? file.kinds : file.kind ? [file.kind] : []
  if (kinds.length === 0) return []
  return kinds.map((kind, i) => file.labels?.[i] ?? (kinds.length === 1 ? file.label : null) ?? fieldOf(kind).label)
}

/**
 * The name one file will be saved under. `name` is the name read from the documents; null while it is not known, and then the file carries
 * the Care India ID the import assigns ("CI-…-Passport.pdf"), never the folder name. `taken` holds the names already used for this candidate (lower-cased; this call adds to it),
 * so a second passport becomes "-2". A file that is only sorted during the import has no name yet (`pending`); a scan of several documents
 * is cut by page into one file per document (`pieces`), and `name` is what it would be called if it were kept whole.
 */
export function plannedStoredName(name: string | null, file: Pick<PlannedFile, 'path' | 'kind' | 'kinds' | 'labels' | 'label' | 'guessed'>, taken: Set<string>, override?: string[]): StoredName {
  const labels = labelsOf(file, override)
  if (labels.length === 0) return { name: null, pending: true }
  if (labels.length === 1) return { name: storedNameOf(name, labels, file.path, taken) }
  const whole = storedNameOf(name, labels, file.path, new Set(taken))
  const pieces = labels.map((label) => storedNameOf(name, [label], file.path, taken))
  return { name: whole, pieces, note: 'cut by page when it holds several documents' }
}

/**
 * Every file of the plan with the name it will be saved under, keyed by path. Files are taken in the order the server takes them (scans with
 * several documents first, then by lower-case path) so that "-2" falls on the same file; files that are left out get nothing. `overrides` maps a
 * path to the labels of a kind chosen in the table.
 */
export function plannedStoredNames(plan: Pick<ImportPlan, 'files'>, name: string | null, overrides: Record<string, string[]> = {}): Map<string, StoredName> {
  const count = (file: PlannedFile) => labelsOf(file, overrides[file.path]).length
  const files = plan.files.filter((file) => !file.skipped)
  const ordered = [...files].sort((a, b) => Number(count(a) === 1) - Number(count(b) === 1) || (a.path.toLowerCase() < b.path.toLowerCase() ? -1 : a.path.toLowerCase() > b.path.toLowerCase() ? 1 : 0))
  const taken = new Set<string>()
  const result = new Map<string, StoredName>()
  for (const file of ordered) result.set(file.path, plannedStoredName(name, file, taken, overrides[file.path]))
  return result
}

// ------------------------------------------------------------------------------ time

/** 85 -> "1:25", 7 -> "0:07", 3725 -> "1:02:05". Never negative. */
export function formatElapsed(totalSeconds: number): string {
  const seconds = Math.max(0, Math.floor(Number.isFinite(totalSeconds) ? totalSeconds : 0))
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const two = (n: number) => String(n).padStart(2, '0')
  return h > 0 ? `${h}:${two(m)}:${two(s)}` : `${m}:${two(s)}`
}

/**
 * How far the reading of the files probably is. The first seconds are "indeterminate" (null): nobody can tell yet. After that a smooth
 * curve towards (never reaching) 95 %, tuned to roughly 8 seconds per file, so the bar always moves yet never claims to be done.
 */
export function readEstimate(elapsedSeconds: number, fileCount: number): number | null {
  if (elapsedSeconds < 4) return null
  const expected = Math.max(20, fileCount * 8)
  return Math.min(0.95, 1 - Math.exp(-elapsedSeconds / expected))
}

/** True when nothing moved for `limitMs` (default one minute). */
export function isStalled(lastChangeAt: number, now: number, limitMs = 60000): boolean {
  return now - lastChangeAt >= limitMs
}

// ------------------------------------------------------------------------------ the stepper

export type StepState = 'done' | 'current' | 'todo'
export interface Step { key: 'folder' | 'check' | 'import' | 'read'; label: string; state: StepState }

export interface StepInput {
  hasFolder: boolean
  /** The first import has been started (some candidate is waiting, running, done or failed). */
  importStarted: boolean
  importing: boolean
  importedCount: number
  failedCount: number
  /** Every imported candidate has had its documents read (and there was something to read). */
  readFinished: boolean
}

/** The four steps: Choose folder, Check, Import, Read documents; the finished ones are ticked, the next is highlighted. */
export function computeSteps(input: StepInput): Step[] {
  const importDone = input.importStarted && !input.importing && input.failedCount === 0 && input.importedCount > 0
  const folder: StepState = input.hasFolder ? 'done' : 'current'
  const check: StepState = !input.hasFolder ? 'todo' : input.importStarted ? 'done' : 'current'
  const imported: StepState = !input.importStarted ? 'todo' : importDone ? 'done' : 'current'
  const read: StepState = !importDone ? 'todo' : input.readFinished ? 'done' : 'current'
  return [
    { key: 'folder', label: 'Choose folder', state: folder },
    { key: 'check', label: 'Check', state: check },
    { key: 'import', label: 'Import', state: imported },
    { key: 'read', label: 'Read documents', state: read },
  ]
}

// ------------------------------------------------------------------------------ candidate status, filter, search

/** How far one candidate's import has got (the screen keeps one per candidate once the import started). */
export interface RowProgress<R = unknown> {
  /**
   * done: imported (or imported before). duplicate: already in the CRM, nothing was created, a person chooses. skipped: a person left the duplicate alone.
   * added: the documents went to the candidate already in the CRM.
   */
  status: 'waiting' | 'running' | 'done' | 'failed' | 'duplicate' | 'skipped' | 'added'
  result?: R
  error?: string
}


export type CardStatus = 'ready' | 'look' | 'exists' | 'duplicate' | 'skipped' | 'added' | 'blocked' | 'waiting' | 'importing' | 'imported' | 'failed'

export const STATUS_LABEL: Record<CardStatus, string> = {
  ready: 'Ready',
  look: 'Needs a look',
  exists: 'Already in the CRM',
  duplicate: 'Duplicate',
  skipped: 'Skipped',
  added: 'Documents added',
  blocked: 'Will not be imported',
  waiting: 'Waiting',
  importing: 'Importing',
  imported: 'Imported',
  failed: 'Not imported',
}

/** The one status a candidate card shows: what happened to it if the import ran, otherwise what the check found. */
export function cardStatus(plan: Pick<ImportPlan, 'action' | 'duplicateOf' | 'needsLook'> & { alreadyInCrm?: unknown }, progress?: { status: RowProgress['status'] }): CardStatus {
  if (progress) {
    if (progress.status === 'done') return 'imported'
    if (progress.status === 'failed') return 'failed'
    if (progress.status === 'duplicate' || progress.status === 'skipped' || progress.status === 'added') return progress.status
    return progress.status === 'running' ? 'importing' : 'waiting'
  }
  if (plan.alreadyInCrm) return 'duplicate'
  if (plan.action === 'skip') return plan.duplicateOf ? 'exists' : 'blocked'
  return plan.needsLook ? 'look' : 'ready'
}

export type ListFilter = 'all' | 'attention' | 'ready' | 'imported'

export const FILTER_LABEL: Record<ListFilter, string> = { all: 'All', attention: 'Needs attention', ready: 'Ready', imported: 'Imported' }

/** "Needs attention" is everything that wants a person: a look, a failure, a duplicate or an error. */
export function inFilter(status: CardStatus, filter: ListFilter): boolean {
  if (filter === 'all') return true
  if (filter === 'attention') return status === 'look' || status === 'failed' || status === 'blocked' || status === 'exists' || status === 'duplicate'
  if (filter === 'ready') return status === 'ready' || status === 'waiting' || status === 'importing'
  return status === 'imported' || status === 'added'
}

export function filterCounts(statuses: CardStatus[]): Record<ListFilter, number> {
  const counts: Record<ListFilter, number> = { all: statuses.length, attention: 0, ready: 0, imported: 0 }
  for (const status of statuses) {
    if (inFilter(status, 'attention')) counts.attention += 1
    if (inFilter(status, 'ready')) counts.ready += 1
    if (inFilter(status, 'imported')) counts.imported += 1
  }
  return counts
}

/** True when the search words (any case) are all found in the candidate's name, folder, job, recruiter or file names. */
export function matchesQuery(plan: Pick<ImportPlan, 'name' | 'folder' | 'parent' | 'order' | 'recruiter' | 'files'>, query: string): boolean {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean)
  if (words.length === 0) return true
  const haystack = [plan.name, plan.folder, plan.parent, plan.order, plan.recruiter, ...plan.files.map((file) => file.path)].filter(Boolean).join(' ').toLowerCase()
  return words.every((word) => haystack.includes(word))
}

/** The items to list for a filter and a search box, in their original order. */
export function filterItems<T extends { plan: Pick<ImportPlan, 'name' | 'folder' | 'parent' | 'order' | 'recruiter' | 'files'>; status: CardStatus }>(items: T[], filter: ListFilter, query: string): T[] {
  return items.filter((item) => inFilter(item.status, filter) && matchesQuery(item.plan, query))
}

/** Filters and a search box only help when the list is long. */
export const TOOLBAR_FROM = 5
export const showToolbar = (count: number) => count > TOOLBAR_FROM

// ------------------------------------------------------------------------------ after the import: the result track

export type StageState = 'done' | 'active' | 'todo' | 'warn' | 'error'
export interface ResultStage { key: 'filed' | 'read' | 'details' | 'done'; label: string; state: StageState; note: string }

/** "Reading the documents: 2 of 3 done…", the sentence the old list showed; kept word for word. */
export function readingText(reading: ImportedStatus['reading']): string {
  return reading.total === 0 ? 'Documents stored. Read them with "Read the documents", or Extract details on the candidate\'s page.'
    : reading.open > 0 ? `Reading the documents: ${reading.done + reading.review + reading.failed} of ${reading.total} done…`
    : reading.failed > 0 ? `${reading.failed} of ${reading.total} documents could not be read. Use "Read the documents".`
    : reading.review > 0 ? `All ${reading.total} documents read. ${reading.review} need a look on the candidate's page.`
    : `All ${reading.total} documents read.`
}

/** Filed, Read, Details filled, Done: where an imported candidate is, from the status endpoint. Without a status everything waits. */
export function resultStages(status?: ImportedStatus): ResultStage[] {
  if (!status) {
    return [
      { key: 'filed', label: 'Filed', state: 'active', note: 'Loading…' },
      { key: 'read', label: 'Read', state: 'todo', note: '' },
      { key: 'details', label: 'Details filled', state: 'todo', note: '' },
      { key: 'done', label: 'Done', state: 'todo', note: '' },
    ]
  }
  const { reading } = status
  const filed = status.documents.length
  const finished = reading.done + reading.review + reading.failed
  const readState: StageState = reading.total === 0 ? 'todo' : reading.open > 0 ? 'active' : reading.failed > 0 ? 'error' : reading.review > 0 ? 'warn' : 'done'
  const readNote = reading.total === 0 ? 'Not read yet'
    : reading.open > 0 ? `Reading… ${finished} of ${reading.total} ${reading.total === 1 ? 'document' : 'documents'}`
    : reading.failed > 0 ? `${reading.failed} failed`
    : reading.review > 0 ? `${reading.review} to check`
    : `${reading.total} read`
  const details: StageState = status.detailsFound > 0 ? 'done' : readState === 'done' || readState === 'warn' ? 'warn' : 'todo'
  const everything = readState === 'done' && status.detailsFound > 0
  return [
    { key: 'filed', label: 'Filed', state: filed > 0 ? 'done' : 'todo', note: filed > 0 ? `${filed} ${filed === 1 ? 'document' : 'documents'}` : 'No documents' },
    { key: 'read', label: 'Read', state: readState, note: readNote },
    { key: 'details', label: 'Details filled', state: details, note: status.detailsFound > 0 ? `${status.detailsFound} found` : details === 'warn' ? 'None found' : '' },
    { key: 'done', label: 'Done', state: everything ? 'done' : 'todo', note: '' },
  ]
}

// ------------------------------------------------------------------------------ live reading state (bar and card chip)

/** The documents the online reader takes (the same four kinds as the server's SENT_TO_READER). */
const READABLE_SLOTS = new Set(['resume', 'passport', 'aadhaar', 'education_certificate'])

export type ReadPhase =
  | 'checking'   // the first answer of the status call has not come yet
  | 'nothing'    // nothing that can be read was filed
  | 'unread'     // filed, nothing read yet: "Read the documents"
  | 'starting'   // the button was pressed, the answer has not come yet
  | 'reading'    // jobs are queued or running
  | 'read'       // everything is read and nobody needs to look
  | 'review'     // everything is read, some need a look
  | 'partial'    // some were never read or failed: "Read again"

export interface ReadingSummary {
  phase: ReadPhase
  candidates: number
  /** Documents queued in all, and how many of them are finished (read, to check, or failed). */
  total: number
  finished: number
  /** Candidates that are fully read. */
  read: number
  /** Candidates whose reading failed or never happened (what "Read again" would do). */
  unread: string[]
  /** Candidates that were read but need a look on their page. */
  review: string[]
  detailsFound: number
  fraction: number
}

/**
 * Where the reading of a whole import stands, from the status of each imported candidate. `expected` is how many were imported (statuses
 * still to arrive keep it "checking"); `requesting` is true between pressing "Read the documents" and the server's answer, so the bar changes
 * at once. A candidate with a resume, passport, Aadhaar or education file and no reading job is "unread"; one with a failed job is too.
 */
export function readingSummary(statuses: Pick<ImportedStatus, 'name' | 'documents' | 'reading' | 'detailsFound'>[], expected: number, requesting = false): ReadingSummary {
  let total = 0
  let finished = 0
  let open = 0
  let read = 0
  let details = 0
  let readable = 0
  const unread: string[] = []
  const review: string[] = []
  for (const item of statuses) {
    const { reading } = item
    const canRead = item.documents.some((doc) => READABLE_SLOTS.has(doc.slot))
    total += reading.total
    finished += reading.done + reading.review + reading.failed
    open += reading.open
    details += item.detailsFound
    if (canRead || reading.total > 0) readable += 1
    if (reading.open > 0) continue
    if (reading.failed > 0 || (canRead && reading.total === 0)) unread.push(item.name)
    else if (reading.review > 0) { review.push(item.name); read += 1 }
    else if (reading.total > 0) read += 1
  }
  const base = { candidates: statuses.length, total, finished, read, unread, review, detailsFound: details, fraction: total > 0 ? Math.min(1, finished / total) : 0 }
  const phase: ReadPhase = requesting ? 'starting'
    : open > 0 ? 'reading'
    : statuses.length < expected ? 'checking'
    : readable === 0 ? 'nothing'
    : total === 0 ? 'unread'
    : unread.length > 0 ? 'partial'
    : review.length > 0 ? 'review'
    : 'read'
  return { ...base, phase }
}

const peopleList = (names: string[]) => (names.length <= 3 ? names.join(', ') : `${names.slice(0, 3).join(', ')} and ${names.length - 3} more`)

export interface ReadBar {
  headline: string
  detail: string
  tone: 'neutral' | 'busy' | 'done' | 'warn'
  /** 0-1 while reading; null when the length is not known yet; undefined when there is no bar. */
  progress?: number | null
  /** The "Read the documents" button: its words, or null when it must not be there (nothing to read, or a reading is queued or running). */
  readLabel: string | null
  /** "Open Data Bank" and "Import more" belong to a finished reading. */
  finished: boolean
}

/** The words and buttons of the bar once the import is done, from the reading summary. */
export function readBar(summary: ReadingSummary, imported: number): ReadBar {
  const who = imported === 1 ? '1 candidate' : `${imported} candidates`
  switch (summary.phase) {
    case 'checking': return { headline: `${who} imported`, detail: 'Checking what was filed…', tone: 'neutral', readLabel: null, finished: false }
    case 'nothing': return { headline: `${who} imported`, detail: 'Nothing to read: no resume, passport, Aadhaar or education files were filed.', tone: 'neutral', readLabel: null, finished: true }
    case 'unread': return { headline: `${who} imported, nothing read yet`, detail: 'Next: read the documents so the details fill in.', tone: 'neutral', readLabel: 'Read the documents', finished: false }
    case 'starting': return { headline: 'Reading the documents…', detail: 'Starting the reading.', tone: 'busy', progress: null, readLabel: null, finished: false }
    case 'reading': return {
      headline: `Reading the documents… ${summary.finished} of ${summary.total} read`,
      detail: 'The details fill in as each one is done. You can keep this page open and carry on.', tone: 'busy', progress: summary.fraction, readLabel: null, finished: false,
    }
    case 'read': return {
      headline: imported === 1 ? '1 candidate read · details filled' : `All ${imported} candidates read · details filled`,
      detail: summary.detailsFound > 0 ? `${summary.detailsFound} details filled in. Open a candidate to check them.` : 'All documents are read.', tone: 'done', readLabel: null, finished: true,
    }
    case 'review': return {
      headline: `${summary.read} read, ${summary.review.length} ${summary.review.length === 1 ? 'needs' : 'need'} a look`,
      detail: `Check the details of ${peopleList(summary.review)}.`, tone: 'warn', readLabel: null, finished: true,
    }
    default: return {
      headline: `${summary.read} read, ${summary.unread.length} not read`,
      detail: `Not read: ${peopleList(summary.unread)}.${summary.review.length > 0 ? ` Needs a look: ${peopleList(summary.review)}.` : ''}`,
      tone: 'warn', readLabel: `Read again (${summary.unread.length})`, finished: true,
    }
  }
}

/** The chip an imported candidate's card shows while and after its documents are read; null keeps the plain "Imported". */
export function readChip(status?: Pick<ImportedStatus, 'reading'>): { label: string; tone: 'navy' | 'green' | 'amber' } | null {
  if (!status || status.reading.total === 0) return null
  const { reading } = status
  if (reading.open > 0) return { label: 'Reading', tone: 'navy' }
  return reading.failed > 0 || reading.review > 0 ? { label: 'Needs a look', tone: 'amber' } : { label: 'Read', tone: 'green' }
}

/** "Updated just now", "Updated 12 s ago", "Updated 3 min ago". */
export function updatedAgo(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds))
  return s < 5 ? 'Updated just now' : s < 60 ? `Updated ${s} s ago` : `Updated ${Math.floor(s / 60)} min ago`
}

/**
 * When to ask for the status again: after 3 seconds while any document is queued or being read (or the reading was just requested), never when
 * everything is settled, and not at all while the tab is hidden (the screen asks at once when the tab is back). null means "do not poll".
 */
export function nextPoll(input: { open: number; requesting: boolean; hidden: boolean; round: number }): number | null {
  if (input.hidden) return null
  if (input.open === 0 && !input.requesting) return null
  return input.round <= 200 ? 3000 : 10000
}

export interface BatchReading { candidates: number; total: number; finished: number; open: number; failed: number; review: number; fraction: number; complete: boolean }

/** The whole batch's reading in one number: documents finished out of documents queued, over every imported candidate asked about. */
export function batchReading(statuses: Pick<ImportedStatus, 'reading'>[]): BatchReading {
  let total = 0
  let finished = 0
  let open = 0
  let failed = 0
  let review = 0
  for (const { reading } of statuses) {
    total += reading.total
    finished += reading.done + reading.review + reading.failed
    open += reading.open
    failed += reading.failed
    review += reading.review
  }
  return { candidates: statuses.length, total, finished, open, failed, review, fraction: total > 0 ? Math.min(1, finished / total) : 0, complete: statuses.length > 0 && total > 0 && open === 0 }
}

/**
 * Keeps the objects of candidates whose status did not change (so their cards are not drawn again on every poll) and swaps in the ones
 * that did. Returns `previous` itself when nothing changed at all.
 */
export function mergeStatuses(previous: Record<string, ImportedStatus>, items: ImportedStatus[]): Record<string, ImportedStatus> {
  let next: Record<string, ImportedStatus> | null = null
  for (const item of items) {
    const old = previous[item.candidateId]
    if (old && JSON.stringify(old) === JSON.stringify(item)) continue
    next = next ?? { ...previous }
    next[item.candidateId] = item
  }
  return next ?? previous
}

// ------------------------------------------------------------------------------ the sticky bar

export interface BarInput {
  /** The check is running (or the list is not there yet). */
  checking: boolean
  /** The files are being read to find each candidate. */
  reading: boolean
  importing: boolean
  /** Candidates ticked and importable now. */
  importable: number
  /** Different files those candidates hold. */
  documents: number
  /** Candidates imported so far, failed ones, and everyone who was started. */
  imported: number
  failed: number
  started: number
  /** Candidates that need a look and are not ticked. */
  unticked: number
  /** There are candidates in the list at all. */
  hasList: boolean
  /** Where the reading of the imported documents is: not asked for yet, going on, or finished. */
  readState?: 'none' | 'reading' | 'done'
  /** Duplicates are counted apart and never as imported: left alone (skipped), still waiting for a choice, and documents added to a candidate already in the CRM. */
  duplicatesSkipped?: number
  duplicatesWaiting?: number
  /** Found in the CRM by the check and left alone by default: never started, so not part of "Importing x of N". */
  duplicatesLeft?: number
  added?: number
}

export interface BarModel {
  phase: 'check' | 'importing' | 'imported'
  headline: string
  detail: string
  /** The import button: its words, and when it cannot be pressed, why in words. */
  importLabel: string
  importDisabled: boolean
  reason: string
}

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`

/** "1 duplicate skipped. 1 duplicate waiting for your choice. Documents added to 2 candidates already in the CRM." Empty when there are none. */
export function duplicateNote(input: Pick<BarInput, 'duplicatesSkipped' | 'duplicatesWaiting' | 'duplicatesLeft' | 'added'>): string {
  const parts: string[] = []
  const skipped = (input.duplicatesSkipped ?? 0) + (input.duplicatesLeft ?? 0)
  const waiting = input.duplicatesWaiting ?? 0
  const added = input.added ?? 0
  if (skipped > 0) parts.push(`${plural(skipped, 'duplicate')} skipped.`)
  if (waiting > 0) parts.push(`${plural(waiting, 'duplicate')} waiting for your choice.`)
  if (added > 0) parts.push(`Documents added to ${plural(added, 'candidate')} already in the CRM.`)
  return parts.join(' ')
}

/** Words for the bar at the bottom: what is ready, what the main button does, and why it cannot be pressed. */
export function barModel(input: BarInput): BarModel {
  const importLabel = `Import ${plural(input.importable, 'candidate')}`
  if (input.importing) {
    const settled = input.imported + input.failed + (input.duplicatesSkipped ?? 0) + (input.duplicatesWaiting ?? 0) + (input.added ?? 0)
    return { phase: 'importing', headline: `Importing ${settled} of ${input.started}`, detail: 'Each candidate is filed with all their documents. Keep this page open.', importLabel: `Importing… ${settled} of ${input.started}`, importDisabled: true, reason: '' }
  }
  if (input.started > 0) {
    const next = input.readState === 'reading' ? 'The documents are being read: the details fill in as each one is done.'
      : input.readState === 'done' ? 'All documents are read. Open a candidate to check the details.'
      : 'Next: read the documents so the details fill in.'
    const detail = [input.failed > 0 ? `${plural(input.failed, 'candidate')} could not be imported. Retry them, or leave them out.` : next, duplicateNote(input)].filter(Boolean).join(' ')
    return {
      phase: 'imported',
      headline: `${plural(input.imported, 'candidate')} imported`,
      detail,
      importLabel,
      importDisabled: input.importable === 0 || input.checking,
      reason: input.importable === 0 ? '' : input.checking ? 'Checking the files…' : `${plural(input.importable, 'more candidate')} still ticked.`,
    }
  }
  const headline = `${plural(input.importable, 'candidate')} · ${plural(input.documents, 'document')}`
  const detail = [input.unticked > 0 ? `${input.unticked} ${input.unticked === 1 ? 'needs' : 'need'} a look and ${input.unticked === 1 ? 'is' : 'are'} not ticked.` : 'Job and recruiter are optional.', duplicateNote(input)].filter(Boolean).join(' ')
  let reason = ''
  if (input.reading) reason = 'Wait for the files to finish being read, or cancel the reading.'
  else if (input.checking || !input.hasList) reason = 'Checking the files…'
  else if (input.importable === 0) reason = 'Tick at least one candidate to import.'
  return { phase: 'check', headline, detail, importLabel, importDisabled: reason !== '', reason }
}

// ------------------------------------------------------------------------------ candidates already in the CRM

/** Asked before a possible match (only the phone, e-mail or name matches) is imported as a new candidate. */
export const DUPLICATE_CONFIRM = 'This looks like the same person, only the phone or e-mail matches. Import anyway?'
/** Said under the documents added to a candidate who was already there. */
export const ADDED_KEPT_NOTE = 'The candidate’s stages, status and owner were not changed.'

/** What a card shows for a candidate that is already in the CRM (from the check, or from the import's answer). */
export interface DuplicateView {
  headline: string
  /** "CI-139": the Care India ID, else the reference. */
  careLabel: string
  candidateId: string | null
  name: string
  strength: MatchStrength | null
  matchedBy: string
  choices: DuplicateChoice[]
}

const CHOICE_ORDER: DuplicateChoice[] = ['skip', 'add_documents', 'import_anyway']

/**
 * The choices to offer for a duplicate: Skip (always, and the default), add the documents to the candidate already there, and importing as a new
 * candidate ONLY for a possible match. A certain match never offers it, whatever the server lists. `offered` is the server's list (all three when not given).
 */
export function duplicateChoices(strength: MatchStrength | null | undefined, offered?: DuplicateChoice[] | null): DuplicateChoice[] {
  const allowed = new Set<DuplicateChoice>(offered?.length ? offered : CHOICE_ORDER)
  allowed.add('skip')
  if (strength !== 'possible') allowed.delete('import_anyway')
  return CHOICE_ORDER.filter((choice) => allowed.has(choice))
}

/** The words on a duplicate's buttons. */
export function duplicateChoiceLabel(choice: DuplicateChoice, careLabel: string): string {
  if (choice === 'skip') return 'Skip'
  if (choice === 'add_documents') return `Add these documents to ${careLabel || 'this candidate'}`
  return 'Import as a new candidate anyway'
}

const careLabelOf = (found: { careId?: string | null; ref?: string | null }) => (found.careId || found.ref || '').trim()

function duplicateHeadline(label: string, name: string, strength: MatchStrength | null, matchedBy: string): string {
  const who = [label, name].filter(Boolean).join(' · ')
  const why = strength || matchedBy ? ` (${[strength, matchedBy ? `same ${matchedBy}` : ''].filter(Boolean).join(': ')})` : ''
  return `Already in the CRM as ${who || 'another candidate'}${why}`
}

/** The duplicate the import answered with ("status: duplicate": nothing was created), or null for any other answer. */
export function duplicateState(result: Pick<ImportResult, 'status' | 'message' | 'careId' | 'ref' | 'candidateId' | 'name' | 'strength' | 'matchedBy' | 'choices'> | undefined | null): DuplicateView | null {
  if (!result || result.status !== 'duplicate') return null
  const careLabel = careLabelOf(result)
  const strength = result.strength ?? null
  return {
    headline: result.message || duplicateHeadline(careLabel, result.name ?? '', strength, result.matchedBy ?? ''),
    careLabel, candidateId: result.candidateId ?? null, name: result.name ?? '', strength, matchedBy: result.matchedBy ?? '',
    choices: duplicateChoices(strength, result.choices),
  }
}

/** The duplicate the check found in a plan (`alreadyInCrm`), or null. */
export function planDuplicate(plan: Pick<ImportPlan, 'issues'> & { alreadyInCrm?: AlreadyInCrm | null }): DuplicateView | null {
  const found = plan.alreadyInCrm
  if (!found) return null
  const careLabel = careLabelOf(found)
  return {
    headline: splitIssues(plan.issues ?? []).existing || duplicateHeadline(careLabel, found.name, found.strength ?? null, found.matchedBy),
    careLabel, candidateId: found.candidateId ?? null, name: found.name, strength: found.strength ?? null, matchedBy: found.matchedBy,
    choices: duplicateChoices(found.strength),
  }
}

/** The documents an "add documents" answer filed, and the ones that were already there. */
export function addedDocuments(result: Pick<ImportResult, 'documents'>): { added: ResultDocument[]; already: ResultDocument[] } {
  const documents = result.documents ?? []
  return { added: documents.filter((doc) => doc.added), already: documents.filter((doc) => !doc.added && doc.alreadyFiled) }
}

/** "Documents added to CI-139", or what really happened when nothing new was filed. */
export function addedHeadline(result: Pick<ImportResult, 'documents' | 'careId' | 'ref'>): string {
  const label = careLabelOf(result) || 'the candidate'
  return addedDocuments(result).added.length > 0 ? `Documents added to ${label}` : `No new documents for ${label}`
}

/** A document added to a candidate that the online reader takes (resume, passport, Aadhaar, education): reading it fills in details. */
export function addedReadable(result: Pick<ImportResult, 'documents'> | undefined): boolean {
  return (result?.documents ?? []).some((doc) => doc.added && READABLE_SLOTS.has(doc.slot ?? ''))
}

/** How many pages the import saw again and did not file a second time. */
export function repeatedCount(duplicates: Pick<RepeatedPage, 'pages'>[] | undefined): number {
  return (duplicates ?? []).reduce((sum, item) => sum + (Array.isArray(item.pages) ? Math.max(1, item.pages.length) : 1), 0)
}

/** "3 repeated pages were not filed again". Empty when there were none. */
export function repeatedLine(duplicates: Pick<RepeatedPage, 'pages'>[] | undefined): string {
  const count = repeatedCount(duplicates)
  return count === 0 ? '' : `${count} repeated ${count === 1 ? 'page was' : 'pages were'} not filed again`
}

/** The originals are kept as they were uploaded when the server says so (the repeated pages stay in the original file). */
export const ORIGINALS_NOTE = 'The original files are kept as they were uploaded.'
export const keepsOriginals = (duplicates: Pick<RepeatedPage, 'originalKept'>[] | undefined) => (duplicates ?? []).some((item) => Boolean(item.originalKept))

/** What the card says for an answer of "already imported": the same import answered again, not a second candidate. */
export function alreadyImportedNote(result: Pick<ImportResult, 'status' | 'careId' | 'ref'> | undefined): string | null {
  return result?.status === 'already_imported' ? `Already imported as ${careLabelOf(result) || 'this candidate'}` : null
}

export interface RunCounts {
  started: number
  /** Imported (also "already imported"): each card counts once. */
  imported: number
  failed: number
  /** Duplicates a person left alone. */
  skipped: number
  /** Duplicates still waiting for a choice. */
  waiting: number
  /** Cards whose documents went to a candidate already in the CRM. */
  added: number
  /** Everything that has an answer (all but the ones still waiting or running for the server). */
  settled: number
}

/** The numbers the bottom bar shows after cards have been imported. Duplicates are never counted as imported. */
export function runCounts(entries: Pick<RowProgress, 'status'>[]): RunCounts {
  const count = (status: RowProgress['status']) => entries.filter((entry) => entry.status === status).length
  const imported = count('done')
  const failed = count('failed')
  const skipped = count('skipped')
  const waiting = count('duplicate')
  const added = count('added')
  return { started: entries.length, imported, failed, skipped, waiting, added, settled: imported + failed + skipped + waiting + added }
}

/**
 * The candidates to ask about while their documents are read: the ones imported, and the ones that got a resume, passport, Aadhaar or education file
 * added. A skipped or waiting duplicate (whose answer carries the OTHER candidate's id) is never asked about. Each candidate once.
 */
export function trackedIds(entries: Pick<RowProgress<Pick<ImportResult, 'candidateId' | 'documents'>>, 'status' | 'result'>[]): string[] {
  const ids: string[] = []
  for (const entry of entries) {
    const id = entry.result?.candidateId
    if (!id) continue
    if (entry.status === 'done' || (entry.status === 'added' && addedReadable(entry.result))) ids.push(id)
  }
  return [...new Set(ids)]
}

/** What a card becomes once the import answered: imported (also an "already imported" answer), a duplicate to choose about, documents added, or (an older refusal) not imported. */
export function progressFor<R extends Pick<ImportResult, 'status' | 'action' | 'issues'>>(result: R): RowProgress<R> {
  if (result.status === 'duplicate') return { status: 'duplicate', result }
  if (result.status === 'documents_added') return { status: 'added', result }
  if (result.status === 'imported' && result.action === 'skip') return { status: 'failed', result, error: result.issues[0]?.message ?? 'Not imported.' }
  return { status: 'done', result }
}

/** True when this card goes in "Import N candidates": not left out, not already answered, and (for a duplicate) a person chose something other than Skip. */
export function isImportable(plan: Pick<ImportPlan, 'action'> & { alreadyInCrm?: unknown }, state: { left: boolean; progress?: Pick<RowProgress, 'status'>; choice?: DuplicateChoice }): boolean {
  const answered = state.progress && ['done', 'duplicate', 'skipped', 'added'].includes(state.progress.status)
  if (answered || state.left) return false
  if (plan.alreadyInCrm) return (state.choice ?? 'skip') !== 'skip'
  return plan.action !== 'skip'
}

/** Checked cards that were found in the CRM and are left alone by default: counted as "N duplicates skipped" before anything runs. */
export function plansSkippedAsDuplicates(plans: (Pick<ImportPlan, 'folder'> & { alreadyInCrm?: unknown })[], choices: Record<string, DuplicateChoice>, progress: Record<string, unknown>): number {
  return plans.filter((plan) => plan.alreadyInCrm && !progress[plan.folder] && (choices[plan.folder] ?? 'skip') === 'skip').length
}
