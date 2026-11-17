import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** Transpile import-api.ts with its two "@/" imports replaced by small stubs (the module itself is pure). */
const calls = []
globalThis.__importApiCalls = calls
const stubs = {
  '@/shared/api/client': "export class ApiError extends Error { constructor(status, message, detail) { super(message); this.status = status; this.detail = detail } }\nexport const api ={ post: (path, body) => (globalThis.__importApiCalls.push({ path, body }), Promise.resolve({})), form: (path, form) => (globalThis.__importApiCalls.push({ path, form }), Promise.resolve(globalThis.__importApiReply ?? {})), get: () => Promise.resolve({}) }\nexport const fileHref = (p) => p",
  '@/shared/lib/document-files': 'export const MAX_DOCUMENT_BYTES = 25 * 1024 * 1024',
}
let source = await readFile(new URL('../src/features/candidate-import/import-api.ts', import.meta.url), 'utf8')
for (const [spec, code] of Object.entries(stubs)) {
  assert.ok(source.includes(`from '${spec}'`), `import-api.ts no longer imports ${spec}`)
  source = source.replaceAll(`from '${spec}'`, `from 'data:text/javascript;base64,${Buffer.from(code).toString('base64')}'`)
}
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { assignKey, groupPicked, importApi, fileTooLarge, summaryText, listCounts, peopleFor, leftovers, leftoverKey, waiting, pollDelay, openReadings, POLL_ROUNDS, countOf, readAgainMessage, parseImportResult, newImportKey, hashFile, hashPicked } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

const file = (relative, content = 'x') => {
  const f = new File([content], relative.split('/').pop())
  Object.defineProperty(f, 'webkitRelativePath', { value: relative })
  return f
}

test('assignKey is "<picked folder>/<file path>", the key the check API reads', () => {
  assert.equal(assignKey('Rashid Khan', 'passport.pdf'), 'Rashid Khan/passport.pdf')
  assert.equal(assignKey('Rashid Khan', 'scans/Rashid passport.pdf'), 'Rashid Khan/scans/Rashid passport.pdf')
})

test('groupPicked makes one candidate per sub-folder and keeps nested paths relative to it', () => {
  const picked = groupPicked([
    file('All/B Two/passport.pdf'), file('All/A One/scans/cv.pdf'), file('All/A One/photo.jpg'), file('All/A One/.hidden'), file('All/sheet.xlsx'),
  ])
  assert.deepEqual(picked.folders.map((f) => f.folder), ['A One', 'B Two'])
  assert.deepEqual(picked.folders[0].files.map((f) => f.path), ['photo.jpg', 'scans/cv.pdf'])
  assert.equal(picked.ignored, 1)
  assert.equal(picked.sheet?.name, 'sheet.xlsx')
  // Every file of a folder is addressable by the key the screen sends as `assign`.
  const keys = picked.folders[0].files.map(({ path }) => assignKey('A One', path))
  assert.deepEqual(keys, ['A One/photo.jpg', 'A One/scans/cv.pdf'])
})

test('groupPicked treats a single candidate folder as one candidate', () => {
  const picked = groupPicked([file('Rashid/passport.pdf'), file('Rashid/cv.pdf')])
  assert.deepEqual(picked.folders.map((f) => f.folder), ['Rashid'])
  assert.deepEqual(picked.folders[0].files.map((f) => f.path), ['cv.pdf', 'passport.pdf'])
})

test('check sends the grouping choices to the API as they are', async () => {
  calls.length = 0
  const body = { folders: [], rows: [], defaultOrder: '', defaultRecruiterId: '', jobs: {}, groupMode: 'one', grouping: { A: 'auto' }, assign: { 'A/x.pdf': '', 'A/y.pdf': 'A / Bob' } }
  await importApi.check(body)
  assert.equal(calls[0].path, '/candidate-import/check')
  assert.deepEqual(calls[0].body, body)
})

