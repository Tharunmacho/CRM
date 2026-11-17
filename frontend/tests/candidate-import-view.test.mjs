import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** import-view.ts and drop-folder.ts are pure (their only import is a type): transpile and load them as they are. */
const load = async (name) => {
  const source = await readFile(new URL(`../src/features/candidate-import/${name}.ts`, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const view = await load('import-view')
const drop = await load('drop-folder')
const {
  initialsOf, avatarTone, AVATAR_TONES, normalizeNameSource, readName, folderTag, candidateLabel, importedTitle, NAME_NOT_READ, NAME_TO_BE_READ, NAME_READ_NOTE, FILES_CAPTION, rootNameOf, pageRanges, pagesLabel, docChips, planCounts, formatElapsed, readEstimate, isStalled,
  computeSteps, cardStatus, inFilter, filterCounts, matchesQuery, filterItems, showToolbar, readingText, resultStages, batchReading, mergeStatuses, barModel,
  plannedStoredName, plannedStoredNames, nameSlug, readingSummary, readBar, readChip, updatedAgo, nextPoll, needsPersonReading, nameInPath, nameQuality, unnamedDetail, splitIssues, fieldOf,
  personReadingReason, nameWordsOf, readReasonDetail, READ_HINT, unplacedForSoleCandidate, assignAllTo, giveAllNote,
  duplicateChoices, duplicateChoiceLabel, duplicateState, planDuplicate, duplicateNote, addedDocuments, addedHeadline, addedReadable, repeatedCount, repeatedLine, keepsOriginals,
  alreadyImportedNote, runCounts, trackedIds, isImportable, plansSkippedAsDuplicates, progressFor, DUPLICATE_CONFIRM, ADDED_KEPT_NOTE,
} = view

// ------------------------------------------------------------------------------ names and colours

test('initials come from the first and last word of the name', () => {
  assert.equal(initialsOf('Gunavelan Raman'), 'GR')
  assert.equal(initialsOf('K.Sivakumar'), 'KS')
  assert.equal(initialsOf('alnoor'), 'A')
  assert.equal(initialsOf('Mohamed Ali Khan'), 'MK')
  assert.equal(initialsOf('Gunavelan-alnoor'), 'GA')
  assert.equal(initialsOf('Rashid (2)'), 'R')
  assert.equal(initialsOf(''), '?')
  assert.equal(initialsOf('  --  '), '?')
})

test('a name always gets the same colour, whatever its case or spacing, and it is one of eight', () => {
  assert.equal(avatarTone('Meera Nair'), avatarTone('  meera   NAIR '))
  for (const name of ['A', 'Meera Nair', 'Tharun Raman', 'Rashid Khan', 'Anil', '']) {
    const tone = avatarTone(name)
    assert.ok(Number.isInteger(tone) && tone >= 0 && tone < AVATAR_TONES, name)
  }
  assert.ok(new Set(['A', 'B', 'C', 'D', 'Meera', 'Rashid', 'Anil', 'Ravi', 'Kumar', 'Ali'].map(avatarTone)).size > 3)
})

test('only the server values documents, sheet and typed count as a name; folder, files and anything else mean not read yet', () => {
  assert.equal(normalizeNameSource('documents'), 'documents')
  assert.equal(normalizeNameSource('sheet'), 'sheet')
  assert.equal(normalizeNameSource('typed'), 'typed')
  assert.equal(normalizeNameSource('pending'), 'pending')
  for (const old of ['folder', 'files', '', undefined, null, 'whatever']) assert.equal(normalizeNameSource(old), 'pending')
})

const aPlan = (over = {}) => ({ name: 'rashid khan-alnoor', folder: 'rashid khan-alnoor', parent: 'rashid khan-alnoor', identified: false, needsLook: false, ...over })
const aPerson = (over = {}) => ({ evidence: ['passport name'], conflicts: [], nameSource: 'passport', ...over })

test('a card made from a folder name has no name: only a name read from the documents is one', () => {
  assert.equal(readName(aPlan()), null)
  assert.equal(readName(aPlan({ folder: 'M / Ali', parent: 'M' })), null)
  assert.equal(readName(aPlan({ identified: true, name: 'Rashid Khan' }), aPerson()), 'Rashid Khan')
  assert.equal(readName(aPlan({ identified: true, name: 'Rashid Khan' })), 'Rashid Khan')
  // found as a person but never named: not a name
  assert.equal(readName(aPlan({ identified: true, name: 'Candidate 1' }), aPerson({ nameSource: '' })), null)
  assert.equal(readName(aPlan({ identified: true, name: 'Unnamed candidate (Aadhaar ending 123)' }), aPerson({ nameSource: '' })), null)
  assert.equal(readName(aPlan({ identified: true, name: 'Rashid Khan' }), aPerson({ nameSource: '' })), null)
})

test('the folder is a quiet tag, and a folder split by file names also says which group of files the card holds', () => {
  assert.deepEqual(folderTag(aPlan(), null), { folder: 'rashid khan-alnoor', group: null })
  assert.deepEqual(folderTag(aPlan({ folder: 'M / Ali', parent: 'M' }), null), { folder: 'M', group: 'Ali' })
  assert.deepEqual(folderTag(aPlan({ folder: 'M / Rashid Khan', parent: 'M', identified: true }), 'Rashid Khan'), { folder: 'M', group: null })
})

test('a choice between cards names the person read from the documents, else the folder or file group, marked as such', () => {
  assert.equal(candidateLabel(aPlan({ identified: true, name: 'Rashid Khan' }), aPerson()), 'Rashid Khan')
  assert.equal(candidateLabel(aPlan()), 'Files of "rashid khan-alnoor"')
})

test('after the import the title is the name the server gives, else "Name not read yet · CI-167" with the note', () => {
  const reading = { total: 0, open: 0, done: 0, review: 0, failed: 0 }
  assert.deepEqual(importedTitle({ name: 'Rashid Khan', nameSource: 'documents', reading }, 'CI-167', null), { title: 'Rashid Khan', read: true, note: null })
  assert.equal(importedTitle({ name: 'Rashid Khan', nameSource: 'typed', reading }, 'CI-167', null).read, true)
  for (const source of ['pending', 'folder', 'files']) {
    assert.deepEqual(importedTitle({ name: 'rashid khan-alnoor', nameSource: source, reading }, 'CI-167', null), { title: 'Name not read yet · CI-167', read: false, note: NAME_READ_NOTE })
  }
  assert.equal(importedTitle({ name: NAME_NOT_READ, nameSource: 'documents', reading }, 'CI-167', null).read, false)
  assert.equal(importedTitle({ name: NAME_NOT_READ, nameSource: 'pending', reading }, null, null).title, 'Name not read yet')
  // not loaded yet: the name read on this screen, else not read yet
  assert.deepEqual(importedTitle(undefined, 'CI-167', 'Rashid Khan'), { title: 'Rashid Khan', read: true, note: null })
  assert.equal(importedTitle(undefined, 'CI-167', null).title, 'Name not read yet · CI-167')
  // the status follows the reading: the new name replaces the pending one
  const pending = { name: NAME_NOT_READ, nameSource: 'pending', reading: { ...reading, total: 2, open: 2 } }
  assert.equal(importedTitle(pending, 'CI-167', null).note, NAME_READ_NOTE)
  assert.equal(importedTitle({ ...pending, name: 'Rashid Khan', nameSource: 'documents', reading: { ...reading, total: 2, done: 2 } }, 'CI-167', null).title, 'Rashid Khan')
  // read, and still no name
  assert.match(importedTitle({ ...pending, reading: { ...reading, total: 2, done: 2 } }, 'CI-167', null).note, /No name could be read/)
})

test('the texts of the screen', () => {
  assert.equal(NAME_TO_BE_READ, 'Name will be read from the documents')
  assert.equal(FILES_CAPTION, 'Files are named after the candidate once the name is read from the documents. Until then they carry the Care India ID.')
})

test('the picked folder is named after the first part of the path', () => {
  const f = new File(['x'], 'a.pdf')
  Object.defineProperty(f, 'webkitRelativePath', { value: 'Gunavelan-alnoor/a.pdf' })
  assert.equal(rootNameOf([f]), 'Gunavelan-alnoor')
  assert.equal(rootNameOf([]), '')
})

// ------------------------------------------------------------------------------ chips

test('page numbers are written as ranges', () => {
  assert.equal(pageRanges([2, 3]), '2-3')
  assert.equal(pageRanges([1, 3, 4]), '1, 3-4')
  assert.equal(pageRanges([5]), '5')
  assert.equal(pageRanges([4, 2, 3, 3, 9]), '2-4, 9')
  assert.equal(pageRanges([]), '')
  assert.equal(pagesLabel([2]), 'page 2')
  assert.equal(pagesLabel([2, 3]), 'pages 2-3')
})

const file = (path, kinds, extra = {}) => ({ path, kind: kinds[0] ?? null, kinds, skipped: false, ...extra })

test('chips group the files by document field, in order, with counts and the pages of a cut file', () => {
  const chips = docChips([
    file('cv.pdf', ['resume']),
    file('scan.pdf', ['passport'], { pages: [2, 3] }),
    file('scan2.pdf', ['aadhaar']),
    file('pp2.jpg', ['passport']),
    file('big.pdf', ['medical'], { skipped: true }),
    file('o.pdf', ['signed_offer']),
  ])
  assert.deepEqual(chips.map((c) => [c.label, c.count, c.pages]), [['Resume', 1, null], ['Passport', 2, null], ['Aadhaar', 1, null], ['Offer', 1, null]])
  const single = docChips([file('scan.pdf', ['passport'], { pages: [2, 3] })])
  assert.equal(single[0].pages, 'pages 2-3')
})

test('a scan holding several documents counts once under each field; a file with no clue in its name is "sorted at import"', () => {
  const chips = docChips([file('mix.pdf', ['signed_offer', 'passport', 'aadhaar']), file('scan7.pdf', ['other'], { guessed: true })])
  assert.deepEqual(chips.map((c) => c.label), ['Offer', 'Passport', 'Aadhaar', 'Sorted at import'])
  assert.equal(chips[3].guessed, true)
  assert.deepEqual(docChips([]), [])
  assert.equal(fieldOf('something_new').label, 'Something new')
})

test('counts: documents, and pages when they are known', () => {
  const plan = { name: 'Meera Nair', files: [file('a.pdf', ['resume']), file('b.pdf', ['passport'], { skipped: true })] }
  assert.deepEqual(planCounts(plan), { documents: 1, pages: null, leftOut: 1 })
  const read = { people: [{ name: 'Meera Nair', pageCount: 7 }], files: [] }
  assert.deepEqual(planCounts(plan, read), { documents: 1, pages: 7, leftOut: 1 })
  const cut = { name: 'X', files: [file('a.pdf', ['passport'], { pages: [1, 2] }), file('b.pdf', ['aadhaar'], { pages: [4] })] }
  assert.equal(planCounts(cut).pages, 3)
})

// ------------------------------------------------------------------------------ time

test('elapsed time reads as minutes and seconds', () => {
  assert.equal(formatElapsed(0), '0:00')
  assert.equal(formatElapsed(7), '0:07')
  assert.equal(formatElapsed(85), '1:25')
  assert.equal(formatElapsed(600), '10:00')
  assert.equal(formatElapsed(3725), '1:02:05')
  assert.equal(formatElapsed(-5), '0:00')
  assert.equal(formatElapsed(Number.NaN), '0:00')
  assert.equal(formatElapsed(59.9), '0:59')
})

test('the reading estimate starts indeterminate, then rises towards but never reaches 95 %', () => {
  assert.equal(readEstimate(0, 10), null)
  assert.equal(readEstimate(3, 10), null)
  const a = readEstimate(10, 10)
  const b = readEstimate(60, 10)
  const c = readEstimate(100000, 10)
  assert.ok(a > 0 && a < b && b < 0.95)
  assert.equal(c, 0.95)
  // more files take longer, so the same time shows less progress
  assert.ok(readEstimate(30, 40) < readEstimate(30, 4))
})

test('"nothing moved" needs a full minute by default', () => {
  assert.equal(isStalled(0, 59999), false)
  assert.equal(isStalled(0, 60000), true)
  assert.equal(isStalled(1000, 5000, 3000), true)
})

// ------------------------------------------------------------------------------ steps

const base = { hasFolder: false, importStarted: false, importing: false, importedCount: 0, failedCount: 0, readFinished: false }
const states = (input) => computeSteps({ ...base, ...input }).map((s) => s.state).join(' ')

test('the stepper highlights the current step and ticks the finished ones', () => {
  assert.deepEqual(computeSteps(base).map((s) => s.label), ['Choose folder', 'Check', 'Import', 'Read documents'])
  assert.equal(states({}), 'current todo todo todo')
  assert.equal(states({ hasFolder: true }), 'done current todo todo')
  assert.equal(states({ hasFolder: true, importStarted: true, importing: true }), 'done done current todo')
  assert.equal(states({ hasFolder: true, importStarted: true, importedCount: 3 }), 'done done done current')
  assert.equal(states({ hasFolder: true, importStarted: true, importedCount: 3, readFinished: true }), 'done done done done')
})

test('a failed import keeps step 3 open until the failures are retried; all failed means nothing is imported yet', () => {
  assert.equal(states({ hasFolder: true, importStarted: true, importedCount: 2, failedCount: 1 }), 'done done current todo')
  assert.equal(states({ hasFolder: true, importStarted: true, importedCount: 0, failedCount: 2 }), 'done done current todo')
})

// ------------------------------------------------------------------------------ status, filter, search

const plan = (extra = {}) => ({ name: 'Meera Nair', folder: 'Meera Nair', parent: 'Meera Nair', order: null, recruiter: null, files: [{ path: 'cv.pdf' }], action: 'documents_only', duplicateOf: null, needsLook: false, ...extra })

test('the card status is what happened in the import, otherwise what the check found', () => {
  assert.equal(cardStatus(plan()), 'ready')
  assert.equal(cardStatus(plan({ needsLook: true })), 'look')
  assert.equal(cardStatus(plan({ action: 'skip', duplicateOf: 'CND-1' })), 'exists')
  assert.equal(cardStatus(plan({ action: 'skip' })), 'blocked')
  assert.equal(cardStatus(plan(), { status: 'waiting' }), 'waiting')
  assert.equal(cardStatus(plan(), { status: 'running' }), 'importing')
  assert.equal(cardStatus(plan(), { status: 'done' }), 'imported')
  assert.equal(cardStatus(plan({ action: 'skip', duplicateOf: 'CND-1' }), { status: 'failed' }), 'failed')
})

test('"Needs attention" holds everything that wants a person; the filters count each candidate where it belongs', () => {
  const statuses = ['ready', 'ready', 'look', 'exists', 'blocked', 'failed', 'imported', 'importing']
  assert.deepEqual(filterCounts(statuses), { all: 8, attention: 4, ready: 3, imported: 1 })
  assert.equal(inFilter('look', 'attention'), true)
  assert.equal(inFilter('ready', 'attention'), false)
  assert.equal(inFilter('imported', 'ready'), false)
  assert.equal(inFilter('exists', 'all'), true)
  assert.deepEqual(filterCounts([]), { all: 0, attention: 0, ready: 0, imported: 0 })
})

test('search finds a candidate by any word of the name, folder, job or file names', () => {
  const p = plan({ name: 'Meera Nair', folder: 'Mixed / Meera Nair', parent: 'Mixed', order: 'J-12', files: [{ path: 'scans/passport.pdf' }] })
  assert.equal(matchesQuery(p, ''), true)
  assert.equal(matchesQuery(p, 'meera'), true)
  assert.equal(matchesQuery(p, 'NAIR meera'), true)
  assert.equal(matchesQuery(p, 'j-12'), true)
  assert.equal(matchesQuery(p, 'passport'), true)
  assert.equal(matchesQuery(p, 'mixed'), true)
  assert.equal(matchesQuery(p, 'tharun'), false)
})

test('filtering keeps the list order and applies the filter and the search together', () => {
  const items = [
    { plan: plan({ name: 'A One' }), status: 'ready' },
    { plan: plan({ name: 'B Two' }), status: 'look' },
    { plan: plan({ name: 'C Three' }), status: 'ready' },
    { plan: plan({ name: 'D Four' }), status: 'imported' },
  ]
  assert.deepEqual(filterItems(items, 'all', '').map((i) => i.plan.name), ['A One', 'B Two', 'C Three', 'D Four'])
  assert.deepEqual(filterItems(items, 'ready', '').map((i) => i.plan.name), ['A One', 'C Three'])
  assert.deepEqual(filterItems(items, 'ready', 'three').map((i) => i.plan.name), ['C Three'])
  assert.deepEqual(filterItems(items, 'attention', '').map((i) => i.plan.name), ['B Two'])
  assert.deepEqual(filterItems(items, 'imported', 'one'), [])
})

test('the filter row and the search box appear only above five candidates', () => {
  assert.deepEqual([0, 5, 6, 200].map(showToolbar), [false, false, true, true])
})

// ------------------------------------------------------------------------------ when reading the files is offered

const named = (...paths) => paths.map((path) => ({ path, skipped: false }))
const one = (files, extra = {}) => ({ folder: 'Gunavelan-alnoor', parent: 'Gunavelan-alnoor', files: named(...files), leftOut: [], identified: false, ...extra })

test('one clear person is not asked to be read: every file name starts with their name', () => {
  const clear = [one(['Gunavelan-PP.pdf', 'Gunavelan-aadhar.jpeg', 'Gunavelan-signed offer.pdf', 'Gunavelan.pdf'])]
  assert.equal(needsPersonReading(clear), false)
  // a file whose document TYPE is unknown does not matter: it is read at import anyway
  assert.equal(needsPersonReading([one(['Gunavelan-PP.pdf', 'Gunavelan-xyz.pdf'], { files: [{ path: 'Gunavelan-PP.pdf', skipped: false }, { path: 'Gunavelan-xyz.pdf', skipped: false, guessed: true }] })]), false)
})

test('several people told apart by their file names, with nothing left over, are not asked to be read', () => {
  const split = [
    { folder: 'M / Meera', parent: 'M', files: named('meera-passport.pdf', 'meera-cv.pdf'), leftOut: [], identified: false },
    { folder: 'M / Tharun', parent: 'M', files: named('tharun-passport.pdf'), leftOut: [], identified: false },
  ]
  assert.equal(needsPersonReading(split), false)
})

test('any file nobody owns, or a folder of generic-named files, is offered for reading', () => {
  const left = [
    { folder: 'M / Meera', parent: 'M', files: named('meera-passport.pdf'), leftOut: ['scan1.pdf'], identified: false },
    { folder: 'M / Tharun', parent: 'M', files: named('tharun-passport.pdf'), leftOut: ['scan1.pdf'], identified: false },
  ]
  assert.equal(needsPersonReading(left), true)
  assert.equal(needsPersonReading([one(['scan1.pdf', 'scan2.pdf', 'IMG_2031.jpg'], { folder: 'Batch', parent: 'Batch' })]), true)
  assert.equal(needsPersonReading([one(['Gunavelan-PP.pdf', 'scan2.pdf'])]), true)
})

test('already read, empty, one generic file, or one folder of many: not offered', () => {
  assert.equal(needsPersonReading([]), false)
  assert.equal(needsPersonReading([one(['scan1.pdf', 'scan2.pdf'], { identified: true, leftOut: ['x.pdf'] })]), false)
  assert.equal(needsPersonReading([one(['scan1.pdf'])]), false)
  // one folder per candidate: the folder name is the candidate, so generic file names ("cv.pdf") are expected
  assert.equal(needsPersonReading([one(['cv.pdf', 'passport.pdf'])], { pickedFolders: 30 }), false)
  assert.equal(needsPersonReading([one(['cv.pdf', 'passport.pdf'])], { pickedFolders: 1 }), true)
  // files left out for being too big do not count
  assert.equal(needsPersonReading([{ ...one(['Gunavelan.pdf']), files: [{ path: 'Gunavelan.pdf', skipped: false }, { path: 'scan.pdf', skipped: true }] }]), false)
})

test('a file name carries a person when a word is left after document words, numbers and short codes', () => {
  for (const [path, expected] of [
    ['Gunavelan-PP.pdf', true], ['Gunavelan-aadhar.jpeg', true], ['K.Sivakumar-signed-offer.jpeg', true], ['Subramanian-Medical_1.pdf', true],
    ['scan1.pdf', false], ['IMG_2031.jpg', false], ['passport copy.jpg', false], ['Resume.docx', false], ['PP.pdf', false], ['folder/Aadhaar front.png', false],
  ]) assert.equal(nameInPath(path), expected, path)
})

test('name words are read like the server reads them: up to the first hyphen, stopping at a document, a trade or a counter', () => {
  for (const [path, expected] of [
    ['K.Sivakumar-offer.jpg', ['Sivakumar']], ['Rashid khan - PP.pdf', ['Rashid', 'khan']], ['RASHIDKHAN-SIGNED OFFER.pdf', ['RASHIDKHAN']],
    ['Rashid Khan Nawazkhan-picker.pdf', ['Rashid', 'Khan', 'Nawazkhan']], ['Mohamed-passport.pdf', ['Mohamed']], ['Meera Nair Welder.pdf', ['Meera', 'Nair']],
    ['CamScanner 12-10-2026.pdf', []], ['CamScanner.pdf', []], ['EVisa-123.pdf', []], ['Itinerary AB1CD2.pdf', []], ['DSC01234.jpg', []], ['PXL_20260101.jpg', []],
    ['WhatsApp Image 2026-10-12.jpeg', []], ['Scan 4.pdf', []], ['Document.pdf', []], ['photo.jpg', []], ['dir/IMG_0042.jpg', []],
  ]) assert.deepEqual(nameWordsOf(path), expected, path)
})

const kinded = (items) => items.map(([path, kind, extra]) => ({ path, kind, kinds: kind ? [kind] : [], skipped: false, ...(extra ?? {}) }))
const folderOf = (name, items, extra = {}) => ({ folder: name, parent: name, files: kinded(items), leftOut: [], identified: false, ...extra })

test('the same first name on every file and two files of one identity kind: two people, reading is offered and recommended', () => {
  const plan = folderOf('Mohamed', [
    ['Mohamed-passport.pdf', 'passport'], ['Mohamed-aadhar.pdf', 'aadhaar'], ['Mohamed-offer.pdf', 'signed_offer'], ['Mohamed-passport (2).pdf', 'passport'],
  ])
  assert.equal(needsPersonReading([plan]), true)
  assert.equal(personReadingReason([plan]), 'first_name_only')
  assert.match(readReasonDetail('first_name_only'), /same first name/)
  assert.equal(READ_HINT, 'These file names could belong to more than one person')
  // two Aadhaar cards or two resumes say the same
  assert.equal(needsPersonReading([folderOf('Mohamed', [['Mohamed-aadhar.pdf', 'aadhaar'], ['Mohamed-aadhar 2.pdf', 'aadhaar']])]), true)
  assert.equal(needsPersonReading([folderOf('Mohamed', [['Mohamed-cv.pdf', 'resume'], ['Mohamed-resume.pdf', 'resume']])]), true)
  // one passport and one Aadhaar only: still one person as far as the names go
  assert.equal(needsPersonReading([folderOf('Mohamed', [['Mohamed-passport.pdf', 'passport'], ['Mohamed-aadhar.pdf', 'aadhaar'], ['Mohamed-offer.pdf', 'signed_offer']])]), false)
  // a file whose type is only guessed does not count as a second passport
  assert.equal(needsPersonReading([folderOf('Mohamed', [['Mohamed-passport.pdf', 'passport'], ['Mohamed-xyz.pdf', 'passport', { guessed: true }]])]), false)
})

test('files named like a scanner or a phone name nobody: reading is offered', () => {
  const plan = folderOf('Batch', [['CamScanner 12-10-2026.pdf', 'passport'], ['CamScanner 12-10-2026 (1).pdf', 'aadhaar'], ['CamScanner 13-10-2026.pdf', null]])
  assert.equal(needsPersonReading([plan]), true)
  assert.equal(personReadingReason([plan]), 'no_name')
  assert.equal(needsPersonReading([folderOf('Ravi', [['IMG_2031.jpg', null], ['WhatsApp Image 2026-10-12.jpeg', null]])]), true)
  assert.equal(needsPersonReading([folderOf('Ravi', [['PXL_20261012.jpg', null], ['DSC01234.jpg', null]])]), true)
  assert.equal(nameInPath('CamScanner 12-10-2026.pdf'), false)
})

test('one person with a full name spelled the same in every file and one file per identity kind is not offered', () => {
  const plan = folderOf('Rashid khan Nawazkhan', [['Rashid khan Nawazkhan-PP.pdf', 'passport'], ['Rashid khan Nawazkhan-aadhar.pdf', 'aadhaar'], ['Rashid khan Nawazkhan-cv.pdf', 'resume']])
  assert.equal(needsPersonReading([plan]), false)
  assert.equal(personReadingReason([plan]), null)
})

test('names that differ by spacing, case or an extra word are still one person: not offered', () => {
  // the real folder: "Rashid khan - PP", "Rashid Khan Nawazkhan-picker", "RASHIDKHAN-SIGNED OFFER"
  const real = folderOf('Rashid khan', [['Rashid khan - PP.pdf', 'passport'], ['Rashid Khan Nawazkhan-picker.pdf', null, { guessed: true }], ['RASHIDKHAN-SIGNED OFFER.pdf', 'signed_offer']])
  assert.equal(needsPersonReading([real]), false)
  // a slip of a letter in the name is the same person
  assert.equal(needsPersonReading([folderOf('Rashid khan', [['Rashid khan-PP.pdf', 'passport'], ['Rashiid khan-offer.pdf', 'signed_offer']])]), false)
  // the front and the back of one card are one document, not two
  assert.equal(needsPersonReading([folderOf('Rashid khan', [['Rashid khan-aadhar front.jpg', 'aadhaar'], ['Rashid khan-aadhar back.jpg', 'aadhaar'], ['Rashid khan-PP.pdf', 'passport']])]), false)
})

test('a folder named for one person and files named for another, or different names in the files, are offered', () => {
  const plan = folderOf('Meera Nair', [['Tharun Raman-PP.pdf', 'passport'], ['Tharun Raman-aadhar.pdf', 'aadhaar']])
  assert.equal(needsPersonReading([plan]), true)
  assert.equal(personReadingReason([plan]), 'folder_disagrees')
  // the folder name is the same name (spaced, cased or shortened differently): fine
  assert.equal(needsPersonReading([folderOf('RASHIDKHAN', [['Rashid khan-PP.pdf', 'passport'], ['Rashid khan-offer.pdf', 'signed_offer']])]), false)
  assert.equal(needsPersonReading([folderOf('Rashid', [['Rashid khan-PP.pdf', 'passport'], ['Rashid khan-offer.pdf', 'signed_offer']])]), false)
  // folders that only say "Batch" or a job say nothing about the name
  assert.equal(needsPersonReading([folderOf('Batch 12', [['Rashid khan-PP.pdf', 'passport'], ['Rashid khan-offer.pdf', 'signed_offer']])]), false)
  assert.equal(needsPersonReading([folderOf('Rashid khan welder', [['Rashid khan-PP.pdf', 'passport'], ['Rashid khan-offer.pdf', 'signed_offer']])]), false)
  // different people in the files that the check could not split
  const mixed = folderOf('Batch', [['Meera Nair-PP.pdf', 'passport'], ['Tharun Raman-PP.pdf', 'passport']])
  assert.equal(personReadingReason([mixed]), 'names_disagree')
})

test('the stricter rule leaves the many-folder and already-read cases alone', () => {
  const dup = folderOf('Mohamed', [['Mohamed-passport.pdf', 'passport'], ['Mohamed-passport (2).pdf', 'passport']])
  assert.equal(needsPersonReading([dup], { pickedFolders: 30 }), false)
  assert.equal(needsPersonReading([{ ...dup, identified: true }]), false)
  assert.equal(personReadingReason([dup, dup]), null)
})

// ------------------------------------------------------------------------------ one person found: give them every unplaced file

test('exactly one person found and files nobody owns: one click gives all of them to that person', () => {
  const solo = { folder: 'Mohamed / Mohamed Ali', parent: 'Mohamed', identified: true, leftOut: ['scan1.pdf', 'scan2.pdf', 'scan1.pdf'] }
  const found = unplacedForSoleCandidate([solo])
  assert.deepEqual(found, { target: 'Mohamed / Mohamed Ali', parent: 'Mohamed', paths: ['scan1.pdf', 'scan2.pdf'] })
  // implemented through the per-file "Belongs to" choice: key "<picked folder>/<path>" -> the candidate
  assert.deepEqual(assignAllTo(found.parent, found.paths, found.target), {
    'Mohamed/scan1.pdf': 'Mohamed / Mohamed Ali', 'Mohamed/scan2.pdf': 'Mohamed / Mohamed Ali',
  })
  const words = giveAllNote(2, 'Mohamed Ali')
  assert.match(words, /These 2 files are not given to anyone yet/)
  assert.match(words, /filed under Mohamed Ali/)
  assert.match(words, /move them to another candidate later/)
  assert.match(giveAllNote(1, null), /This file is not given to anyone yet\. It will be filed under this candidate/)
})

test('the give-all action is hidden with two or more people, nothing unplaced, or a card not made by reading', () => {
  const person = (name, leftOut) => ({ folder: `M / ${name}`, parent: 'M', identified: true, leftOut })
  assert.equal(unplacedForSoleCandidate([person('A', ['x.pdf']), person('B', ['x.pdf'])]), null)
  assert.equal(unplacedForSoleCandidate([person('A', [])]), null)
  assert.equal(unplacedForSoleCandidate([{ folder: 'M', parent: 'M', identified: false, leftOut: ['x.pdf'] }]), null)
  assert.equal(unplacedForSoleCandidate([]), null)
  assert.deepEqual(unplacedForSoleCandidate([{ folder: 'M', identified: true }]), null)
})

// ------------------------------------------------------------------------------ names that could not be read

test('a person with no readable name is "unnamed"; the part in brackets is kept as a detail', () => {
  assert.equal(nameQuality('Unnamed candidate (Aadhaar ending 123)', { needsLook: true }), 'unnamed')
  assert.equal(nameQuality('Candidate 2', { needsLook: true }), 'unnamed')
  assert.equal(nameQuality('', {}), 'unnamed')
  assert.equal(nameQuality('Meera Nair', { needsLook: true }, { evidence: ['aadhaar name'], conflicts: [{ kind: 'no_name' }] }), 'unnamed')
  assert.equal(unnamedDetail('Unnamed candidate (Aadhaar ending 123)'), 'Aadhaar ending 123')
  assert.equal(unnamedDetail('Meera (Nair)'), null)
  assert.equal(unnamedDetail('Unnamed candidate'), null)
})

test('a name that needs a look is "low confidence" when one document gives it, two names differ or it is OCR noise', () => {
  const two = { evidence: ['passport name', 'resume name'], conflicts: [] }
  assert.equal(nameQuality('Meera Nair', { needsLook: false }, { evidence: ['passport name'], conflicts: [] }), 'ok')
  assert.equal(nameQuality('Meera Nair', { needsLook: true }, two), 'ok')
  assert.equal(nameQuality('Meera Nair', { needsLook: true }, { evidence: ['passport name'], conflicts: [] }), 'low')
  assert.equal(nameQuality('Meera Nair', { needsLook: true }, { ...two, conflicts: [{ kind: 'names_differ' }] }), 'low')
  assert.equal(nameQuality('Aaharapef Dio Tdliadledf', { needsLook: true }, two), 'low')
  assert.equal(nameQuality('Bhrtschtn Kumar', { needsLook: true }, two), 'low')
})

test('"Already in the CRM" is information, not an error; real errors stay errors', () => {
  const issues = [
    { level: 'error', message: 'Already in the CRM as CI-162 (Meera Nair). Nothing is imported for this folder.' },
    { level: 'error', message: 'No recruiter could be chosen.' },
    { level: 'warning', message: '2 files are marked Passport.' },
    { level: 'info', message: '1 file with no clue in the name will be checked.' },
  ]
  const split = splitIssues(issues)
  assert.equal(split.existing, issues[0].message)
  assert.deepEqual(split.errors.map((i) => i.message), ['No recruiter could be chosen.'])
  assert.equal(split.notes.length, 2)
  assert.deepEqual(splitIssues([]), { errors: [], notes: [], existing: null, checked: null })
})

test('"Checked against the CRM while importing" is shown quietly, not as a note', () => {
  const issues = [
    { level: 'info', message: 'Checked against the CRM while importing: passport, phone and e-mail.' },
    { level: 'info', message: '1 file with no clue in the name will be checked.' },
  ]
  const split = splitIssues(issues)
  assert.equal(split.checked, issues[0].message)
  assert.deepEqual(split.notes.map((i) => i.message), [issues[1].message])
})

// ------------------------------------------------------------------------------ the result track

const status = (over = {}) => ({
  candidateId: 'c1', name: 'Meera', careId: 'CI-1', ref: 'CND-1', nameSource: 'pending', needsReview: false, phone: null,
  documents: [{ slot: 'resume', label: 'Resume', fileName: 'cv.pdf', versions: 1 }],
  reading: { total: 0, open: 0, done: 0, review: 0, failed: 0 }, detailsFound: 0, experience: { total: null, india: null, abroad: null }, ...over,
})
const track = (s) => resultStages(s).map((x) => `${x.key}:${x.state}`).join(' ')

test('the result track goes Filed, Read, Details filled, Done as the documents are read', () => {
  assert.equal(track(undefined), 'filed:active read:todo details:todo done:todo')
  assert.equal(track(status()), 'filed:done read:todo details:todo done:todo')
  assert.equal(track(status({ reading: { total: 3, open: 2, done: 1, review: 0, failed: 0 } })), 'filed:done read:active details:todo done:todo')
  assert.equal(resultStages(status({ reading: { total: 3, open: 2, done: 1, review: 0, failed: 0 } }))[1].note, 'Reading… 1 of 3 documents')
  assert.equal(track(status({ reading: { total: 3, open: 0, done: 3, review: 0, failed: 0 }, detailsFound: 12 })), 'filed:done read:done details:done done:done')
  assert.equal(track(status({ reading: { total: 3, open: 0, done: 3, review: 0, failed: 0 } })), 'filed:done read:done details:warn done:todo')
  assert.equal(track(status({ reading: { total: 3, open: 0, done: 1, review: 0, failed: 2 } })), 'filed:done read:error details:todo done:todo')
  assert.equal(track(status({ reading: { total: 3, open: 0, done: 2, review: 1, failed: 0 }, detailsFound: 4 })), 'filed:done read:warn details:done done:todo')
})

test('the reading sentence is the one the screen always had', () => {
  assert.match(readingText({ total: 0, open: 0, done: 0, review: 0, failed: 0 }), /^Documents stored\. Read them with "Read the documents"/)
  assert.equal(readingText({ total: 4, open: 2, done: 1, review: 1, failed: 0 }), 'Reading the documents: 2 of 4 done…')
  assert.equal(readingText({ total: 4, open: 0, done: 2, review: 0, failed: 2 }), '2 of 4 documents could not be read. Use "Read the documents".')
  assert.equal(readingText({ total: 4, open: 0, done: 3, review: 1, failed: 0 }), "All 4 documents read. 1 need a look on the candidate's page.")
  assert.equal(readingText({ total: 4, open: 0, done: 4, review: 0, failed: 0 }), 'All 4 documents read.')
})

test('the batch is one bar: documents finished out of documents queued', () => {
  const r = (total, open, done, review = 0, failed = 0) => ({ reading: { total, open, done, review, failed } })
  assert.deepEqual(batchReading([]), { candidates: 0, total: 0, finished: 0, open: 0, failed: 0, review: 0, fraction: 0, complete: false })
  const b = batchReading([r(3, 1, 2), r(2, 2, 0), r(4, 0, 3, 0, 1)])
  assert.deepEqual([b.candidates, b.total, b.finished, b.open, b.failed, b.complete], [3, 9, 6, 3, 1, false])
  assert.ok(Math.abs(b.fraction - 6 / 9) < 1e-9)
  assert.equal(batchReading([r(3, 0, 3), r(2, 0, 1, 1)]).complete, true)
  assert.equal(batchReading([r(0, 0, 0)]).complete, false)
})

test('a poll that changes nothing keeps the very same objects, so no card is drawn again', () => {
  const a = status({ candidateId: 'a' })
  const b = status({ candidateId: 'b' })
  const previous = { a, b }
  assert.equal(mergeStatuses(previous, [{ ...a }, { ...b }]), previous)
  const next = mergeStatuses(previous, [{ ...a }, status({ candidateId: 'b', detailsFound: 3 }), status({ candidateId: 'c' })])
  assert.notEqual(next, previous)
  assert.equal(next.a, a)
  assert.equal(next.b.detailsFound, 3)
  assert.ok(next.c)
  assert.equal(Object.keys(previous).length, 2)
})

// ------------------------------------------------------------------------------ the bar at the bottom

const bar = (over = {}) => barModel({ checking: false, reading: false, importing: false, importable: 3, documents: 14, imported: 0, failed: 0, started: 0, unticked: 0, hasList: true, ...over })

test('the bar says how much is ready and what the main button does', () => {
  const m = bar()
  assert.equal(m.phase, 'check')
  assert.equal(m.headline, '3 candidates · 14 documents')
  assert.equal(m.importLabel, 'Import 3 candidates')
  assert.equal(m.importDisabled, false)
  assert.equal(bar({ importable: 1, documents: 1 }).headline, '1 candidate · 1 document')
  assert.equal(bar({ importable: 1 }).importLabel, 'Import 1 candidate')
  assert.match(bar({ unticked: 2 }).detail, /2 need a look and are not ticked/)
})

test('when the button cannot be pressed the bar says why in words', () => {
  assert.match(bar({ checking: true }).reason, /Checking the files/)
  assert.match(bar({ importable: 0 }).reason, /Tick at least one candidate/)
  assert.match(bar({ reading: true }).reason, /finish being read/)
  assert.equal(bar({ checking: true }).importDisabled, true)
  assert.equal(bar({ hasList: false }).importDisabled, true)
  assert.equal(bar().reason, '')
})

test('while importing, and after it, the bar follows the batch', () => {
  const running = bar({ importing: true, started: 5, imported: 2, failed: 1 })
  assert.equal(running.phase, 'importing')
  assert.equal(running.headline, 'Importing 3 of 5')
  assert.equal(running.importDisabled, true)
  const done = bar({ started: 5, imported: 5, importable: 0 })
  assert.equal(done.phase, 'imported')
  assert.equal(done.headline, '5 candidates imported')
  assert.match(done.detail, /read the documents/i)
  assert.match(bar({ started: 5, imported: 5, importable: 0, readState: 'reading' }).detail, /being read/)
  assert.match(bar({ started: 5, imported: 5, importable: 0, readState: 'done' }).detail, /All documents are read/)
  const partly = bar({ started: 5, imported: 4, failed: 1, importable: 0 })
  assert.match(partly.detail, /1 candidate could not be imported/)
})

test('the screen never uses developer words', () => {
  const words = JSON.stringify([bar(), bar({ importing: true, started: 2 }), bar({ started: 2, imported: 1, failed: 1 }), resultStages(status())])
  for (const bad of ['stand-in', 'provisional', 'probe', 'slot', 'kind']) assert.ok(!words.toLowerCase().includes(bad), bad)
})

// ------------------------------------------------------------------------------ dropping a folder

const fileEntry = (name, content = 'x') => ({ isFile: true, isDirectory: false, name, file: (ok) => ok(new File([content], name)) })
/** A folder whose entries come in batches of `batch`, like a browser hands over a big folder. */
const dirEntry = (name, children, batch = 2) => ({
  isFile: false, isDirectory: true, name,
  createReader() {
    let at = 0
    return { readEntries(ok) { const slice = children.slice(at, at + batch); at += batch; ok(slice) } }
  },
})

test('a dropped folder becomes files with the same folder paths the folder button gives, so groupPicked works on both', async () => {
  const folder = dirEntry('Gunavelan-alnoor', [fileEntry('a.pdf'), fileEntry('b.pdf'), fileEntry('c.jpg'), dirEntry('scans', [fileEntry('d.pdf')])])
  const dropped = await drop.collectDropped([folder])
  assert.equal(dropped.root, 'Gunavelan-alnoor')
  assert.deepEqual(dropped.files.map((f) => f.webkitRelativePath).sort(), ['Gunavelan-alnoor/a.pdf', 'Gunavelan-alnoor/b.pdf', 'Gunavelan-alnoor/c.jpg', 'Gunavelan-alnoor/scans/d.pdf'])
  assert.equal(dropped.files.find((f) => f.name === 'a.pdf').size, 1)
})

test('a dropped folder of candidate folders keeps one level of folders for groupPicked', async () => {
  const folder = dirEntry('All', [dirEntry('A One', [fileEntry('cv.pdf'), fileEntry('pp.pdf'), fileEntry('x.pdf')]), dirEntry('B Two', [fileEntry('cv.pdf')])], 1)
  const { files } = await drop.collectDropped([folder])
  assert.deepEqual(files.map((f) => f.webkitRelativePath), ['All/A One/cv.pdf', 'All/A One/pp.pdf', 'All/A One/x.pdf', 'All/B Two/cv.pdf'])
})

test('several dropped folders are treated as the contents of one folder; loose files are not a folder', async () => {
  const two = await drop.collectDropped([dirEntry('A One', [fileEntry('cv.pdf')]), dirEntry('B Two', [fileEntry('cv.pdf')]), fileEntry('loose.pdf')])
  assert.deepEqual(two.files.map((f) => f.webkitRelativePath), ['Dropped folders/A One/cv.pdf', 'Dropped folders/B Two/cv.pdf'])
  assert.equal(await drop.collectDropped([fileEntry('loose.pdf')]), null)
  assert.equal(await drop.collectDropped([]), null)
})

test('an unreadable dropped folder fails with an error the screen turns into a friendly message', async () => {
  const broken = { isFile: false, isDirectory: true, name: 'X', createReader: () => ({ readEntries: (_ok, fail) => fail(new Error('denied')) }) }
  await assert.rejects(drop.collectDropped([broken]))
})

test('entries are taken from the drop event synchronously and files without entries are skipped', () => {
  const dt = { items: [{ kind: 'file', webkitGetAsEntry: () => ({ name: 'F', isDirectory: true }) }, { kind: 'string' }, { kind: 'file', webkitGetAsEntry: () => null }] }
  assert.deepEqual(drop.entriesFromTransfer(dt).map((e) => e.name), ['F'])
  assert.deepEqual(drop.entriesFromTransfer(null), [])
  assert.deepEqual(drop.entriesFromTransfer({ items: null }), [])
})

// ------------------------------------------------------------------------------ the names the files are saved under

const pf = (path, labels, extra = {}) => ({ path, kind: null, kinds: labels.map((l) => l.toLowerCase().replace(/\W+/g, '_')), labels, label: labels[0] ?? null, skipped: false, problem: null, ...extra })

test('a file of one kind is saved as Name-Document with its extension lower-cased', () => {
  const plan = 'Gunavelan R'
  const taken = new Set()
  assert.deepEqual(plannedStoredName(plan, pf('Gunavelan-PP.pdf', ['Passport']), taken), { name: 'Gunavelan_R-Passport.pdf' })
  assert.equal(plannedStoredName(plan, pf('x/other.PDF', ['Passport']), taken).name, 'Gunavelan_R-Passport-2.pdf')
  assert.equal(plannedStoredName(plan, pf('x/photo.JPEG', ['Photo']), new Set()).name, 'Gunavelan_R-Photo.jpeg')
  assert.equal(plannedStoredName(plan, pf('scan', ['Photo']), new Set()).name, 'Gunavelan_R-Photo')
  assert.equal(plannedStoredName(plan, pf('Police.pdf', ['Police clearance certificate (PCC)']), new Set()).name, 'Gunavelan_R-Police_clearance_certificate_PCC.pdf')
  assert.equal(plannedStoredName(plan, pf('r.pdf', ['Advance receipt (stage 8)']), new Set()).name, 'Gunavelan_R-Advance_receipt_stage_8.pdf')
})

test('a scan holding several documents carries every name and is cut by page into one file per document', () => {
  const plan = 'Rashid khan alnoor'
  const planned = plannedStoredName(plan, pf('scan.pdf', ['Signed offer', 'Passport', 'Aadhaar card']), new Set())
  assert.equal(planned.name, 'Rashid_khan_alnoor-Signed_offer_Passport_Aadhaar_card.pdf')
  assert.deepEqual(planned.pieces, ['Rashid_khan_alnoor-Signed_offer.pdf', 'Rashid_khan_alnoor-Passport.pdf', 'Rashid_khan_alnoor-Aadhaar_card.pdf'])
  assert.equal(planned.note, 'cut by page when it holds several documents')
})

test('repeated documents are numbered -2, -3 in the order the server takes them: scans of several documents first, then by lower-case path', () => {
  const plan = {
    files: [
      pf('b-passport.pdf', ['Passport']),
      pf('A-passport.pdf', ['Passport']),
      pf('c-passport.pdf', ['Passport']),
      pf('all.pdf', ['Passport', 'Aadhaar card']),
      pf('left.pdf', ['Resume'], { skipped: true, problem: 'x' }),
    ],
  }
  const names = plannedStoredNames(plan, 'Rahul K')
  assert.equal(names.get('all.pdf').pieces[0], 'Rahul_K-Passport.pdf')
  assert.equal(names.get('A-passport.pdf').name, 'Rahul_K-Passport-2.pdf')
  assert.equal(names.get('b-passport.pdf').name, 'Rahul_K-Passport-3.pdf')
  assert.equal(names.get('c-passport.pdf').name, 'Rahul_K-Passport-4.pdf')
  assert.equal(names.has('left.pdf'), false)
})

test('a file whose kind is not known before the import is pending, unless a kind was picked for it', () => {
  const plan = { files: [pf('scan1.pdf', [], { kinds: [], guessed: true }), pf('scan2.pdf', [], { kinds: [] }), pf('scan3.pdf', ['Passport'], { guessed: true })] }
  assert.deepEqual(plannedStoredName('Rahul K', plan.files[0], new Set()), { name: null, pending: true })
  assert.equal(plannedStoredName('Rahul K', plan.files[1], new Set()).pending, true)
  assert.equal(plannedStoredName('Rahul K', plan.files[2], new Set()).pending, true)
  assert.equal(plannedStoredNames(plan, 'Rahul K', { 'scan1.pdf': ['Resume'] }).get('scan1.pdf').name, 'Rahul_K-Resume.pdf')
})

test('accented and spaced names are slugged like the server does', () => {
  assert.equal(nameSlug('S. Dinesh Kumar'), 'S_Dinesh_Kumar')
  assert.equal(nameSlug('  José  Ñandú '), 'Jos_and')
  assert.equal(nameSlug('---'), 'Candidate')
  assert.equal(plannedStoredName('S. Dinesh Kumar', pf('a.pdf', ['Resume']), new Set()).name, 'S_Dinesh_Kumar-Resume.pdf')
})

test('a long name is kept as it is, up to the 150 characters the server stores', () => {
  const longName = 'Muhammad Abdul Rahman Al Hussain Ibn Mohammed '.repeat(2).trim()
  const planned = plannedStoredName(longName, pf('a.pdf', ['Resume']), new Set()).name
  assert.equal(planned, `${nameSlug(longName)}-Resume.pdf`)
  assert.ok(planned.length < 150)
  assert.equal(plannedStoredName('N'.repeat(300), pf('a.pdf', ['Resume']), new Set()).name.length, 150)
})

test('until the name is read, files carry the Care India ID and never the folder name; the -2 numbering still works', () => {
  const taken = new Set()
  assert.deepEqual(plannedStoredName(null, pf('rashid khan-alnoor-pp.pdf', ['Passport']), taken), { name: 'CI-…-Passport.pdf' })
  assert.equal(plannedStoredName(null, pf('x/other.PDF', ['Passport']), taken).name, 'CI-…-Passport-2.pdf')
  assert.equal(plannedStoredName(null, pf('photo.JPEG', ['Photo']), new Set()).name, 'CI-…-Photo.jpeg')
  const planned = plannedStoredName(null, pf('scan.pdf', ['Signed offer', 'Passport']), new Set())
  assert.equal(planned.name, 'CI-…-Signed_offer_Passport.pdf')
  assert.deepEqual(planned.pieces, ['CI-…-Signed_offer.pdf', 'CI-…-Passport.pdf'])
  const names = plannedStoredNames({ files: [pf('b.pdf', ['Passport']), pf('a.pdf', ['Passport'])] }, null)
  assert.equal(names.get('a.pdf').name, 'CI-…-Passport.pdf')
  assert.equal(names.get('b.pdf').name, 'CI-…-Passport-2.pdf')
  assert.equal(plannedStoredName(null, pf('scan1.pdf', [], { kinds: [], guessed: true }), new Set()).pending, true)
  // the name read from the documents replaces the placeholder
  assert.equal(plannedStoredNames({ files: [pf('a.pdf', ['Passport'])] }, 'Rashid Khan').get('a.pdf').name, 'Rashid_Khan-Passport.pdf')
})

// ------------------------------------------------------------------------------ the live state of the bar and the cards while reading

const live = (name, reading, extra = {}) => status({ candidateId: name, name, reading: { total: 0, open: 0, done: 0, review: 0, failed: 0, ...reading }, ...extra })
const phaseOf = (list, expected = list.length, requesting = false) => readingSummary(list, expected, requesting).phase

test('the reading state goes unread, starting, reading, read as the status changes', () => {
  assert.equal(phaseOf([], 2), 'checking')
  assert.equal(phaseOf([live('A', {})], 2), 'checking')
  assert.equal(phaseOf([live('A', {}), live('B', {})]), 'unread')
  assert.equal(phaseOf([live('A', {}), live('B', {})], 2, true), 'starting')
  assert.equal(phaseOf([live('A', { total: 3, open: 2, done: 1 }), live('B', { total: 2, open: 2 })]), 'reading')
  assert.equal(phaseOf([live('A', { total: 3, done: 3 }), live('B', { total: 2, done: 2 })]), 'read')
  // the request button beats nothing: while a reading is requested the bar says so even when the old status shows everything read
  assert.equal(phaseOf([live('A', { total: 3, done: 3 })], 1, true), 'starting')
})

test('a candidate with only a photo has nothing to read', () => {
  const photo = live('A', {}, { documents: [{ slot: 'photo', label: 'Photo', fileName: 'p.jpg', versions: 1 }] })
  assert.equal(phaseOf([photo]), 'nothing')
  assert.equal(readBar(readingSummary([photo], 1), 1).readLabel, null)
})

test('read with some to check, and read with some failed or never read', () => {
  const review = readingSummary([live('A', { total: 2, done: 1, review: 1 }), live('B', { total: 1, done: 1 })], 2)
  assert.equal(review.phase, 'review')
  assert.deepEqual(review.review, ['A'])
  const partial = readingSummary([live('A', { total: 2, done: 1, failed: 1 }), live('B', { total: 1, done: 1 }), live('C', {})], 3)
  assert.equal(partial.phase, 'partial')
  assert.deepEqual(partial.unread, ['A', 'C'])
  assert.equal(partial.read, 1)
})

test('the bar words and buttons follow the state', () => {
  const imported = (list, requesting = false) => readBar(readingSummary(list, list.length, requesting), list.length)
  const unread = imported([live('A', {})])
  assert.equal(unread.headline, '1 candidate imported, nothing read yet')
  assert.equal(unread.readLabel, 'Read the documents')
  assert.equal(unread.finished, false)

  const starting = imported([live('A', {})], true)
  assert.equal(starting.readLabel, null)
  assert.equal(starting.progress, null)
  assert.equal(starting.tone, 'busy')

  const reading = imported([live('A', { total: 3, open: 1, done: 2 }), live('B', { total: 3, open: 3 })])
  assert.equal(reading.headline, 'Reading the documents… 2 of 6 read')
  assert.equal(reading.readLabel, null)
  assert.equal(Math.round(reading.progress * 100), 33)

  const read = imported([live('A', { total: 3, done: 3 }, { detailsFound: 9 }), live('B', { total: 1, done: 1 }, { detailsFound: 4 })])
  assert.equal(read.headline, 'All 2 candidates read · details filled')
  assert.match(read.detail, /13 details filled in/)
  assert.equal(read.readLabel, null)
  assert.equal(read.finished, true)

  const look = imported([live('Meera', { total: 2, done: 1, review: 1 }), live('B', { total: 1, done: 1 })])
  assert.equal(look.headline, '2 read, 1 needs a look')
  assert.equal(look.tone, 'warn')
  assert.match(look.detail, /Meera/)
  assert.equal(look.readLabel, null)

  const partial = imported([live('Meera', { total: 2, done: 1, failed: 1 }), live('B', { total: 1, done: 1 }), live('C', {})])
  assert.equal(partial.readLabel, 'Read again (2)')
  assert.match(partial.detail, /Not read: Meera, C/)
})

test('the card chip changes from Imported to Reading, Read or Needs a look', () => {
  assert.equal(readChip(undefined), null)
  assert.equal(readChip(live('A', {})), null)
  assert.deepEqual(readChip(live('A', { total: 2, open: 1, done: 1 })), { label: 'Reading', tone: 'navy' })
  assert.deepEqual(readChip(live('A', { total: 2, done: 2 })), { label: 'Read', tone: 'green' })
  assert.deepEqual(readChip(live('A', { total: 2, done: 1, review: 1 })), { label: 'Needs a look', tone: 'amber' })
  assert.deepEqual(readChip(live('A', { total: 2, done: 1, failed: 1 })), { label: 'Needs a look', tone: 'amber' })
})

test('polling every 3 s while anything is read, never when settled or hidden, and the quiet update note', () => {
  assert.equal(nextPoll({ open: 2, requesting: false, hidden: false, round: 1 }), 3000)
  assert.equal(nextPoll({ open: 0, requesting: true, hidden: false, round: 1 }), 3000)
  assert.equal(nextPoll({ open: 0, requesting: false, hidden: false, round: 1 }), null)
  assert.equal(nextPoll({ open: 2, requesting: false, hidden: true, round: 1 }), null)
  assert.equal(nextPoll({ open: 2, requesting: false, hidden: false, round: 500 }), 10000)
  assert.equal(updatedAgo(0), 'Updated just now')
  assert.equal(updatedAgo(12), 'Updated 12 s ago')
  assert.equal(updatedAgo(185), 'Updated 3 min ago')
})

// ------------------------------------------------------------------------------ candidates already in the CRM

const STATUS_LABEL_OF = (key) => view.STATUS_LABEL[key]
const found = (over = {}) => ({ candidateId: 'c-139', careId: 'CI-139', ref: 'CND-139', name: 'Meera Nair', matchedBy: 'passport', files: 2, strength: 'certain', ...over })
const dupResult = (over = {}) => ({
  status: 'duplicate', action: 'skip', careId: 'CI-139', candidateId: 'c-139', ref: 'CND-139', name: 'Meera Nair', matchedBy: 'passport', strength: 'certain', files: 2,
  choices: ['skip', 'add_documents'], message: 'Already in the CRM as CI-139 · Meera Nair (certain: same passport)', folder: 'Meera', issues: [], ...over,
})

test('a certain match never offers "import anyway"; a possible one does, and Skip is always first', () => {
  assert.deepEqual(duplicateChoices('certain'), ['skip', 'add_documents'])
  assert.deepEqual(duplicateChoices('certain', ['skip', 'add_documents', 'import_anyway']), ['skip', 'add_documents'])
  assert.deepEqual(duplicateChoices('possible'), ['skip', 'add_documents', 'import_anyway'])
  assert.deepEqual(duplicateChoices('possible', ['skip', 'add_documents']), ['skip', 'add_documents'])
  assert.deepEqual(duplicateChoices('possible', ['add_documents']), ['skip', 'add_documents'])
  assert.deepEqual(duplicateChoices(null), ['skip', 'add_documents'])
})

test('the duplicate buttons say what they do', () => {
  assert.equal(duplicateChoiceLabel('skip', 'CI-139'), 'Skip')
  assert.equal(duplicateChoiceLabel('add_documents', 'CI-139'), 'Add these documents to CI-139')
  assert.equal(duplicateChoiceLabel('import_anyway', 'CI-139'), 'Import as a new candidate anyway')
  assert.equal(DUPLICATE_CONFIRM, 'This looks like the same person, only the phone or e-mail matches. Import anyway?')
})

test('duplicateState reads the "duplicate" answer: the server message is the headline, the choices come from the server (never import anyway for certain)', () => {
  const dup = duplicateState(dupResult())
  assert.equal(dup.headline, 'Already in the CRM as CI-139 · Meera Nair (certain: same passport)')
  assert.equal(dup.careLabel, 'CI-139')
  assert.equal(dup.candidateId, 'c-139')
  assert.deepEqual(dup.choices, ['skip', 'add_documents'])
  assert.deepEqual(duplicateState(dupResult({ strength: 'possible', matchedBy: 'phone', choices: ['skip', 'add_documents', 'import_anyway'] })).choices, ['skip', 'add_documents', 'import_anyway'])
  assert.deepEqual(duplicateState(dupResult({ choices: ['skip', 'add_documents', 'import_anyway'] })).choices, ['skip', 'add_documents'])
  // without a message the headline is built from what matched
  assert.equal(duplicateState(dupResult({ message: undefined })).headline, 'Already in the CRM as CI-139 · Meera Nair (certain: same passport)')
  // any other answer is not a duplicate
  assert.equal(duplicateState({ status: 'imported' }), null)
  assert.equal(duplicateState({ status: 'already_imported' }), null)
  assert.equal(duplicateState({ status: 'documents_added' }), null)
  assert.equal(duplicateState(undefined), null)
})

test('planDuplicate turns the alreadyInCrm of the check into the same card, Skip by default', () => {
  assert.equal(planDuplicate({ issues: [] }), null)
  assert.equal(planDuplicate({ issues: [], alreadyInCrm: null }), null)
  const dup = planDuplicate({ issues: [{ level: 'error', message: 'Already in the CRM as CI-139 · Meera Nair (certain: same file)' }], alreadyInCrm: found({ matchedBy: 'same file' }) })
  assert.equal(dup.headline, 'Already in the CRM as CI-139 · Meera Nair (certain: same file)')
  assert.deepEqual(dup.choices, ['skip', 'add_documents'])
  assert.equal(dup.candidateId, 'c-139')
  const possible = planDuplicate({ issues: [], alreadyInCrm: found({ strength: 'possible', matchedBy: 'phone' }) })
  assert.deepEqual(possible.choices, ['skip', 'add_documents', 'import_anyway'])
  assert.match(possible.headline, /CI-139 · Meera Nair \(possible: same phone\)/)
})

test('a duplicate card is its own state: Duplicate before and after the import, Skipped and Documents added after a choice', () => {
  assert.equal(cardStatus({ action: 'skip', duplicateOf: 'CND-139', alreadyInCrm: found() }), 'duplicate')
  assert.equal(cardStatus({ action: 'skip', duplicateOf: 'CND-139' }), 'exists')
  assert.equal(cardStatus({ action: 'in_progress' }, { status: 'duplicate' }), 'duplicate')
  assert.equal(cardStatus({ action: 'in_progress' }, { status: 'skipped' }), 'skipped')
  assert.equal(cardStatus({ action: 'in_progress' }, { status: 'added' }), 'added')
  assert.equal(STATUS_LABEL_OF('duplicate'), 'Duplicate')
  assert.equal(STATUS_LABEL_OF('added'), 'Documents added')
  // Duplicates need attention and are never "Imported" (added documents are listed with the imported)
  assert.equal(inFilter('duplicate', 'attention'), true)
  assert.equal(inFilter('duplicate', 'imported'), false)
  assert.equal(inFilter('skipped', 'imported'), false)
  assert.equal(inFilter('added', 'imported'), true)
})

test('progressFor sorts the answers: imported and already imported are done, duplicate and documents added are their own', () => {
  assert.equal(progressFor({ status: 'imported', action: 'in_progress', issues: [] }).status, 'done')
  assert.equal(progressFor({ status: 'already_imported', action: 'in_progress', issues: [] }).status, 'done')
  assert.equal(progressFor(dupResult()).status, 'duplicate')
  assert.equal(progressFor({ status: 'documents_added', action: 'documents_only', issues: [] }).status, 'added')
  // an older refusal (action skip, normal status) still shows as not imported with its first message
  const refused = progressFor({ status: 'imported', action: 'skip', issues: [{ level: 'error', message: 'No files.' }] })
  assert.equal(refused.status, 'failed')
  assert.equal(refused.error, 'No files.')
})

test('the bar counts duplicates apart and never as imported', () => {
  const counts = runCounts([{ status: 'done' }, { status: 'done' }, { status: 'duplicate' }, { status: 'skipped' }, { status: 'added' }, { status: 'failed' }, { status: 'running' }])
  assert.deepEqual(counts, { started: 7, imported: 2, failed: 1, skipped: 1, waiting: 1, added: 1, settled: 6 })
  const done = bar({ started: 4, imported: 2, duplicatesSkipped: 1, duplicatesWaiting: 1, importable: 0 })
  assert.equal(done.headline, '2 candidates imported')
  assert.match(done.detail, /1 duplicate skipped\./)
  assert.match(done.detail, /1 duplicate waiting for your choice\./)
  const running = bar({ importing: true, started: 5, imported: 1, duplicatesSkipped: 1, added: 1 })
  assert.equal(running.headline, 'Importing 3 of 5')
  // before anything runs, the duplicates left alone by default are named
  assert.match(bar({ duplicatesLeft: 2 }).detail, /2 duplicates skipped\./)
  assert.equal(bar({ duplicatesLeft: 1 }).headline, '3 candidates · 14 documents')
  assert.equal(duplicateNote({}), '')
  assert.equal(duplicateNote({ added: 2 }), 'Documents added to 2 candidates already in the CRM.')
  assert.equal(bar({ started: 1, imported: 0, duplicatesSkipped: 1, importable: 0 }).headline, '0 candidates imported')
})

test('a duplicate is importable only when a person chose something other than Skip; answered cards never are', () => {
  const dup = { action: 'skip', alreadyInCrm: found() }
  assert.equal(isImportable(dup, { left: false }), false)
  assert.equal(isImportable(dup, { left: false, choice: 'skip' }), false)
  assert.equal(isImportable(dup, { left: false, choice: 'add_documents' }), true)
  assert.equal(isImportable(dup, { left: true, choice: 'add_documents' }), false)
  assert.equal(isImportable({ action: 'skip' }, { left: false }), false)
  assert.equal(isImportable({ action: 'in_progress' }, { left: false }), true)
  assert.equal(isImportable({ action: 'in_progress' }, { left: true }), false)
  for (const status of ['done', 'duplicate', 'skipped', 'added']) assert.equal(isImportable({ action: 'in_progress' }, { left: false, progress: { status } }), false, status)
  assert.equal(isImportable({ action: 'in_progress' }, { left: false, progress: { status: 'failed' } }), true)
  assert.equal(plansSkippedAsDuplicates([{ folder: 'A', alreadyInCrm: found() }, { folder: 'B', alreadyInCrm: found() }, { folder: 'C' }], { B: 'add_documents' }, {}), 1)
  assert.equal(plansSkippedAsDuplicates([{ folder: 'A', alreadyInCrm: found() }], {}, { A: { status: 'duplicate' } }), 0)
})

test('the live polling ignores skipped and waiting duplicates (their answer carries the other candidate id); added documents are followed only when a readable one was added', () => {
  const doc = (slot, added = true) => ({ slot, added })
  const ids = trackedIds([
    { status: 'done', result: { candidateId: 'new-1' } },
    { status: 'done', result: { candidateId: 'new-1' } },
    { status: 'duplicate', result: { candidateId: 'c-139' } },
    { status: 'skipped', result: { candidateId: 'c-140' } },
    { status: 'added', result: { candidateId: 'c-141', documents: [doc('passport')] } },
    { status: 'added', result: { candidateId: 'c-142', documents: [doc('photo'), doc('passport', false)] } },
    { status: 'failed', result: { candidateId: 'c-143' } },
    { status: 'running' },
  ])
  assert.deepEqual(ids, ['new-1', 'c-141'])
})

test('documents added: lists the added files and the ones already filed', () => {
  const result = { status: 'documents_added', careId: 'CI-139', candidateId: 'c-139', documents: [
    { slot: 'passport', label: 'Passport', fileName: 'Meera-Passport.pdf', pages: 2, added: true, alreadyFiled: false },
    { slot: 'aadhaar', label: 'Aadhaar', fileName: 'Meera-Aadhaar.pdf', pages: 1, added: false, alreadyFiled: true, note: 'same file' },
  ] }
  assert.equal(addedHeadline(result), 'Documents added to CI-139')
  assert.deepEqual(addedDocuments(result).added.map((d) => d.slot), ['passport'])
  assert.deepEqual(addedDocuments(result).already.map((d) => d.slot), ['aadhaar'])
  assert.equal(addedReadable(result), true)
  assert.equal(addedReadable({ documents: [{ slot: 'photo', added: true }] }), false)
  assert.equal(addedReadable({ documents: [{ slot: 'passport', added: false, alreadyFiled: true }] }), false)
  assert.equal(addedHeadline({ careId: 'CI-139', documents: [{ slot: 'photo', added: false, alreadyFiled: true }] }), 'No new documents for CI-139')
  assert.equal(ADDED_KEPT_NOTE, 'The candidate’s stages, status and owner were not changed.')
})

test('repeated pages are counted and worded, and the originals note follows the server', () => {
  const list = [{ pages: [2, 3], message: 'Passport pages 2-3 repeat file a.pdf', originalKept: true }, { pages: [5], message: 'Aadhaar page 5 repeats b.pdf' }]
  assert.equal(repeatedCount(list), 3)
  assert.equal(repeatedLine(list), '3 repeated pages were not filed again')
  assert.equal(repeatedLine([{ pages: [1], message: 'x' }]), '1 repeated page was not filed again')
  assert.equal(repeatedLine([]), '')
  assert.equal(repeatedLine(undefined), '')
  assert.equal(keepsOriginals(list), true)
  assert.equal(keepsOriginals([{ message: 'x' }]), false)
})

test('an "already imported" answer says so', () => {
  assert.equal(alreadyImportedNote({ status: 'already_imported', careId: 'CI-150' }), 'Already imported as CI-150')
  assert.equal(alreadyImportedNote({ status: 'imported', careId: 'CI-150' }), null)
  assert.equal(alreadyImportedNote(undefined), null)
})

test('nameSource: documents, sheet, typed and pending are kept; the older folder and files count as pending', () => {
  assert.deepEqual(['documents', 'sheet', 'typed', 'pending', 'folder', 'files', '', null, undefined, 'other'].map(normalizeNameSource),
    ['documents', 'sheet', 'typed', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending', 'pending'])
})
