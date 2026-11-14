import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const camp = await load('../src/features/leads/campaigns-model.ts')
const view = await load('../src/features/leads/leads-view-model.ts')
const wa = await load('../src/features/leads/wa-template-model.ts')
const origin = await load('../src/features/leads/lead-origin.ts')
const lead = await load('../src/features/leads/lead-model.ts')

const stat = (campaign, leads, over = {}) => ({
  campaign, form: null, leads, newToday: 0, last7Days: 0, assigned: 0, assignedPct: 0, contacted: 0, contactedPct: 0, interested: 0, resumeReceived: 0,
  converted: 0, conversionRate: 0, lastUploadAt: null, platform: { fb: 0, ig: 0, other: 0 }, topPositions: [], topCountries: [], ...over,
})

/* ---------------------------------------------------------------- campaigns */

test('campaigns sort by any column in both directions and keep unnamed campaigns last when sorting by name', () => {
  const rows = [stat('Qatar-B', 8, { conversionRate: 0 }), stat('Dubai-A', 10, { conversionRate: 10 }), stat(null, 2, { conversionRate: 50 }), stat('Kuwait-C', 2, { conversionRate: 25 })]
  assert.deepEqual(camp.sortCampaigns(rows, 'leads', 'desc').map((r) => r.campaign), ['Dubai-A', 'Qatar-B', 'Kuwait-C', null])
  assert.deepEqual(camp.sortCampaigns(rows, 'conversionRate', 'desc').map((r) => r.campaign), [null, 'Kuwait-C', 'Dubai-A', 'Qatar-B'])
  assert.deepEqual(camp.sortCampaigns(rows, 'conversionRate', 'asc').map((r) => r.campaign), ['Qatar-B', 'Dubai-A', 'Kuwait-C', null])
  assert.deepEqual(camp.sortCampaigns(rows, 'campaign', 'asc').map((r) => camp.campaignName(r)), ['Dubai-A', 'Kuwait-C', 'No campaign', 'Qatar-B'])
  assert.equal(rows[0].campaign, 'Qatar-B', 'the input is not mutated')
  // ties fall back to the bigger campaign first
  assert.deepEqual(camp.sortCampaigns([stat('A', 2), stat('B', 9)], 'newToday', 'desc').map((r) => r.campaign), ['B', 'A'])
  assert.deepEqual(camp.sortCampaigns([stat('A', 1, { lastUploadAt: '2026-11-01T00:00:00Z' }), stat('B', 1, { lastUploadAt: '2026-11-05T00:00:00Z' }), stat('C', 1)], 'lastUploadAt', 'desc').map((r) => r.campaign), ['B', 'A', 'C'])
})

test('the table gets a closing total row only when there are campaigns', () => {
  const total = stat(null, 12)
  const rows = camp.tableRows([stat('A', 5), stat('B', 7)], total, 'leads', 'desc')
  assert.deepEqual(rows.map((r) => [r.campaign, !!r.isTotal]), [['B', false], ['A', false], [null, true]])
  assert.equal(camp.rowId(rows[2]), '__total__')
  assert.equal(camp.rowId(rows[0]), 'c:B')
  assert.deepEqual(camp.tableRows([], total, 'leads', 'desc'), [])
  assert.equal(camp.tableRows([stat('A', 1)], null, 'leads', 'desc').length, 1)
})

test('clicking a campaign filters the Leads tab by it; the total row and unnamed campaigns do nothing', () => {
  assert.deepEqual(camp.campaignFilterFor({ campaign: ' Dubai-A ' }), { campaign: 'Dubai-A' })
  assert.equal(camp.campaignFilterFor({ campaign: null }), null)
  assert.equal(camp.campaignFilterFor({ campaign: 'Dubai-A', isTotal: true }), null)
})

test('percentages, bars and the platform split read sensibly', () => {
  assert.equal(camp.pctText(60), '60%')
  assert.equal(camp.pctText(12.5), '12.5%')
  assert.equal(camp.pctText(undefined), '0%')
  assert.equal(camp.barFill(0), 0)
  assert.equal(camp.barFill(0.4), 3, 'a tiny rate still shows a sliver')
  assert.equal(camp.barFill(250), 100)
  assert.equal(camp.barFill(NaN), 0)
  assert.deepEqual(camp.platformShares({ fb: 5, ig: 4, other: 1 }), { fb: 50, ig: 40, other: 10 })
  assert.deepEqual(camp.platformShares({ fb: 1, ig: 1, other: 1 }), { fb: 33, ig: 33, other: 34 })
  assert.deepEqual(camp.platformShares({ fb: 0, ig: 0, other: 0 }), { fb: 0, ig: 0, other: 0 })
  assert.equal(camp.topText([{ name: 'Chef', count: 4 }, { name: 'Helper', count: 3 }, { name: 'Cook', count: 2 }, { name: 'X', count: 1 }]), 'Chef 4, Helper 3, Cook 2')
})

