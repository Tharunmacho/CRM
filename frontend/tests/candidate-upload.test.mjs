import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const upload = await loadSource('../src/features/candidates/entry-upload.ts')
const { fileRow, fileRows, foundChips, readProgress, queueText, pagesText, sizeText, findingsNames, documentPresence, severalPeopleFound, jobFindings, isDocumentJob,
  trackNextInLine, readingStatus, elapsedText, SLOW_READ_MS, NEXT_IN_LINE_MS, NO_READER_INFO_MS, READER_DOWN, STILL_WAITING, safeHint,
  sortingLine, sortingOf, foundLines, unplacedPages, isSorting } = upload

// Synthetic jobs only: no real names, numbers or documents.
let counter = 0
const job = (over = {}) => ({
  id: `job-${++counter}`, groupId: 'g', kind: 'resume', filename: `file-${counter}.pdf`, sizeKb: 200, status: 'uploaded', lastError: null,
  candidateId: null, attachmentId: null, originalUrl: '/x', createdAt: `2026-01-01T00:00:${String(counter).padStart(2, '0')}Z`, updatedAt: '2026-01-01T00:00:00Z', ...over,
})
const data = { raw: { text: 'Synthetic' }, fields: {}, warnings: [], extraction_status: 'complete' }
const scan = (pages, extra = {}) => ({ ...data, inspection: { page_count: 6, resume_pages: [], unread_pages: [], unknown_pages: [], pages: [], warnings: [], selected_pages: pages }, ...extra })

// A moment after the files were queued (their createdAt is early January 2026): too soon for an older server's silence to mean anything.
const early = { now: Date.parse('2026-01-01T00:00:30Z') }
const alive = { alive: true, workers: 1, busy: 0, lastSeenSeconds: 2, outdated: false }
const dead = { alive: false, workers: 0, busy: 0, lastSeenSeconds: null, outdated: false }

test('a queued file says how many are ahead of it, then Reading, then Done', () => {
  assert.equal(fileRow(job({ status: 'pending', aheadOfYou: 3, queuePosition: 4 }), early).label, 'Waiting, 3 ahead of you')
  assert.equal(fileRow(job({ status: 'pending', aheadOfYou: 1 }), early).label, 'Waiting, 1 ahead of you')
  assert.equal(fileRow(job({ status: 'retry', aheadOfYou: 0, reader: alive }), early).label, 'Next in line')
  // Without word from the reader, being first in line is just "Queued" (an older server cannot promise anything).
  assert.equal(fileRow(job({ status: 'retry', aheadOfYou: 0 }), early).label, 'Queued')
  assert.equal(fileRow(job({ status: 'pending' }), early).label, 'Queued')
  assert.equal(fileRow(job({ status: 'uploaded' }), early).label, 'Waiting to read')
  assert.equal(queueText({ queuePosition: 5 }), 'Waiting, 4 ahead of you')
  assert.equal(queueText({ aheadOfYou: null, queuePosition: null }), 'Queued')
  const reading = fileRow(job({ status: 'processing' }))
  assert.deepEqual([reading.state, reading.label, reading.tone], ['reading', 'Reading', 'busy'])
  const done = fileRow(job({ status: 'completed', result: { scans: { resume: scan([1, 2]) } } }))
  assert.deepEqual([done.state, done.label, done.tone], ['done', 'Done', 'good'])
})

test('a failed file offers a retry and says the file is saved; one with nothing readable needs a look', () => {
  const failed = fileRow(job({ status: 'failed', lastError: 'The reader was not available.' }))
  assert.deepEqual([failed.state, failed.label, failed.canRetry, failed.note], ['failed', 'Reading failed', true, 'Reading failed. The file is saved.'])
  assert.equal(fileRow(job({ status: 'failed' })).note, 'Reading failed. The file is saved.')
  const empty = fileRow(job({ status: 'completed', result: { scans: { resume: { ...data, raw: {}, extraction_status: 'no_resume' } } } }))
  assert.deepEqual([empty.state, empty.label, empty.canRetry], ['review', 'Needs a look', true])
  const review = fileRow(job({ status: 'needs_review' }))
  assert.equal(review.label, 'Needs a look')
  assert.equal(fileRow(job({ status: 'attached' })).label, 'Attached')
  // An older response with no result at all is not flagged.
  assert.equal(fileRow(job({ status: 'completed' })).label, 'Done')
})

