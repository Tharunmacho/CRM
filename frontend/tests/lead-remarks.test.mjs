import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const cache = new Map()
async function load(path) {
  if (cache.has(path)) return cache.get(path)
  const url = new URL(path, import.meta.url)
  let source = await readFile(url, 'utf8')
  for (const spec of [...source.matchAll(/from '(\.[^']+)'/g)].map((match) => match[1])) {
    const loaded = await load(new URL(`${spec}.ts`, url).href)
    source = source.replaceAll(`from '${spec}'`, `from '${loaded.__url}'`)
  }
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  const dataUrl = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
  const loaded = { ...(await import(dataUrl)), __url: dataUrl }
  cache.set(path, loaded)
  return loaded
}
const r = await load('../src/features/leads/lead-remarks-model.ts')

const NOW = new Date(2026, 10, 13, 15, 0, 0) // 13 Nov 2026, local 3 pm
const at = (day, hour, minute = 0) => new Date(2026, 10, day, hour, minute).toISOString()
const call = (id, outcome, remark, when, extra = {}) => ({
  id, kind: 'call', userId: 'u1', userName: 'Asha Rao', createdAt: when,
  summary: `${outcome.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())}${remark ? `: ${remark}` : ''}`,
  meta: { outcome, from: 'new', to: 'new', callNo: 1, ...extra },
})

test('parseCall splits the stored summary into outcome and the staff member\'s remark', () => {
  assert.deepEqual(r.parseCall(call('a', 'interested', 'Will send resume: tomorrow', at(12, 9))), { outcome: 'interested', remark: 'Will send resume: tomorrow' })
  assert.deepEqual(r.parseCall(call('a', 'no_answer', '', at(12, 9))), { outcome: 'no_answer', remark: '' })
  assert.deepEqual(r.parseCall({ id: 'x', kind: 'call', summary: 'Wrong number: dead line', createdAt: at(1, 1) }), { outcome: 'wrong_number', remark: 'dead line' })
  assert.deepEqual(r.parseCall({ id: 'x', kind: 'call', summary: 'Spoke at length about visa', createdAt: at(1, 1) }), { outcome: '', remark: 'Spoke at length about visa' })
})

test('remark cards: newest first, outcome chip, calls without text stay out, saved notes pinned once', () => {
  const activities = [
    call('c1', 'no_answer', '', at(10, 9)),
    call('c2', 'interested', 'Wants Dubai', at(11, 10), { from: 'new', to: 'interested' }),
    call('c3', 'answered', 'Asked to call back', at(12, 11)),
    { id: 'n1', kind: 'note', userName: 'Asha Rao', summary: 'Prefers evening calls', createdAt: at(12, 12) },
    { id: 'n0', kind: 'note', userName: 'Ben', summary: 'Old note', createdAt: at(9, 12) },
    { id: 'n2', kind: 'note', userName: 'Ben', summary: 'Notes cleared', createdAt: at(8, 12) },
  ]
  const cards = r.remarkCards(activities, 'Prefers evening calls')
  assert.deepEqual(cards.map((c) => c.id), ['pinned-notes', 'c3', 'c2', 'n0'])
  assert.equal(cards[0].kind, 'pinned')
  assert.equal(cards[0].who, 'Asha Rao')
  assert.equal(cards[1].outcome.label, 'Spoke to them')
  assert.equal(cards[2].outcome.tone, 'ok')
  assert.equal(cards[2].text, 'Wants Dubai')
  assert.deepEqual(r.remarkCards([], '   '), [])
  assert.deepEqual(r.remarkCards([], null), [])
})

test('timeline events read as plain English and never repeat the remark text', () => {
  const e = r.timelineEvent(call('c', 'interested', 'secret remark text', at(12, 9), { from: 'new', to: 'interested' }))
  assert.equal(e.icon, 'call')
  assert.equal(e.title, 'Called: they are interested')
  assert.equal(e.detail, 'Remark added · Status New → Interested')
  assert.ok(!JSON.stringify(e).includes('secret'))
  assert.equal(r.timelineEvent(call('c', 'no_answer', '', at(12, 9))).detail, '')
  assert.equal(r.timelineEvent({ id: 's', kind: 'status', summary: 'x', meta: { from: 'interested', to: 'resume_requested' }, createdAt: at(1, 1) }).title, 'Changed status from Interested to Resume requested')
  assert.equal(r.timelineEvent({ id: 'n', kind: 'note', summary: 'Notes cleared', createdAt: at(1, 1) }).title, 'Cleared the notes')
  assert.equal(r.timelineEvent({ id: 'n', kind: 'note', summary: 'Call him', createdAt: at(1, 1) }).title, 'Updated the notes')
  const resume = r.timelineEvent({ id: 'r', kind: 'resume', summary: 'Resume uploaded: cv.pdf (version 2)', createdAt: at(1, 1) })
  assert.deepEqual([resume.icon, resume.title, resume.detail], ['resume', 'Uploaded a resume', 'cv.pdf (version 2)'])
  const odd = r.timelineEvent({ id: 'o', kind: 'brand_new_kind', summary: '', createdAt: at(1, 1) })
  assert.deepEqual([odd.icon, odd.title], ['other', 'Brand new kind'])
})

test('timeline days: newest first, grouped by local day with Today / Yesterday labels', () => {
  const days = r.timelineDays([
    call('a', 'no_answer', '', at(11, 9)),
    call('b', 'answered', 'x', at(13, 9)),
    { id: 'c', kind: 'assign', summary: 'Assigned to Asha', userName: 'Admin', createdAt: at(13, 8) },
    call('d', 'interested', 'y', at(12, 18)),
  ], NOW)
  assert.deepEqual(days.map((d) => d.label), ['Today', 'Yesterday', 'Wednesday, 11 Nov'])
  assert.deepEqual(days[0].events.map((e) => e.id), ['b', 'c'])
  assert.deepEqual(r.timelineDays([], NOW), [])
  assert.equal(r.dayLabel(new Date(2025, 11, 31, 10).toISOString(), NOW), 'Wednesday, 31 Dec 2025')
})

test('relative time', () => {
  assert.equal(r.relativeTime(new Date(NOW.getTime() - 20_000).toISOString(), NOW), 'just now')
  assert.equal(r.relativeTime(at(13, 14, 59), NOW), '1 min ago')
  assert.equal(r.relativeTime(at(13, 14, 30), NOW), '30 min ago')
  assert.equal(r.relativeTime(at(13, 9), NOW), '6 h ago')
  assert.equal(r.relativeTime(at(12, 20), NOW), 'Yesterday')
  assert.equal(r.relativeTime(at(10, 9), NOW), '3 days ago')
  assert.equal(r.relativeTime(at(1, 9), NOW), '1 Nov 2026')
  assert.equal(r.relativeTime(null, NOW), '')
  assert.equal(r.relativeTime('garbage', NOW), '')
})

test('stats prefer the lead\'s own call counter', () => {
  const activities = [call('a', 'no_answer', '', at(11, 9))]
  assert.deepEqual(r.remarkStats(activities, [], 5), { calls: 5, remarks: 0 })
  assert.deepEqual(r.remarkStats(activities, [], null), { calls: 1, remarks: 0 })
})