test('last upload wording', () => {
  const now = new Date(2026, 10, 6, 12, 0, 0)
  assert.equal(camp.uploadAgo(null, now), 'Never')
  assert.equal(camp.uploadAgo(new Date(2026, 10, 6, 1).toISOString(), now), 'Today')
  assert.equal(camp.uploadAgo(new Date(2026, 10, 5, 23).toISOString(), now), 'Yesterday')
  assert.equal(camp.uploadAgo(new Date(2026, 10, 2, 9).toISOString(), now), '4 days ago')
})

test('empty state wording depends on who is looking', () => {
  assert.match(camp.emptyCampaigns('mine').description, /assigns leads to you/)
  assert.match(camp.emptyCampaigns('all').description, /Upload a lead sheet/)
  assert.ok(camp.isCampaignSort('conversionRate') && !camp.isCampaignSort('nope'))
})

/* ---------------------------------------------------------------- loading state */

const state = (over) => ({ loading: false, stale: false, hasPage: true, error: '', rows: 3, ...over })

test('the list shows the skeleton on the first fetch, never "No leads yet"', () => {
  assert.equal(view.listView(state({ loading: true, hasPage: false, rows: 0 })), 'skeleton')
  assert.equal(view.listView(state({ loading: true, hasPage: false, rows: 0, error: 'old' })), 'skeleton')
})

test('after a changed filter old rows stay for a moment, then the skeleton takes over; an empty old page never poses as the answer', () => {
  assert.equal(view.listView(state({ loading: true, stale: true })), 'rows')
  assert.equal(view.listView(state({ loading: true, stale: true, slow: true })), 'skeleton')
  assert.equal(view.listView(state({ loading: true, stale: true, rows: 0 })), 'skeleton')
  assert.equal(view.listView(state({ loading: true, stale: false })), 'rows', 'a live refresh keeps the rows')
})

test('empty only after a successful answer with no rows; an error with nothing loaded offers Retry', () => {
  assert.equal(view.listView(state({ rows: 0 })), 'empty')
  assert.equal(view.listView(state({ rows: 0, hasPage: false, error: 'boom' })), 'error')
  assert.equal(view.listView(state({ rows: 0, hasPage: true, error: 'boom' })), 'empty', 'a failed refresh after a good answer keeps the last page and shows the banner elsewhere')
  assert.equal(view.listView(state({})), 'rows')
})

test('the count line says Loading until there is an answer', () => {
  assert.equal(view.countText({ loading: true, hasPage: false, total: 0, busy: true }), 'Loading…')
  assert.equal(view.countText({ loading: false, hasPage: false, total: 0, busy: false, error: 'x' }), 'Could not load the leads')
  assert.equal(view.countText({ loading: false, hasPage: true, total: 1, busy: false }), 'Showing 1 lead')
  assert.equal(view.countText({ loading: true, hasPage: true, total: 3200, busy: true }), `Showing ${(3200).toLocaleString()} leads · updating…`)
  assert.equal(view.countText({ loading: false, hasPage: true, total: 0, busy: false }), 'Showing 0 leads')
  assert.equal(view.LOADING_LEADS, 'Loading leads…')
  assert.equal(view.skeletonRows(25), 12)
  assert.equal(view.skeletonRows(3), 5)
  assert.equal(view.skeletonRows(Number.NaN), 8)
})

/* ---------------------------------------------------------------- WhatsApp templates */

test('three default templates with the placeholders', () => {
  assert.deepEqual(wa.DEFAULT_WA_TEMPLATES.map((t) => t.key), ['first_contact', 'request_resume', 'follow_up'])
  for (const t of wa.DEFAULT_WA_TEMPLATES) {
    assert.ok(t.body.includes('{name}') && t.body.includes('{company_name}') && t.body.includes('{position}'), t.key)
    assert.ok(t.body.length <= wa.WA_BODY_MAX, `${t.key} fits the 255 character limit`)
    assert.deepEqual(wa.unknownPlaceholders(t.body), [])
  }
})

