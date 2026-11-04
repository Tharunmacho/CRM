import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function loadSource(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { cappedNote, defaultView, overviewCards, overviewCsvRows, overviewRows, overviewTabs, plural, waitLabel } = await loadSource('../src/features/dashboard/overview.ts')

const item = (id, state, days, extra = {}) => ({ applicationId: id, candidateId: `c-${id}`, candidate: id, orderId: 'o', order: 'ORD-1', designation: 'Welder', company: 'Co', client: 'Client',
  stage: 5, stageLabel: 'Offer letter', status: 'active', state, ownerId: 'u', owner: 'Ann', since: '2026-10-01T00:00:00Z', days, ...extra })
const overview = {
  idleDays: 7,
  offer: { current: 2, issued: 3, itemsTotal: 5, items: [item('a', 'current', 6), item('b', 'current', 2), item('c', 'done', 4), item('d', 'done', 9), item('e', 'done', 20)] },
  visa: { current: 1, received: 4, stamped: 1, itemsTotal: 1, items: [item('v', 'current', 3, { stage: 9 })] },
  departure: { current: 0, departed: 0, itemsTotal: 0, items: [] },
  idle: { count: 2, longestDays: 31, itemsTotal: 2, items: [item('i1', 'current', 31), item('i2', 'current', 9)] },
}

test('each card carries the number, the supporting line and the rule behind it', () => {
  const [offer, visa, departure, idle] = overviewCards(overview)
  assert.deepEqual([offer.key, visa.key, departure.key, idle.key], ['offer', 'visa', 'departure', 'idle'])
  assert.equal(offer.label, 'Overall offer letter')
  assert.equal(offer.value, 2)
  assert.equal(offer.detail, '3 offers issued in total')
  assert.equal(visa.value, 1)
  assert.equal(visa.detail, '4 visas received · 1 stamped')
  assert.equal(departure.value, 0)
  assert.equal(departure.detail, 'No one has departed yet')
  assert.equal(idle.value, 2)
  assert.equal(idle.detail, 'No progress for 7+ days · longest 31 days')
  assert.equal(idle.warn, true)
  assert.equal(offer.warn, false)
  for (const card of [offer, visa, departure, idle]) assert.ok(card.hint.length > 40)
})

test('empty cards say so in plain words and idle stops warning', () => {
  const empty = { ...overview, offer: { current: 0, issued: 0, itemsTotal: 0, items: [] }, visa: { current: 0, received: 0, stamped: 0, itemsTotal: 0, items: [] }, idle: { count: 0, longestDays: 0, itemsTotal: 0, items: [] } }
  const [offer, visa, , idle] = overviewCards(empty)
  assert.equal(offer.detail, 'No offers issued yet')
  assert.equal(visa.detail, 'No visas received yet')
  assert.equal(idle.detail, 'Nobody has waited 7+ days')
  assert.equal(idle.warn, false)
  assert.equal(plural(1, 'offer'), '1 offer')
  assert.equal(plural(1234, 'day'), '1,234 days')
})

test('tabs split the list into waiting and finished and their counts add up', () => {
  const tabs = overviewTabs(overview, 'offer')
  assert.deepEqual(tabs.map(tab => [tab.view, tab.count]), [['current', 2], ['done', 3], ['all', 5]])
  assert.deepEqual(overviewRows(overview, 'offer', 'current').map(row => row.applicationId), ['a', 'b'])
  assert.deepEqual(overviewRows(overview, 'offer', 'done').map(row => row.applicationId), ['c', 'd', 'e'])
  assert.equal(overviewRows(overview, 'offer', 'all').length, 5)
  assert.deepEqual(overviewTabs(overview, 'idle'), [])
  assert.equal(overviewRows(overview, 'idle', 'current').length, 2)
})

test('the list opens on the people still waiting, otherwise on everything', () => {
  assert.equal(defaultView(overview, 'offer'), 'current')
  assert.equal(defaultView(overview, 'departure'), 'all')
  assert.equal(defaultView(overview, 'idle'), 'all')
})

test('a capped list says how many more there are', () => {
  assert.equal(cappedNote(overview, 'offer'), null)
  const capped = { ...overview, offer: { ...overview.offer, itemsTotal: 1200 } }
  assert.match(cappedNote(capped, 'offer'), /first 5 of 1,200/)
})

test('waiting and finished rows read naturally', () => {
  assert.equal(waitLabel({ state: 'current', days: 0 }), 'Since today')
  assert.equal(waitLabel({ state: 'current', days: 1 }), '1 day')
  assert.equal(waitLabel({ state: 'current', days: 12 }), '12 days')
  assert.equal(waitLabel({ state: 'done', days: 0 }), 'Done today')
  assert.equal(waitLabel({ state: 'done', days: 4 }), '4 days ago')
})

test('the export carries every overview number', () => {
  const rows = overviewCsvRows(overview)
  const map = Object.fromEntries(rows)
  assert.equal(map['Offer letter: waiting now'], 2)
  assert.equal(map['Offer letters issued in total'], 3)
  assert.equal(map['Visas received in total'], 4)
  assert.equal(map['Visas stamped in total'], 1)
  assert.equal(map['Departed in total'], 0)
  assert.equal(map['Idle stage: no progress for 7+ days'], 2)
  assert.equal(map['Idle stage: longest wait (days)'], 31)
  assert.equal(rows.length, 9)
})