test('one file with a resume, a passport and an Aadhaar card lights the chips one by one', () => {
  const file = (result, status) => job({ status, result })
  // 1. Queued: nothing found yet; all three are muted and unrequired.
  let chips = foundChips([file(undefined, 'pending')])
  assert.deepEqual(chips.map((chip) => [chip.kind, chip.state, chip.label]), [['resume', 'idle', 'Resume'], ['passport', 'idle', 'Passport'], ['aadhaar', 'idle', 'Aadhaar']])
  // 2. Classified, reading: pages are known, so the kinds are found but not read yet.
  const classified = { inspection: { page_count: 6, resume_pages: [1, 2], passport_pages: [4], aadhaar_pages: [5, 6], unread_pages: [], unknown_pages: [3], pages: [], warnings: [] } }
  chips = foundChips([file(classified, 'processing')])
  assert.deepEqual(chips.map((chip) => [chip.state, chip.label, chip.detail]), [['reading', 'Resume', '2 pages'], ['reading', 'Passport', '1 page'], ['reading', 'Aadhaar', '2 pages']])
  // 3. The resume is read first (partial result): it turns done, the others keep reading.
  chips = foundChips([file({ ...classified, scans: { resume: scan([1, 2]) } }, 'processing')])
  assert.deepEqual(chips.map((chip) => chip.state), ['done', 'reading', 'reading'])
  // 4. Everything read.
  chips = foundChips([file({ ...classified, scans: { resume: scan([1, 2]), passport: scan([4]), aadhaar: scan([5, 6]) } }, 'completed')])
  assert.deepEqual(chips.map((chip) => chip.state), ['done', 'done', 'done'])
  assert.equal(chips[1].detail, '1 page')
})

test('a found kind that could not be read asks for a look; a resume with no pages is not found', () => {
  const unreadable = job({ status: 'completed', result: { scans: { passport: scan([4], { raw: {}, extraction_status: 'needs_review' }), resume: { ...data, raw: {}, extraction_status: 'no_resume', inspection: { selected_pages: [] } } } } })
  const chips = foundChips([unreadable])
  assert.deepEqual(chips.map((chip) => [chip.kind, chip.state]), [['resume', 'idle'], ['passport', 'review'], ['aadhaar', 'idle']])
  assert.equal(chips[1].label, 'Passport needs a look')
})

test('several files: pages are merged per kind, more than one file shows a count, and the legacy one-kind jobs still count', () => {
  const a = job({ kind: 'resume', status: 'completed', result: { scans: { resume: scan([1]) } } })
  const b = job({ kind: 'resume', status: 'completed', result: { scans: { resume: scan([1, 2]), passport: scan([3]) } } })
  const legacy = job({ kind: 'aadhaar', status: 'completed', result: { scans: { aadhaar: { ...data, inspection: undefined } } } })
  const chips = foundChips([a, b, legacy])
  assert.deepEqual(chips.map((chip) => [chip.kind, chip.state, chip.detail]), [['resume', 'done', '3 pages · 2 files'], ['passport', 'done', '1 page'], ['aadhaar', 'done', '']])
  assert.deepEqual(jobFindings(b).map((f) => [f.kind, f.pages, f.read]), [['resume', [1, 2], true], ['passport', [3], true]])
})

test('the photo and the certificates never appear in the documents zone', () => {
  const rows = fileRows([job({ kind: 'photo' }), job({ kind: 'education' }), job({ kind: 'resume' }), job({ kind: 'resume', status: 'dismissed' })])
  assert.equal(rows.length, 1)
  assert.equal(isDocumentJob({ kind: 'experience', status: 'uploaded' }), false)
})

test('rows keep the order the files were added, however the server lists them', () => {
  const first = job({ filename: 'first.pdf' })
  const second = job({ filename: 'second.pdf' })
  const third = job({ filename: 'third.pdf' })
  assert.deepEqual(fileRows([third, first, second]).map((row) => row.name), ['first.pdf', 'second.pdf', 'third.pdf'])
  // A status change does not move a row.
  assert.deepEqual(fileRows([third, { ...first, status: 'completed' }, second]).map((row) => row.name), ['first.pdf', 'second.pdf', 'third.pdf'])
})

test('twelve files: overall progress says N of M read and counts failures', () => {
  const jobs = Array.from({ length: 12 }, (_, index) => job({ status: index < 5 ? 'completed' : index === 5 ? 'failed' : index === 6 ? 'processing' : 'pending', result: index < 5 ? { scans: { resume: scan([1]) } } : undefined }))
  const progress = readProgress(fileRows(jobs))
  assert.deepEqual([progress.total, progress.finished, progress.working, progress.failed, progress.percent, progress.text], [12, 6, 6, 1, 50, '6 of 12 read'])
  assert.equal(readProgress([]).percent, 0)
})

test('page, size and file texts', () => {
  assert.equal(pagesText([4]), 'page 4')
  assert.equal(pagesText([2, 1]), 'pages 1, 2')
  assert.equal(pagesText([1, 2, 3, 5]), 'pages 1–3, 5')
  assert.equal(pagesText([]), '')
  assert.equal(sizeText(0), '1 KB')
  assert.equal(sizeText(245), '245 KB')
  assert.equal(sizeText(1536), '1.5 MB')
  assert.equal(sizeText(2048), '2 MB')
  assert.equal(findingsNames([{ kind: 'resume', pages: [], read: true }, { kind: 'passport', pages: [4], read: true }]), 'Resume · Passport')
})