test('importOne sends the files of the plan (moved files included, left-out ones not) from the picked folder', async () => {
  calls.length = 0
  const picked = groupPicked([file('All/Mixed/ali passport.pdf'), file('All/Mixed/bob passport.pdf'), file('All/Mixed/left out.pdf')]).folders[0]
  // The check moved "bob passport.pdf" to Ali and left "left out.pdf" out.
  const plan = {
    folder: 'Mixed / Ali', name: 'Ali', row: {}, parent: 'Mixed', issues: [],
    files: [{ path: 'ali passport.pdf', skipped: false }, { path: 'bob passport.pdf', skipped: false }, { path: 'big.pdf', skipped: true }],
  }
  await importApi.importOne(picked, plan, { 'bob passport.pdf': 'passport' }, { order: '', recruiterId: '' })
  const { form } = calls[0]
  const spec = JSON.parse(form.get('spec'))
  assert.equal(spec.folder, 'Mixed / Ali')
  assert.deepEqual(spec.files, [{ path: 'ali passport.pdf' }, { path: 'bob passport.pdf', kind: 'passport' }])
  assert.deepEqual(form.getAll('files').map((f) => f.name), ['ali passport.pdf', 'bob passport.pdf'])
})

test('fileTooLarge is true only above 25 MB', () => {
  assert.equal(fileTooLarge({ size: 25 * 1024 * 1024 }), false)
  assert.equal(fileTooLarge({ size: 25 * 1024 * 1024 + 1 }), true)
})

const result = () => ({
  folder: 'Mixed',
  people: [
    { key: 'p1', name: 'Meera Nair', needsLook: false, files: [{ path: 'a.pdf', pages: null }, { path: 'mixed.pdf', pages: [1, 2] }] },
    { key: 'p2', name: 'Tharun Raman', needsLook: true, files: [{ path: 'mixed.pdf', pages: [3] }] },
  ],
  unplaced: [{ path: 'mixed.pdf', pages: [4, 5], reason: 'No name' }, { path: 'photo.jpg', pages: null, reason: 'A picture' }],
  unreadable: [{ path: 'bad.pdf', pages: null, reason: 'Damaged' }],
  files: [{ path: 'a.pdf', pageCount: 2, readable: true, lowConfidence: [] }, { path: 'mixed.pdf', pageCount: 5, readable: true, lowConfidence: [] },
    { path: 'photo.jpg', pageCount: 1, readable: true, lowConfidence: [] }, { path: 'bad.pdf', pageCount: 1, readable: false, lowConfidence: [] }],
  totals: { files: 4, pages: 9, assigned: 5, unplaced: 3, unreadable: 1 },
  stopped: false,
})

test('the summary says how many pages are placed, need a person and are unreadable, and adds up', () => {
  assert.equal(summaryText(result()), '9 pages: 5 placed, 3 need you, 1 unreadable')
  const placement = { [leftoverKey('unplaced', result().unplaced[0])]: 'p1', [leftoverKey('unplaced', result().unplaced[1])]: '', [leftoverKey('unreadable', result().unreadable[0])]: 'p2' }
  // 2 pages placed with Meera, the photo left out (decided: no longer waiting for anyone), the unreadable file placed by hand
  assert.equal(summaryText(result(), placement), '9 pages: 8 placed, 0 need you, 0 unreadable, 1 left out')
  const parts = /(\d+) pages: (\d+) placed, (\d+) need you, (\d+) unreadable/.exec(summaryText(result()))
  assert.equal(Number(parts[2]) + Number(parts[3]) + Number(parts[4]), Number(parts[1]))
})

test('once every row is placed the summary says "N placed, 0 need you" and nothing waits', () => {
  const all = { [leftoverKey('unplaced', result().unplaced[0])]: 'p1', [leftoverKey('unplaced', result().unplaced[1])]: 'p2', [leftoverKey('unreadable', result().unreadable[0])]: 'p1' }
  assert.equal(summaryText(result(), all), '9 pages: 9 placed, 0 need you, 0 unreadable')
  assert.equal(waiting(result(), all).length, 0)
  // a row left out of everything is decided too, but an unreadable file left out is still unreadable
  const out = { ...all, [leftoverKey('unreadable', result().unreadable[0])]: '' }
  assert.equal(summaryText(result(), out), '9 pages: 8 placed, 0 need you, 1 unreadable')
})

