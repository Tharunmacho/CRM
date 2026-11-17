import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** ingested-view.ts and inbox-card.ts are pure (their only imports are types): transpile and load them as they are. */
const load = async (path) => {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}
const v = await load('../src/features/inbox/ingested-view.ts')
const { inboxCard } = await load('../src/features/dashboard/inbox-card.ts')

const NOW = Date.parse('2026-11-17T12:00:00Z')
const item = (over = {}) => ({
  id: 'i1', messageId: 'm1', accountId: 'a1', accountAddress: 'hr@care.in', receivedAt: '2026-11-17T11:00:00Z', processedAt: null,
  from: { name: 'Anil Kumar', address: 'anil@mail.com' }, subject: 'Resume', attachments: [], attachmentCount: 0,
  status: 'ingested', candidate: { id: 'c1', ref: 'CAN-9', careId: 'CI-103', name: 'Anil Kumar' }, created: true, note: null, documentsFiled: null, ...over,
})

// ------------------------------------------------------------------------------ request

test('the list query always names account, status, page and a page size within 10..50', () => {
  const q = new URLSearchParams(v.ingestedQuery({ account: '', status: 'all', q: '  ', page: 0, pageSize: 25 }))
  assert.equal(q.get('account'), 'all'); assert.equal(q.get('status'), 'all'); assert.equal(q.get('page'), '0'); assert.equal(q.get('page_size'), '25'); assert.equal(q.has('q'), false)
  const w = new URLSearchParams(v.ingestedQuery({ account: 'a1', status: 'review', q: ' anil ', page: 3, pageSize: 500 }))
  assert.equal(w.get('account'), 'a1'); assert.equal(w.get('status'), 'review'); assert.equal(w.get('q'), 'anil'); assert.equal(w.get('page'), '3'); assert.equal(w.get('page_size'), '50')
  assert.equal(new URLSearchParams(v.ingestedQuery({ account: 'a1', status: 'all', q: '', page: -2, pageSize: 3 })).get('page'), '0')
  assert.equal(new URLSearchParams(v.ingestedQuery({ account: 'a1', status: 'all', q: '', page: 0, pageSize: 3 })).get('page_size'), '10')
})

// ------------------------------------------------------------------------------ badges and result text

test('every status has its own coloured badge, and only Waiting spins', () => {
  assert.deepEqual(v.statusBadge({ status: 'ingested', created: true }), { tone: 'green', label: 'Candidate created', spinner: false })
  assert.deepEqual(v.statusBadge({ status: 'ingested', created: false }), { tone: 'green', label: 'Documents added', spinner: false })
  assert.equal(v.statusBadge({ status: 'review', created: null }).tone, 'amber')
  assert.equal(v.statusBadge({ status: 'failed', created: null }).tone, 'red')
  assert.equal(v.statusBadge({ status: 'ignored', created: null }).tone, 'grey')
  const waiting = v.statusBadge({ status: 'pending', created: null })
  assert.deepEqual([waiting.tone, waiting.label, waiting.spinner], ['blue', 'Waiting', true])
})

test('an ingested row reads "Candidate created · CI-103 · Name" and documents added says how many were filed', () => {
  assert.equal(v.candidateLabel(item().candidate), 'CI-103 · Anil Kumar')
  assert.equal(v.candidateLabel({ id: 'c', ref: 'CAN-9', careId: '', name: 'Anil' }), 'CAN-9 · Anil')
  assert.equal(v.candidateLabel(null), '')
  assert.deepEqual(v.resultText(item()), { headline: 'Candidate created · CI-103 · Anil Kumar', detail: '' })
  assert.deepEqual(v.resultText(item({ created: false, documentsFiled: 2 })), { headline: 'Documents added · CI-103 · Anil Kumar', detail: '2 documents filed' })
  assert.equal(v.resultText(item({ created: false, documentsFiled: 1 })).detail, '1 document filed')
  assert.equal(v.resultText(item({ candidate: null })).headline, 'Candidate created')
})

test('review, failed and ignored rows show the backend note, with a plain fallback when there is none', () => {
  assert.deepEqual(v.resultText(item({ status: 'review', candidate: null, created: null, note: 'Passport number unclear' })), { headline: 'Needs review', detail: 'Passport number unclear' })
  assert.equal(v.resultText(item({ status: 'failed', candidate: null, created: null, note: 'Corrupt PDF' })).detail, 'Corrupt PDF')
  assert.equal(v.resultText(item({ status: 'ignored', candidate: null, created: null, note: 'No resume found' })).detail, 'No resume found')
  assert.match(v.resultText(item({ status: 'ignored', candidate: null, created: null, note: '  ' })).detail, /No candidate/)
  assert.match(v.resultText(item({ status: 'pending', candidate: null, created: null })).detail, /Reading/)
})

// ------------------------------------------------------------------------------ chips and accounts