test('the progress strip counts a kind once found, or while a file of that kind is still unread', () => {
  assert.deepEqual(documentPresence([]), { resume: false, passport: false, aadhaar: false })
  assert.deepEqual(documentPresence([job({ kind: 'resume', status: 'processing' })]), { resume: true, passport: false, aadhaar: false })
  const done = job({ status: 'completed', result: { scans: { resume: scan([1]), passport: scan([2]) } } })
  assert.deepEqual(documentPresence([done]), { resume: true, passport: true, aadhaar: false })
  const nothing = job({ status: 'completed', result: { scans: { resume: { ...data, raw: {}, extraction_status: 'no_resume' } } } })
  assert.equal(documentPresence([nothing]).resume, false)
})

test('Check people in documents shows only when the server finds more than one person', () => {
  assert.equal(severalPeopleFound([job({ status: 'completed', result: { scans: { resume: scan([1]) } } })]), false)
  assert.equal(severalPeopleFound([job({ status: 'needs_review', lastError: 'Review and confirm the people and pages in this intake.' })]), true)
  assert.equal(severalPeopleFound([job({ status: 'completed', result: { warnings: ['This document contains different candidate identities.'] } })]), true)
  assert.equal(severalPeopleFound([job({ status: 'completed', result: { peopleCount: 2 } })]), true)
  assert.equal(severalPeopleFound([job()], true), true)
})