const plan = (parent, folder, ...paths) => ({ parent, folder, files: paths.map((path) => (typeof path === 'string' ? { path, skipped: false } : path)) })

test('the header counts the candidates and files the list shows, a shared file once', () => {
  assert.deepEqual(listCounts([]), { candidates: 0, files: 0, unassigned: 0 })
  assert.deepEqual(listCounts([plan('Mixed', 'Mixed', 'a.pdf', 'b.pdf', 'c.pdf')]), { candidates: 1, files: 3, unassigned: 0 })
  // two people read from one folder: the scan both own and the group ticket count once, a skipped file not at all
  const people = [plan('Mixed', 'Mixed / Tharun', 'a.pdf', 'both.pdf', 'ticket.pdf'), plan('Mixed', 'Mixed / Meera', 'b.pdf', 'both.pdf', 'ticket.pdf', { path: 'big.pdf', skipped: true })]
  assert.deepEqual(listCounts(people), { candidates: 2, files: 4, unassigned: 0 })
  // the same file name in two different folders is two files
  assert.deepEqual(listCounts([plan('A', 'A', 'cv.pdf'), plan('B', 'B', 'cv.pdf')]), { candidates: 2, files: 2, unassigned: 0 })
  // files the names left out count apart, until a person gives them to someone
  const named = [{ ...plan('M', 'M / Ravi', 'ravi.pdf', 'moved.pdf'), leftOut: ['scan1.pdf', 'moved.pdf'] }, { ...plan('M', 'M / Kumar', 'kumar.pdf'), leftOut: ['scan1.pdf', 'moved.pdf'] }]
  assert.deepEqual(listCounts(named), { candidates: 2, files: 3, unassigned: 1 })
})

test('importOne sends a name only when one was read from the documents, never the folder name', async () => {
  calls.length = 0
  const picked = groupPicked([file('All/Mixed/a.pdf')]).folders[0]
  const base = { folder: 'Mixed / Ali', name: 'Ali', row: {}, parent: 'Mixed', issues: [], files: [{ path: 'a.pdf', skipped: false }] }
  await importApi.importOne(picked, { ...base, identified: true, name: 'Rashid Khan' }, {}, { order: '', recruiterId: '' }, 'Rashid Khan')
  await importApi.importOne(picked, { ...base, identified: false }, {}, { order: '', recruiterId: '' }, null)
  await importApi.importOne(picked, { ...base, identified: false }, {}, { order: '', recruiterId: '' })
  const first = JSON.parse(calls[0].form.get('spec'))
  assert.equal(first.name, 'Rashid Khan')
  assert.equal(first.nameFromFiles, true)
  for (const call of calls.slice(1)) {
    const spec = JSON.parse(call.form.get('spec'))
    assert.equal('name' in spec, false)
    assert.equal('nameFromFiles' in spec, false)
  }
})

test('peopleFor sends each person with their files and pages; rows a person placed are added, whole files win', () => {
  const people = peopleFor(result())
  assert.deepEqual(people.map((p) => p.name), ['Meera Nair', 'Tharun Raman'])
  assert.deepEqual(people[0].files, [{ path: 'a.pdf' }, { path: 'mixed.pdf', pages: [1, 2] }])
  assert.equal(people[1].needsLook, true)
  const placed = peopleFor(result(), { [leftoverKey('unplaced', result().unplaced[0])]: 'p1', [leftoverKey('unplaced', result().unplaced[1])]: 'p2', [leftoverKey('unreadable', result().unreadable[0])]: '' })
  assert.deepEqual(placed[0].files, [{ path: 'a.pdf' }, { path: 'mixed.pdf', pages: [1, 2, 4, 5] }])
  assert.deepEqual(placed[1].files, [{ path: 'mixed.pdf', pages: [3] }, { path: 'photo.jpg' }])
  // a person who is given the rest of a file they already hold in part gets the pages merged; the left-out file is nowhere
  assert.ok(!JSON.stringify(placed).includes('bad.pdf'))
})