test('chip counts are blank until the summary loads, and "All" is the sum of the five outcomes', () => {
  assert.equal(v.chipCounts(null).ingested, null)
  const counts = v.chipCounts({ ingested: 300, createdCandidates: 250, review: 5, failed: 3, ignored: 4, pending: 0, accounts: [] })
  assert.deepEqual(counts, { all: 312, ingested: 300, review: 5, failed: 3, ignored: 4, pending: 0 })
  assert.equal(v.countText(null), '–'); assert.equal(v.countText(1234), '1,234')
  assert.equal(v.needsAttention({ review: 5, failed: 3 }), 8); assert.equal(v.needsAttention(null), 0)
  assert.deepEqual(v.STATUS_FILTERS.map((f) => f.key), ['all', 'ingested', 'review', 'failed', 'ignored', 'pending'])
})

test('a mailbox is green when importing, amber when it reports a problem, grey when auto-import is off', () => {
  const base = { connectionStatus: 'connected', autoImport: true, lastError: null }
  assert.equal(v.accountHealth(base).tone, 'green')
  assert.equal(v.accountHealth({ ...base, lastError: 'Login expired' }).tone, 'amber')
  assert.equal(v.accountHealth({ ...base, connectionStatus: 'error' }).tone, 'amber')
  assert.equal(v.accountHealth({ ...base, autoImport: false }).tone, 'grey')
  assert.equal(v.accountHealth({ ...base, autoImport: false }).label, 'Auto-import off')
  assert.equal(v.accountHealth({ ...base, connectionStatus: 'disconnected' }).tone, 'grey')
  assert.equal(v.accountHealth({ ...base, autoImport: false, lastError: 'x' }).tone, 'amber')
  assert.equal(v.accountTotal({ ingested: 10, review: 2, failed: 1, ignored: 3, pending: 4 }), 20)
  const title = v.accountTitle({ id: 'a', address: 'hr@care.in', provider: 'gmail', connectionStatus: 'connected', autoImport: true, lastSyncAt: '2026-11-17T11:55:00Z', lastError: null, ingested: 1, review: 0, failed: 0, ignored: 0, pending: 0 }, NOW)
  assert.equal(title, 'hr@care.in · Importing · Last checked 5 min ago')
})

// ------------------------------------------------------------------------------ attachments and names

test('at most three attachment names show and the rest collapse into "+N"', () => {
  const files = (n) => Array.from({ length: n }, (_, i) => ({ name: `f${i}.pdf`, sizeKb: 1 }))
  assert.deepEqual(v.attachmentChips({ attachments: files(2), attachmentCount: 2 }), { shown: ['f0.pdf', 'f1.pdf'], extra: 0 })
  assert.deepEqual(v.attachmentChips({ attachments: files(5), attachmentCount: 5 }), { shown: ['f0.pdf', 'f1.pdf', 'f2.pdf'], extra: 2 })
  // The server may list fewer names than the real count.
  assert.deepEqual(v.attachmentChips({ attachments: files(3), attachmentCount: 7 }), { shown: ['f0.pdf', 'f1.pdf', 'f2.pdf'], extra: 4 })
  assert.deepEqual(v.attachmentChips({ attachments: [], attachmentCount: 0 }), { shown: [], extra: 0 })
})

test('the sender shows as "Name <address>", or the address alone', () => {
  assert.equal(v.fromLine({ name: 'Anil Kumar', address: 'anil@mail.com' }), 'Anil Kumar <anil@mail.com>')
  assert.equal(v.fromLine({ name: null, address: 'anil@mail.com' }), 'anil@mail.com')
  assert.equal(v.senderName({ name: ' ', address: 'a@b.c' }), 'a@b.c')
  assert.equal(v.senderName({ name: 'Anil', address: 'a@b.c' }), 'Anil')
})

// ------------------------------------------------------------------------------ paging

test('paging reads "Showing 1-25 of 312" and the buttons stop at the ends', () => {
  assert.equal(v.pagingText(0, 25, 312), 'Showing 1-25 of 312')
  assert.equal(v.pagingText(1, 25, 312), 'Showing 26-50 of 312')
  assert.equal(v.pagingText(12, 25, 312, 12), 'Showing 301-312 of 312')
  assert.equal(v.pagingText(0, 25, 7, 7), 'Showing 1-7 of 7')
  assert.equal(v.pagingText(0, 25, 1234), 'Showing 1-25 of 1,234')
  assert.equal(v.pagingText(0, 25, 0), 'No emails')
  assert.equal(v.pageCount(312, 25), 13); assert.equal(v.pageCount(0, 25), 1)
  assert.equal(v.canPrev(0), false); assert.equal(v.canPrev(1), true)
  assert.equal(v.canNext(0, 25, 25), false); assert.equal(v.canNext(0, 25, 26), true); assert.equal(v.canNext(12, 25, 312), false)
})