test('the page sends every file as kind resume without replacing the others, and uses the one-second poll', async () => {
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  assert.match(page, /intake\.upload\('resume', file, \{ many: true \}\)/)
  assert.match(page, /title="Upload documents">/)
  assert.doesNotMatch(page, /Drop all of this candidate's files together/, 'no explanatory line under the section title')
  assert.doesNotMatch(page, /More documents \(optional\)|Extract details|choose Extract|MultiFileSlot|<FileDrop/)
  assert.doesNotMatch(page, /Kept on the profile when you save/)
  assert.doesNotMatch(page, /Aadhaar copy'/)
  assert.match(page, /Check people in documents/)
  assert.match(page, /Stop waiting/)
  const hook = await readFile(new URL('../src/features/candidates/use-document-intake.ts', import.meta.url), 'utf8')
  assert.match(hook, /READ_POLL_MS = 1000/)
  assert.match(hook, /form\.append\('kind', kind\)/)
})

test('the Add candidate page has no Saved uploads card and never lists or reopens earlier uploads', async () => {
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  const hook = await readFile(new URL('../src/features/candidates/use-document-intake.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(page, /IntakeRecovery|RecoveryDialog|Saved uploads|Unfinished uploads|Continue this upload|Remove from list/)
  assert.doesNotMatch(hook, /available|recover|dismiss = |api\.get<IntakeJob\[\]>\('\/ocr\/jobs'\)/)
  assert.match(page, /Try saving the form again/)
  assert.match(hook, /\/ocr\/jobs\/groups\/' \+ group\.current/)
})

// ------------------------------------------------------------------ an honest queue: the reader may not be running

const T0 = Date.parse('2026-01-01T00:10:00Z')
const queued = (over = {}) => job({ status: 'pending', queuedAt: '2026-01-01T00:09:59Z', createdAt: '2026-01-01T00:09:59Z', ...over })

test('a live reader: "Waiting, N ahead of you" with no warning', () => {
  const row = fileRow(queued({ aheadOfYou: 2, reader: alive, waitingReason: 'queue' }), { now: T0 })
  assert.deepEqual([row.label, row.tone, row.stalled, row.canCheckAgain, row.note], ['Waiting, 2 ahead of you', 'muted', null, false, ''])
})

test('reader.alive false: an amber message and a Check again button, even right away', () => {
  const row = fileRow(queued({ aheadOfYou: 0, reader: dead }), { now: T0 })
  assert.deepEqual([row.state, row.tone, row.stalled, row.canCheckAgain, row.note], ['queued', 'warn', 'reader', true, READER_DOWN])
  assert.equal(READER_DOWN, 'The reading service is not running. Ask your administrator to start it.')
  assert.notEqual(row.label, 'Next in line')
})

test('waitingReason reader_not_running alone is enough (a reader key may be missing)', () => {
  const row = fileRow(queued({ aheadOfYou: 3, waitingReason: 'reader_not_running' }), { now: T0 })
  assert.deepEqual([row.stalled, row.canCheckAgain, row.note], ['reader', true, READER_DOWN])
  const retrying = fileRow(queued({ status: 'retry', reader: alive, waitingReason: 'reader_not_running' }), { now: T0 })
  assert.equal(retrying.stalled, 'reader')
})

test('a retry says reading did not finish and that it is being tried again (attempts 1, 2, 3), never to restart the server', () => {
  const at = { now: Date.parse('2026-01-01T01:00:00Z') }
  for (const [attempts, n] of [[1, 2], [2, 3], [3, 4]]) {
    const row = fileRow(job({ status: 'retry', attempts, reader: alive, lastError: 'Traceback (most recent call last): InspectionError at /srv/app.py:12' }), at)
    assert.deepEqual([row.state, row.label, row.tone, row.stalled, row.canCheckAgain], ['queued', 'Trying again', 'warn', null, false])
    assert.equal(row.note, `Reading did not finish. Trying again (attempt ${n} of 4).`)
    assert.deepEqual([row.canRetry, row.retryLabel, row.canEnterManually, row.hint], [true, 'Try now', false, ''])
    assert.doesNotMatch(JSON.stringify(row), /restart|Traceback|InspectionError|srv/)
  }
  // Even with no reader information at all (an older server) a retry after a failed attempt is not "Still waiting".
  const noReader = fileRow(job({ status: 'retry', attempts: 1 }), at)
  assert.deepEqual([noReader.label, noReader.stalled, noReader.canCheckAgain], ['Trying again', null, false])
  assert.equal(noReader.note, 'Reading did not finish. Trying again (attempt 2 of 4).')
  // A dead reader is still said out loud, in the same row, without the restart wording.
  const down = fileRow(job({ status: 'retry', attempts: 2, reader: dead }), at)
  assert.deepEqual([down.stalled, down.canCheckAgain, down.retryLabel], ['reader', true, 'Try now'])
  assert.equal(down.note, `Reading did not finish. Trying again (attempt 3 of 4). ${READER_DOWN}`)
})

test('the extended JSON: maxAttempts and a safe hint are used, unsafe hints are dropped, the old JSON still works', () => {
  const row = fileRow(job({ status: 'retry', attempts: 5, maxAttempts: 6, reader: alive, lastErrorHint: 'The scan was too blurry to read' }))
  assert.equal(row.note, 'Reading did not finish. Trying again (attempt 6 of 6).')
  assert.equal(row.hint, 'The scan was too blurry to read.')
  assert.equal(fileRow(job({ status: 'retry', attempts: 9, reader: alive })).note, 'Reading did not finish. Trying again (attempt 4 of 4).')   // capped, old JSON
  for (const bad of ['Traceback (most recent call last)', 'ValueError: boom', 'see C:/srv/x.log', '/var/log/app.log', 'job 3f2b8c1e-0000-4000-8000-123456789abc failed', 'x'.repeat(200), '', null, 7]) assert.equal(safeHint(bad), '', String(bad))
  assert.equal(fileRow(job({ status: 'retry', attempts: 1, reader: alive, lastErrorHint: 'ValueError: boom' })).hint, '')
})

test('a failed file: red row, "The file is saved", Try again and Enter details manually, no raw error text', () => {
  const row = fileRow(job({ status: 'failed', attempts: 4, lastError: 'Traceback (most recent call last): boom', lastErrorHint: 'The file has no readable pages' }))
  assert.deepEqual([row.state, row.label, row.tone, row.note], ['failed', 'Reading failed', 'bad', 'Reading failed. The file is saved.'])
  assert.deepEqual([row.canRetry, row.retryLabel, row.canEnterManually, row.hint], [true, 'Try again', true, 'The file has no readable pages.'])
  assert.doesNotMatch(JSON.stringify(row), /Traceback|boom/)
  const old = fileRow(job({ status: 'failed', lastError: 'The reader was not available.' }))
  assert.deepEqual([old.note, old.hint], ['Reading failed. The file is saved.', ''])
})

test('a file being read shows Reading and nothing about waiting', () => {
  const row = fileRow(job({ status: 'processing', attempts: 2, reader: alive, aheadOfYou: 3 }), early)
  assert.deepEqual([row.state, row.label, row.tone, row.note, row.stalled, row.canCheckAgain, row.retryLabel], ['reading', 'Reading', 'busy', '', null, false, ''])
})

test('queued with no reader information: Queued at 10 s, the soft hint after 45 s only if never tried', () => {
  const queuedAt = Date.parse('2026-01-01T00:09:59Z')
  assert.deepEqual([fileRow(queued({ aheadOfYou: 0 }), { now: queuedAt + 10_000 }).label, fileRow(queued({ attempts: 0 }), { now: queuedAt + 10_000 }).note], ['Queued', ''])
  assert.equal(fileRow(queued({ attempts: 0 }), { now: queuedAt + NO_READER_INFO_MS + 1 }).note, STILL_WAITING)
  assert.equal(fileRow(queued({ attempts: 1 }), { now: queuedAt + NO_READER_INFO_MS + 1 }).stalled, null)
  assert.equal(fileRow(queued({ attempts: 0, reader: alive, aheadOfYou: 2 }), { now: queuedAt + 120_000 }).stalled, null)
})

test('an older server (no reader key): quiet for 45 s, then it says it is still waiting', () => {
  const soon = fileRow(queued({ aheadOfYou: 0 }), { now: T0 - 20_000 })
  assert.deepEqual([soon.label, soon.stalled, soon.note], ['Queued', null, ''])
  assert.equal(fileRow(queued({ aheadOfYou: 4 }), { now: T0 - 20_000 }).label, 'Waiting, 4 ahead of you')
  const queuedAt = Date.parse('2026-01-01T00:09:59Z')
  assert.equal(fileRow(queued(), { now: queuedAt + NO_READER_INFO_MS }).stalled, null)
  const late = fileRow(queued({ aheadOfYou: 0 }), { now: queuedAt + NO_READER_INFO_MS + 1 })
  assert.deepEqual([late.label, late.tone, late.stalled, late.canCheckAgain, late.note], ['Still waiting', 'warn', 'unknown', true, STILL_WAITING])
  assert.equal(STILL_WAITING, 'Taking longer than usual. Press Check again.')
  assert.doesNotMatch(STILL_WAITING, /restart/)
})

test('"Next in line" lasts at most 20 seconds, then "Starting…"', () => {
  const next = queued({ aheadOfYou: 0, reader: alive })
  let seen = trackNextInLine({}, [next], T0)
  assert.deepEqual(seen, { [next.id]: T0 })
  assert.equal(fileRow(next, { now: T0 + 5_000, nextSince: seen }).label, 'Next in line')
  assert.equal(fileRow(next, { now: T0 + NEXT_IN_LINE_MS, nextSince: seen }).label, 'Next in line')
  assert.equal(fileRow(next, { now: T0 + NEXT_IN_LINE_MS + 1, nextSince: seen }).label, 'Starting…')
  // The start time is kept while the file stays next, and forgotten when it moves back or is being read.
  assert.deepEqual(trackNextInLine(seen, [next], T0 + 9_000), seen)
  assert.deepEqual(trackNextInLine(seen, [{ ...next, aheadOfYou: 2 }], T0 + 9_000), {})
  assert.deepEqual(trackNextInLine(seen, [{ ...next, status: 'processing' }], T0 + 9_000), {})
  assert.deepEqual(trackNextInLine(seen, [{ ...next, reader: dead }], T0 + 9_000), {})
  assert.deepEqual(trackNextInLine({}, [queued({ aheadOfYou: 0 })], T0), {})      // no reader information: never "next in line"
})

test('an old reader is named without raising the alarm', () => {
  const row = fileRow(queued({ aheadOfYou: 2, reader: { ...alive, outdated: true } }), { now: T0 })
  assert.match(row.note, /older version/)
  assert.deepEqual([row.stalled, row.canCheckAgain, row.label], [null, false, 'Waiting, 2 ahead of you'])
})

test('files that are being read, finished or failed never carry the reader warning', () => {
  for (const status of ['processing', 'completed', 'failed', 'needs_review', 'attached']) {
    const row = fileRow(job({ status, reader: dead, waitingReason: 'reader_not_running' }), { now: T0 })
    assert.deepEqual([row.stalled, row.canCheckAgain], [null, false], status)
  }
  assert.equal(fileRow(job({ status: 'uploaded', reader: dead }), { now: T0 }).label, 'Waiting to read') // not queued for reading yet
})

test('fileRows passes the clock to every row and keeps their order', () => {
  const rows = fileRows([queued({ aheadOfYou: 1, reader: dead }), queued({ aheadOfYou: 0, reader: alive })], { now: T0 })
  assert.deepEqual(rows.map((row) => row.stalled), ['reader', null])
})

test('polling waits for the previous answer, then READ_POLL_MS, and sends nothing while the tab is hidden', async () => {
  const hook = await readFile(new URL('../src/features/candidates/use-document-intake.ts', import.meta.url), 'utf8')
  assert.doesNotMatch(hook, /setInterval\([^)]*groups/)
  const loop = hook.slice(hook.indexOf('const read = async'), hook.indexOf('const refresh = async'))
  // One awaited request per round, then the pause, then (only while visible) the next round.
  assert.ok(loop.indexOf("await api.get<IntakeJob[]>('/ocr/jobs/groups/'") < loop.indexOf('await pause(signal)'))
  assert.ok(loop.indexOf('await pause(signal)') < loop.indexOf('await whenVisible(signal)'))
  assert.match(hook, /document\.visibilityState !== 'hidden'/)
  assert.match(hook, /addEventListener\('visibilitychange', changed\)/)
})

// ---------------------------------------------------------------------------------- every other kind found in a file

const slots = await loadSource('../src/shared/model/document-slots.ts')

test('a chip is added for every other kind found in the upload, with its page count, after the three that are always listed', () => {
  const combined = job({ status: 'completed', result: {
    scans: { resume: scan([1, 2]), passport: scan([3]) },
    kinds: { medical: [7], education_certificate: [5, 6], resume: [1, 2], passport: [3], experience_certificate: [4], other: [8] },
  } })
  const chips = foundChips([combined])
  // the extra chips follow one fixed order (education, experience, medical ... other), not the order of the pages
  assert.deepEqual(chips.map((chip) => chip.kind), ['resume', 'passport', 'aadhaar', 'education_certificate', 'experience_certificate', 'medical', 'other'])
  assert.deepEqual(chips.slice(3).map((chip) => [chip.state, chip.label, chip.detail, chip.extra]), [
    ['done', 'Education certificates', '2 pages', true], ['done', 'Experience certificates', '1 page', true],
    ['done', 'Medical', '1 page', true], ['done', 'Other', '1 page', true]])
  assert.deepEqual(chips.slice(0, 3).map((chip) => [chip.state, chip.label, chip.detail, chip.extra]), [['done', 'Resume', '2 pages', undefined], ['done', 'Passport', '1 page', undefined], ['idle', 'Aadhaar', '', undefined]])
  assert.deepEqual(chips[1].pages, [3])                    // the identity chips still come from the scans, not from `kinds`
  assert.equal(chips[3].pageCount, 2)
})

test('every kind the owner lists has its own plain chip name, in one fixed order', () => {
  const everything = job({ status: 'completed', result: { scans: { resume: scan([1]), passport: scan([2, 3]), aadhaar: scan([4]) }, kinds: {
    other: [14], photo: [13], pcc: [12], visa: [11], signed_offer: [10], medical: [9], experience_certificate: [8], education_certificate: [5, 6, 7],
  } } })
  assert.deepEqual(foundChips([everything]).map((chip) => [chip.label, chip.detail]), [
    ['Resume', '1 page'], ['Passport', '2 pages'], ['Aadhaar', '1 page'], ['Education certificates', '3 pages'], ['Experience certificates', '1 page'],
    ['Medical', '1 page'], ['Signed offer', '1 page'], ['Visa', '1 page'], ['Police clearance', '1 page'], ['Photo', '1 page'], ['Other', '1 page']])
  assert.equal(upload.chipLabel('google_review'), 'Google review')   // a kind with no name of its own is still readable
  // a kind the server names that this screen does not know sorts before "Other"
  const odd = job({ status: 'completed', result: { scans: {}, kinds: { other: [1], insurance: [2], medical: [3] } } })
  assert.deepEqual(foundChips([odd]).slice(3).map((chip) => chip.kind), ['medical', 'insurance', 'other'])
})

test('without kinds nothing changes: exactly the three chips, and the same file in two files adds up its pages', () => {
  assert.equal(foundChips([job({ status: 'completed', result: { scans: { resume: scan([1]) } } })]).length, 3)
  assert.equal(foundChips([job({ status: 'completed', result: { scans: {}, kinds: {} } })]).length, 3)
  const a = job({ status: 'completed', result: { scans: {}, kinds: { visa: [2] } } })
  const b = job({ status: 'completed', result: { scans: {}, kinds: { visa: [1, 2] } } })
  const chips = foundChips([a, b])
  assert.deepEqual(chips.slice(3).map((chip) => [chip.kind, chip.label, chip.detail, chip.pages]), [['visa', 'Visa', '3 pages · 2 files', [1, 2]]])
  // damaged or hostile values are ignored, never shown
  const odd = job({ status: 'completed', result: { scans: {}, kinds: { 'x<b>': [1], medical: 'all', visa: [-0, 'two'], pcc: [3] } } })
  assert.deepEqual(foundChips([odd]).slice(3).map((chip) => [chip.kind, chip.label]), [['visa', 'Visa'], ['pcc', 'Police clearance']])
})

test('a file that holds only filed-only kinds is Done, not "Needs a look", and presence still lists the three kinds only', () => {
  const only = job({ status: 'completed', result: { scans: {}, kinds: { medical: [1, 2] } } })
  const row = fileRow(only)
  assert.deepEqual([row.state, row.label], ['done', 'Done'])
  assert.deepEqual(documentPresence([only]), { resume: false, passport: false, aadhaar: false })
  assert.deepEqual(upload.extraKinds(only), [{ kind: 'medical', pages: [1, 2] }])
  assert.deepEqual(upload.extraKinds(job({ status: 'completed' })), [])
  // a file with nothing found at all still needs a look
  assert.equal(fileRow(job({ status: 'completed', result: { scans: {} } })).label, 'Needs a look')
})

test('there is no Extract step: saving the dropped files starts the reading, new files are read after the running round, an error is never retried in a loop', async () => {
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  const add = page.slice(page.indexOf('const addDocuments = async'), page.indexOf('const removeDocument'))
  // after every file is saved (and the draft is), the screen asks for the reading itself
  assert.ok(add.indexOf('await intake.saveDraft') < add.indexOf('readDocumentsRef.current({ retryFailed: false })'))
  assert.match(add, /if \(intake\.hasUnqueued\(\)\) void readDocumentsRef\.current/)
  const read = page.slice(page.indexOf('const readDocuments = async'), page.indexOf('const cancelReading'))
  // a file dropped while a round runs is picked up when the round ends, but never after an error or a stop
  assert.match(read, /if \(!failed && !controller\.signal\.aborted && intake\.hasUnqueued\(\)\) void readDocumentsRef\.current\(\{ retryFailed: false \}\)/)
  assert.match(read, /intake\.latest\(\)/)           // never the render-old list: the files were added a moment ago
  assert.match(read, /setReadError\(/)
  // progress and failure are said plainly; the only button is Try again, shown after a failure
  assert.match(page, /Reading documents… You can leave this page/)
  assert.match(page, /\{!reading && \(readError \|\| failedFiles > 0\) && <Button onClick=\{\(\) => void readDocuments\(\{ retryFailed: true \}\)\}[^>]*icon=\{<RotateCw className="h-4 w-4" \/>\}>Try again<\/Button>\}/)
  assert.doesNotMatch(page, /Read again|Sparkles/)
  // the photo, education and experience cards are gone: one drop zone holds everything
  assert.doesNotMatch(page, /Education certificates|Experience certificates|slot-photo|ac-slots/)
  assert.equal((page.match(/<EntryUploadZone/g) ?? []).length, 1)
})

test('the zone lists a chip only for a kind that was found, under one heading, and shows no explanatory line while it is empty', async () => {
  const zone = await readFile(new URL('../src/features/candidates/components/entry-upload-zone.tsx', import.meta.url), 'utf8')
  assert.match(zone, /chips\.filter\(\(chip\) => chip\.state !== 'idle'\)/)
  assert.match(zone, /found\.length > 0 && \(/)
  assert.match(zone, /aria-labelledby="uz-chips-head"/)
  assert.doesNotMatch(zone, /uz-hint/)
})

test('the reading strip says where the work really is: saved, waiting for the reader, sorting the pages, then reading what was found', () => {
  const start = Date.parse('2026-01-01T00:00:00Z')
  assert.deepEqual(readingStatus([]), { steps: [], text: '' })
  const waiting = readingStatus([job({ status: 'pending', reader: alive })], start)
  assert.equal(waiting.text, 'Saved. Waiting for the reader to pick it up…')
  assert.deepEqual(waiting.steps.map((step) => [step.key, step.state]), [['saved', 'done'], ['line', 'active'], ['sorting', 'todo'], ['reading', 'todo']])
  assert.equal(readingStatus([job({ status: 'pending', reader: dead })], start).text, `Saved. ${READER_DOWN}`)
  // The worker has the file and has not found any page yet: the pages are being sorted, with the time spent.
  const sorting = readingStatus([job({ status: 'processing', startedAt: '2026-01-01T00:00:00Z' })], start + 45_000)
  assert.equal(sorting.text, 'Sorting the pages by what is on them… 45 s.')
  assert.deepEqual(sorting.steps.map((step) => [step.key, step.state]), [['saved', 'done'], ['line', 'done'], ['sorting', 'active'], ['reading', 'todo']])
  // A long scan is told honestly, still working.
  assert.match(readingStatus([job({ status: 'processing', startedAt: '2026-01-01T00:00:00Z' })], start + SLOW_READ_MS + 30_000).text, /2 min\.? .*a few minutes|2 min/)
  // The pages are sorted (kinds found while the reader works): the chips are named and the step moves on.
  const found = readingStatus([job({ status: 'processing', startedAt: '2026-01-01T00:00:00Z', result: { scans: { resume: scan([1], { extraction_status: 'needs_review', raw: {} }), passport: scan([2], { extraction_status: 'needs_review', raw: {} }) }, kinds: { experience_certificate: [3, 4] } } })], start + 100_000)
  assert.match(found.text, /^Found Resume, Passport, Experience certificates\. Reading them now… 1 min 40 s\./)
  assert.deepEqual(found.steps.map((step) => [step.key, step.state]), [['saved', 'done'], ['line', 'done'], ['sorting', 'done'], ['reading', 'active']])
  // Everything read: every step done, and what was found is named.
  const done = readingStatus([job({ status: 'completed', result: { scans: { resume: scan([1]) } } })], start)
  assert.equal(done.text, 'Found Resume.')
  assert.ok(done.steps.every((step) => step.state === 'done'))
  assert.equal(elapsedText(61_000), '1 min 1 s')
  assert.equal(elapsedText(120_000), '2 min')
})

test('the Add candidate strip shows the reading steps under the existing sentence and keeps it', async () => {
  const page = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  assert.match(page, /Reading documents… You can leave this page; we keep going\./)
  assert.match(page, /readingStatus\(intake\.jobs\)/)
})

// ------------------------------------------------------------------ page-by-page sorting (result.sorting)
const pagesOf = (count, over = {}) => Array.from({ length: count }, (_, index) => ({ page: index + 1, state: 'read', ...(over[index + 1] ?? {}) }))
const sorting = (over = {}) => ({ pageCount: 27, pagesRead: 4, pagesSorted: 3, step: 'sorting', seconds: 40, startedAt: '2026-01-01T00:00:00Z', passes: 1, pages: pagesOf(27), ...over })

test('a file being sorted says which page of how many, in plain words', () => {
  assert.equal(sortingLine(job({ status: 'processing', result: { sorting: sorting({ reading: [5, 6] }) } })), 'Page 5 of 27 · sorting')
  assert.equal(sortingLine(job({ status: 'processing', result: { sorting: sorting() } })), 'Page 5 of 27 · sorting')          // no unit named: the next page to read
  assert.equal(sortingLine(job({ status: 'retry', result: { sorting: sorting({ step: 'waiting', pagesRead: 12 }) } })), 'Page 13 of 27 · waiting for its next turn')
  const again = sorting({ reading: [9], pages: pagesOf(27, { 8: { state: 'retry', reason: 'time ran out - retrying' } }) })
  assert.equal(sortingLine(job({ status: 'processing', result: { sorting: again } })), 'Page 9 of 27 · sorting · page 8: time ran out - retrying')
  assert.equal(sortingLine(job({ status: 'processing', result: {} })), '')                                                      // an older server: nothing is invented
  assert.equal(sortingLine(job({ status: 'completed', result: { sorting: sorting({ step: 'done' }) } })), '')
  assert.equal(sortingOf(job({ result: { sorting: { pageCount: 0, pages: [] } } })), null)
})

test('a file between two passes of the reader is reading, not waiting in line, and the strip shows the page', () => {
  const between = job({ status: 'retry', aheadOfYou: 3, result: { sorting: sorting({ step: 'waiting', pagesRead: 8 }) } })
  assert.equal(isSorting(between), true)
  const row = fileRow(between)
  assert.deepEqual([row.state, row.label, row.sortLine], ['reading', 'Reading', 'Page 9 of 27 · waiting for its next turn'])
  const strip = readingStatus([between], Date.parse('2026-01-01T00:01:20Z'))
  assert.match(strip.text, /^Page 9 of 27 · waiting for its next turn… 1 min 20 s\./)
  assert.equal(strip.steps.find((step) => step.key === 'sorting').state, 'active')
  const live = readingStatus([job({ status: 'processing', startedAt: '2026-01-01T00:00:50Z', result: { sorting: sorting({ reading: [5] }) } })], Date.parse('2026-01-01T00:00:45Z'))
  assert.match(live.text, /^Page 5 of 27 · sorting… 45 s\./)
})

test('a sorted file shows one line per kind found and the pages that could not be placed, each with its reason', () => {
  const done = sorting({
    step: 'done', pagesRead: 27,
    pages: pagesOf(9, { 1: { kind: 'resume' }, 2: { kind: 'resume' }, 3: { kind: 'passport' }, 4: { kind: 'passport' }, 5: { kind: 'medical' }, 7: { state: 'unread', reason: 'too dark', code: 'dark' }, 8: { reason: 'no text found' } }),
    unplaced: [{ page: 8, reason: 'no text found', code: 'no_text' }, { page: 7, reason: 'too dark', code: 'dark' }],
  })
  const finished = job({ status: 'completed', result: { scans: { resume: scan([1, 2]) }, sorting: done } })
  assert.deepEqual(foundLines(finished), ['Resume · pages 1, 2', 'Passport · pages 3, 4', 'Medical · page 5'])
  assert.deepEqual(unplacedPages(finished), [{ page: 7, reason: 'too dark' }, { page: 8, reason: 'no text found' }])
  const row = fileRow(finished)
  assert.deepEqual([row.sortLine, row.foundLines.length, row.unplaced.length], ['', 3, 2])
  assert.deepEqual(unplacedPages(job({ status: 'processing', result: { sorting: sorting() } })), [])                 // only once the sorting is over
})