test('rows nobody owns are waiting until a person places them or leaves them out', () => {
  assert.equal(leftovers(result()).length, 3)
  assert.equal(waiting(result()).length, 3)
  assert.equal(waiting(result(), { [leftoverKey('unreadable', result().unreadable[0])]: '' }).length, 2)
})

test('check sends the identified people and importOne sends the pages of a shared file', async () => {
  calls.length = 0
  const people = { Mixed: peopleFor(result()) }
  await importApi.check({ folders: [], rows: [], defaultOrder: '', defaultRecruiterId: '', jobs: {}, groupMode: 'auto', grouping: {}, assign: {}, people })
  assert.deepEqual(calls[0].body.people.Mixed[1].files, [{ path: 'mixed.pdf', pages: [3] }])
  const picked = groupPicked([file('All/Mixed/a.pdf'), file('All/Mixed/mixed.pdf')]).folders[0]
  const plan = { folder: 'Mixed / Tharun Raman', name: 'Tharun Raman', row: {}, parent: 'Mixed', issues: [], files: [{ path: 'mixed.pdf', skipped: false, pages: [3] }] }
  await importApi.importOne(picked, plan, {}, { order: '', recruiterId: '' })
  assert.deepEqual(JSON.parse(calls[1].form.get('spec')).files, [{ path: 'mixed.pdf', pages: [3] }])
})

test('the progress poll backs off 3 s, then 6 s, then 10 s, inside the old cap of 100 rounds', () => {
  assert.equal(POLL_ROUNDS, 100)
  assert.deepEqual([1, 10, 11, 20, 21, 100].map(pollDelay), [3000, 3000, 6000, 6000, 10000, 10000])
})

test('after the first full load only the candidates that still have open readings are asked about', () => {
  const item = (candidateId, open) => ({ candidateId, reading: { total: 2, open, done: 2 - open, review: 0, failed: 0 } })
  assert.deepEqual(openReadings([item('a', 0), item('b', 2), item('c', 1)]), ['b', 'c'])
  assert.deepEqual(openReadings([item('a', 0)]), [])
  assert.deepEqual(openReadings([]), [])
})

test('counts are worded in the right number: "1 file", "2 files"', () => {
  assert.deepEqual([0, 1, 2].map((n) => countOf(n, 'file')), ['0 files', '1 file', '2 files'])
  assert.equal(countOf(1, 'document'), '1 document')
})

test('"Read the documents" always says something: what was queued, nothing to read, or all read already', () => {
  assert.equal(readAgainMessage(1, 1).text, 'Reading 1 document. The details fill in as each one is read.')
  assert.match(readAgainMessage(3, 3).text, /^Reading 3 documents\./)
  const none = readAgainMessage(0, 0)
  assert.equal(none.text, 'Nothing to read: these candidates have no resume, passport, Aadhaar or education files yet.')
  assert.equal(none.tone, 'warn')
  assert.deepEqual(readAgainMessage(0, 4), { text: 'Everything has been read already.' })
})

// ------------------------------------------------------------------------------ same-file check, import key, duplicate answers

const sha = async (text) => Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)))).map((b) => b.toString(16).padStart(2, '0')).join('')

