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
  trackNextInLine, NEXT_IN_LINE_MS, NO_READER_INFO_MS, READER_DOWN, STILL_WAITING, safeHint } = upload

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
  assert.equal(fileRow(job({ status: 'uploaded' }), early).label, 'Queued')
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
  assert.deepEqual(chips.map((chip) => [chip.state, chip.label, chip.detail]), [['reading', 'Resume found', 'pages 1, 2'], ['reading', 'Passport found', 'page 4'], ['reading', 'Aadhaar found', 'pages 5, 6']])
  // 3. The resume is read first (partial result): it turns done, the others keep reading.
  chips = foundChips([file({ ...classified, scans: { resume: scan([1, 2]) } }, 'processing')])
  assert.deepEqual(chips.map((chip) => chip.state), ['done', 'reading', 'reading'])
  // 4. Everything read.
  chips = foundChips([file({ ...classified, scans: { resume: scan([1, 2]), passport: scan([4]), aadhaar: scan([5, 6]) } }, 'completed')])
  assert.deepEqual(chips.map((chip) => chip.state), ['done', 'done', 'done'])
  assert.equal(chips[1].detail, 'page 4')
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
  assert.deepEqual(chips.map((chip) => [chip.kind, chip.state, chip.detail]), [['resume', 'done', '2 files'], ['passport', 'done', 'page 3'], ['aadhaar', 'done', '']])
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
  assert.match(page, /title="Upload documents" subtitle="Resume, passport or Aadhaar, in one file or several\. We find each one\."/)
  assert.match(page, /More documents \(optional\)/)
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
  assert.equal(fileRow(job({ status: 'uploaded', reader: dead }), { now: T0 }).label, 'Queued')   // not queued for reading yet
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