// ------------------------------------------------------------------------------ time

test('received time is relative, with a plain date for old mail and nothing for junk', () => {
  const ago = (ms) => new Date(NOW - ms).toISOString()
  assert.equal(v.relativeTime(ago(10_000), NOW), 'just now')
  assert.equal(v.relativeTime(ago(5 * 60_000), NOW), '5 min ago')
  assert.equal(v.relativeTime(ago(3 * 3_600_000), NOW), '3 h ago')
  assert.equal(v.relativeTime(ago(30 * 3_600_000), NOW), 'Yesterday')
  assert.equal(v.relativeTime(ago(4 * 86_400_000), NOW), '4 d ago')
  assert.match(v.relativeTime(ago(20 * 86_400_000), NOW), /^\d{1,2} \w{3}$/)
  assert.match(v.relativeTime(ago(400 * 86_400_000), NOW), /\d{4}$/)
  assert.equal(v.relativeTime('nonsense', NOW), ''); assert.equal(v.relativeTime(null, NOW), '')
  assert.equal(v.exactTime('nonsense'), ''); assert.match(v.exactTime('2026-11-17T11:00:00Z'), /2026/)
})

// ------------------------------------------------------------------------------ remembered tab

test('the chosen tab is remembered, and blocked or broken storage falls back to "From email"', () => {
  const memory = () => { const map = new Map(); return { getItem: (k) => map.get(k) ?? null, setItem: (k, x) => void map.set(k, x) } }
  const store = memory()
  assert.equal(v.readMode(store), 'from')
  v.writeMode(store, 'mail'); assert.equal(v.readMode(store), 'mail')
  v.writeMode(store, 'from'); assert.equal(v.readMode(store), 'from')
  store.setItem(v.MODE_KEY, 'junk'); assert.equal(v.readMode(store), 'from')
  const broken = { getItem() { throw new Error('blocked') }, setItem() { throw new Error('blocked') } }
  assert.equal(v.readMode(broken), 'from'); assert.doesNotThrow(() => v.writeMode(broken, 'mail'))
  assert.equal(v.readMode(null), 'from'); assert.doesNotThrow(() => v.writeMode(undefined, 'mail'))
})

// ------------------------------------------------------------------------------ dashboard card

test('the dashboard card leads with candidates from email and a second line without zero parts', () => {
  assert.deepEqual(inboxCard({ ingestedCandidates: 74, review: 3, accounts: 2 }), { value: 74, note: 'candidates from email', detail: '3 need review · 2 accounts' })
  // The server nests the numbers under `intake` because `accounts` is already the mailbox list there.
  assert.deepEqual(inboxCard({ accounts: [{ messageCount: 101 }], intake: { ingestedCandidates: 8, review: 2, accounts: 1 } }), { value: 8, note: 'candidates from email', detail: '2 need review · 1 account' })
  assert.deepEqual(inboxCard({ ingestedCandidates: 74, review: 0, accounts: 2 }), { value: 74, note: 'candidates from email', detail: '2 accounts' })
  assert.deepEqual(inboxCard({ ingestedCandidates: 74, review: 1, accounts: 1 }), { value: 74, note: 'candidates from email', detail: '1 needs review · 1 account' })
  assert.deepEqual(inboxCard({ ingestedCandidates: 1, review: 0, accounts: 0 }), { value: 1, note: 'candidate from email', detail: '' })
  assert.deepEqual(inboxCard({ ingestedCandidates: 0, review: 0, accounts: 0 }), { value: 0, note: 'no email account added', detail: '' })
  assert.deepEqual(inboxCard({ ingestedCandidates: 5 }), { value: 5, note: 'candidates from email', detail: '' })
})

test('the dashboard card accepts the mailbox list as `accounts` and ignores disconnected ones', () => {
  const accounts = [{ messageCount: 50 }, { messageCount: 20, disconnectedAt: '2026-01-01' }, { messageCount: 51 }]
  assert.deepEqual(inboxCard({ ingestedCandidates: 9, review: 2, accounts }), { value: 9, note: 'candidates from email', detail: '2 need review · 2 accounts' })
})

test('without the new keys the card keeps the old numbers: total emails and the account count', () => {
  const accounts = [{ messageCount: 100 }, { messageCount: 1, disconnectedAt: null }]
  assert.deepEqual(inboxCard({ accounts }), { value: 101, note: 'emails · 2 accounts', detail: '' })
  assert.deepEqual(inboxCard({ accounts: [{ messageCount: 1 }] }), { value: 1, note: 'email · 1 account', detail: '' })
  assert.deepEqual(inboxCard({ accounts: [] }), { value: 0, note: 'no email account added', detail: '' })
  assert.deepEqual(inboxCard(null), { value: null, note: 'loading', detail: '' })
  assert.deepEqual(inboxCard({}), { value: null, note: 'loading emails', detail: '' })
})