test('hashFile gives the lowercase hex SHA-256 of the bytes, once per file', async () => {
  const f = new File(['abc'], 'a.pdf')
  assert.equal(await hashFile(f), 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  assert.equal(await hashFile(f), await sha('abc'))
  // an empty file has nothing to hash
  assert.equal(await hashFile(new File([], 'empty.pdf')), null)
})

test('hashPicked hashes every picked file, three at a time, and reports progress', async () => {
  const picked = groupPicked([file('All/A/a.pdf', 'one'), file('All/A/b.pdf', 'two'), file('All/B/c.pdf', 'three'), file('All/B/d.pdf', 'four'), file('All/B/e.pdf', 'five')]).folders
  const seen = []
  const out = await hashPicked(picked, { onProgress: (done, total) => seen.push([done, total]) })
  assert.deepEqual(Object.keys(out), ['A', 'B'])
  assert.equal(out.A['a.pdf'], await sha('one'))
  assert.equal(out.B['e.pdf'], await sha('five'))
  assert.deepEqual(seen[0], [0, 5])
  assert.deepEqual(seen[seen.length - 1], [5, 5])
})

test('hashPicked works one folder file at a time with limited concurrency and can be stopped', async () => {
  const picked = groupPicked(Array.from({ length: 10 }, (_, i) => file(`All/A/f${i}.pdf`, `file ${i}`))).folders
  let running = 0
  let most = 0
  const original = crypto.subtle.digest.bind(crypto.subtle)
  crypto.subtle.digest = async (...args) => { running += 1; most = Math.max(most, running); await new Promise((r) => setTimeout(r, 2)); running -= 1; return original(...args) }
  try {
    await hashPicked(picked)
    assert.ok(most <= 3 && most >= 1, `at most 3 at once, saw ${most}`)
    const fresh = groupPicked(Array.from({ length: 6 }, (_, i) => file(`All/B/g${i}.pdf`, `other ${i}`))).folders
    let calls = 0
    const stopped = await hashPicked(fresh, { stop: () => calls++ >= 3 })
    assert.ok(Object.keys(stopped.B ?? {}).length < 6)
  } finally {
    crypto.subtle.digest = original
  }
})

test('without crypto.subtle (an insecure page) or when hashing fails, nothing is hashed and nothing throws', async () => {
  const picked = groupPicked([file('All/A/a.pdf', 'zzz'), file('All/A/b.pdf', 'yyy')]).folders
  const original = crypto.subtle.digest.bind(crypto.subtle)
  crypto.subtle.digest = async () => { throw new Error('boom') }
  try {
    assert.deepEqual(await hashPicked(picked), {})
  } finally {
    crypto.subtle.digest = original
  }
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'crypto')
  Object.defineProperty(globalThis, 'crypto', { value: {}, configurable: true })
  try {
    assert.deepEqual(await hashPicked(groupPicked([file('All/A/c.pdf', 'xxx')]).folders), {})
    assert.equal(await hashFile(new File(['q'], 'q.pdf')), null)
  } finally {
    Object.defineProperty(globalThis, 'crypto', saved)
  }
})