test('placeholders are filled for the lead, with safe fallbacks', () => {
  const filled = wa.fillTemplate(wa.DEFAULT_WA_TEMPLATES[0].body, { name: 'Asha Devi', position: 'Kitchen Helper', companyName: 'Care India' })
  assert.equal(filled, 'Hello Asha, this is Care India. You showed interest in the Kitchen Helper job abroad. Can we talk today about your details?')
  assert.equal(wa.fillTemplate('Hi {name} about the {position} job from {company_name}', { name: '', position: '', companyName: '' }), 'Hi there about the job from our agency')
  assert.equal(wa.fillTemplate('{name} {name}', { name: ' Ravi  Kumar ' }), 'Ravi Ravi')
  assert.equal(wa.fillTemplate('Keep {other} as is', { name: 'A' }), 'Keep {other} as is')
  assert.equal(wa.firstName(null), 'there')
})

test('the wa.me link carries the number and the message', () => {
  assert.equal(wa.waUrl('+91 98765 43210', 'Hello Asha & co'), 'https://wa.me/919876543210?text=Hello%20Asha%20%26%20co')
  assert.equal(wa.waUrl('p:+919876543210', ''), 'https://wa.me/919876543210')
  assert.equal(wa.waUrl('123', 'x'), null)
  assert.equal(wa.waUrl(null, 'x'), null)
  assert.equal(wa.waUrl('1234567890123456', 'x'), null)
})

test('server templates are used when they are valid, else the defaults', () => {
  assert.equal(wa.normalizeTemplates(null), wa.DEFAULT_WA_TEMPLATES)
  assert.equal(wa.normalizeTemplates({ templates: [{ key: 'x' }] }), wa.DEFAULT_WA_TEMPLATES)
  const got = wa.normalizeTemplates({ templates: [{ key: 'follow_up', title: 'Nudge', body: 'Hi {name}', isDefault: false }] })
  assert.deepEqual(got.map((t) => [t.key, t.title, t.body]), [['follow_up', 'Nudge', 'Hi {name}']])
})

test('admin editing rules: 255 characters and known placeholders only', () => {
  assert.equal(wa.bodyRoom('a'.repeat(250)), 5)
  assert.equal(wa.bodyRoom('a'.repeat(260)), -5)
  assert.equal(wa.bodyRoom('  a   b  '), 252, 'spaces are squeezed like the server does')
  assert.deepEqual(wa.unknownPlaceholders('Hi {name} {nme} {company_name} {x}'), ['{nme}', '{x}'])
})

/* ---------------------------------------------------------------- follow-ups, origin badge */

test('the Follow-ups due card filters by due follow-ups and lights only for them', () => {
  const NOW = new Date(2026, 10, 6, 12, 0, 0)
  const f = lead.cardFilters('follow_ups', NOW)
  assert.equal(f.followUp, 'due')
  const filters = { ...lead.EMPTY_FILTERS, ...f }
  assert.equal(lead.activeCard(filters, NOW), 'follow_ups')
  assert.match(lead.buildLeadQuery(filters, { key: 'created', dir: 'desc' }), /(^|&)follow_up=due(&|$)/)
  assert.doesNotMatch(lead.buildLeadQuery(lead.EMPTY_FILTERS, { key: 'created', dir: 'desc' }), /follow_up/)
  assert.equal(lead.activeCard({ ...lead.EMPTY_FILTERS, status: 'interested', followUp: 'due' }, NOW), '', 'a status filter on top is no longer the card')
  assert.equal(lead.activeLeadFilterCount(lead.EMPTY_FILTERS), 0)
  assert.equal(lead.activeLeadFilterCount(filters), 1)
  // the other cards keep their exact filters
  assert.deepEqual(lead.cardFilters('not_contacted', NOW), { status: 'new', from: '', to: '', recent: '' })
})

test('overdue follow-ups are red, today amber, later plain (the state the table colours by)', () => {
  const NOW = new Date(2026, 10, 6, 12, 0, 0)
  assert.equal(lead.followUpState(new Date(2026, 10, 5, 9).toISOString(), 'new', NOW), 'overdue')
  assert.equal(lead.followUpState(new Date(2026, 10, 6, 17).toISOString(), 'new', NOW), 'today')
  assert.equal(lead.followUpState(new Date(2026, 10, 20, 9).toISOString(), 'contacted', NOW), 'later')
  assert.equal(lead.followUpState(new Date(2026, 10, 5, 9).toISOString(), 'not_interested', NOW), 'closed')
})

test('the candidate profile badge text', () => {
  assert.equal(origin.originLabel({ campaignName: 'Dubai-Chef-03-11' }), 'From lead sheet: Dubai-Chef-03-11')
  assert.equal(origin.originLabel({ campaignName: ' ', formName: 'Form A' }), 'From lead sheet: Form A')
  assert.equal(origin.originLabel({}), 'From lead sheet')
})