test('check sends the sha256 of each file when it is given', async () => {
  calls.length = 0
  const body = { folders: [{ folder: 'A', files: [{ path: 'a.pdf', sha256: 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad' }, { path: 'b.pdf', kind: 'passport' }] }], rows: [], defaultOrder: '', defaultRecruiterId: '', jobs: {}, groupMode: 'auto', grouping: {}, assign: {} }
  await importApi.check(body)
  assert.equal(calls[0].body.folders[0].files[0].sha256, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
  assert.equal('sha256' in calls[0].body.folders[0].files[1], false)
})

test('importOne sends the importKey and the duplicateAction in the spec, and only when given', async () => {
  calls.length = 0
  const picked = groupPicked([file('All/A/a.pdf')]).folders[0]
  const plan = { folder: 'A', name: 'A', row: {}, parent: 'A', issues: [], files: [{ path: 'a.pdf', skipped: false }] }
  await importApi.importOne(picked, plan, {}, { order: '', recruiterId: '' })
  await importApi.importOne(picked, plan, {}, { order: '', recruiterId: '' }, null, { importKey: 'key-1', duplicateAction: 'add_documents' })
  await importApi.importOne(picked, plan, {}, { order: '', recruiterId: '' }, 'Rashid Khan', { importKey: 'key-1' })
  const specs = calls.map((call) => JSON.parse(call.form.get('spec')))
  assert.equal('importKey' in specs[0], false)
  assert.equal('duplicateAction' in specs[0], false)
  assert.equal(specs[1].importKey, 'key-1')
  assert.equal(specs[1].duplicateAction, 'add_documents')
  assert.equal(specs[2].importKey, 'key-1')
  assert.equal('duplicateAction' in specs[2], false)
  assert.equal(specs[2].name, 'Rashid Khan')
})

test('newImportKey makes a different key every time', () => {
  const keys = new Set(Array.from({ length: 20 }, () => newImportKey()))
  assert.equal(keys.size, 20)
  for (const key of keys) assert.ok(key.length >= 8)
})

test('importOne parses the four answers: imported, duplicate, already_imported, documents_added', async () => {
  const picked = groupPicked([file('All/A/a.pdf')]).folders[0]
  const plan = { folder: 'A', name: 'A', row: {}, parent: 'A', issues: [], files: [{ path: 'a.pdf', skipped: false }] }
  const ask = async (reply) => { globalThis.__importApiReply = reply; try { return await importApi.importOne(picked, plan, {}, { order: '', recruiterId: '' }) } finally { delete globalThis.__importApiReply } }

  const imported = await ask({ status: 'imported', folder: 'A', action: 'in_progress', careId: 'CI-150', candidateId: 'c-150', nameSource: 'pending', documents: [{ slot: 'passport', label: 'Passport', fileName: 'x.pdf', versions: 1 }],
    duplicates: [{ label: 'Passport', file: 'a.pdf', pages: [2], message: 'Page 2 repeats page 1', originalKept: true }], issues: [{ level: 'info', message: 'Checked against the CRM while importing: passport.' }] })
  assert.equal(imported.status, 'imported')
  assert.equal(imported.careId, 'CI-150')
  assert.equal(imported.nameSource, 'pending')
  assert.equal(imported.duplicates.length, 1)
  assert.equal(imported.duplicates[0].message, 'Page 2 repeats page 1')

  // an older server sends no status: a normal import
  assert.equal((await ask({ folder: 'A', action: 'deploy', issues: [] })).status, 'imported')

  const duplicate = await ask({ status: 'duplicate', action: 'skip', careId: 'CI-139', candidateId: 'c-139', ref: 'CND-139', name: 'Meera Nair', matchedBy: 'passport', strength: 'certain', files: 2,
    choices: ['skip', 'add_documents', 'import_anyway'], message: 'Already in the CRM as CI-139', folder: 'A', issues: [] })
  assert.equal(duplicate.status, 'duplicate')
  assert.equal(duplicate.matchedBy, 'passport')
  // a certain match is never offered "import anyway", even if the server lists it
  assert.deepEqual(duplicate.choices, ['skip', 'add_documents'])
  const possible = await ask({ status: 'duplicate', action: 'skip', strength: 'possible', matchedBy: 'phone', choices: ['skip', 'add_documents', 'import_anyway'], issues: [] })
  assert.deepEqual(possible.choices, ['skip', 'add_documents', 'import_anyway'])
  assert.deepEqual((await ask({ status: 'duplicate', action: 'skip', strength: 'possible', issues: [] })).choices, ['skip', 'add_documents'])

  const again = await ask({ status: 'already_imported', matchedBy: 'import key', careId: 'CI-150', candidateId: 'c-150', folder: 'A', action: 'in_progress' })
  assert.equal(again.status, 'already_imported')
  assert.equal(again.matchedBy, 'import key')
  assert.deepEqual(again.issues, [])

  const added = await ask({ status: 'documents_added', careId: 'CI-139', candidateId: 'c-139', name: 'Meera Nair', matchedBy: 'passport', strength: 'certain',
    documents: [{ slot: 'passport', label: 'Passport', fileName: 'P.pdf', pages: 2, added: true, alreadyFiled: false }, { slot: 'aadhaar', label: 'Aadhaar', fileName: 'A.pdf', pages: 1, added: false, alreadyFiled: true, note: 'same file' }], issues: [] })
  assert.equal(added.status, 'documents_added')
  assert.equal(added.documents[0].added, true)
  assert.equal(added.documents[1].alreadyFiled, true)
  assert.equal(added.documents[1].note, 'same file')
})

test('parseImportResult survives an empty or odd answer', () => {
  assert.equal(parseImportResult(null).status, 'imported')
  assert.deepEqual(parseImportResult(undefined).issues, [])
  assert.equal(parseImportResult({ status: 'surprise' }).status, 'imported')
})
