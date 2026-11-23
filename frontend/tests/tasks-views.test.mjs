import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const m = await import(moduleUrl(await read('../src/features/daily-work/daily-work-model.ts')))
const { createFeed } = await import(moduleUrl(await read('../src/shared/lib/feed.ts')))

const ZONE = 'Asia/Kolkata'
const base = { kind: 'stage', status: 'open', waiting: false, completedAt: null, referral: { source: '', label: '', detail: null }, stage: 3, remark: null }
const item = (id, over = {}) => ({ ...base, id, candidateId: id, ownerId: 'u1', applicationId: `app-${id}`, taskId: `t-${id}`, candidate: `Cand ${id}`, ...over })
const closed = (id, kind, over = {}) => item(id, {
  status: 'closed', taskId: null, outcome: { kind, label: { declined: 'Declined', rejected: 'Rejected', travelled: 'Travelled' }[kind], reason: null, stage: 5, at: '2026-11-19T07:00:00Z' }, ...over,
})
const order = (rows) => rows.map((r) => r.candidateId)

/* ------------------------------------------------------------------ DOC order */

test('DOC order: oldest first, newest last, no date at the very end, ties keep the incoming order', () => {
  const rows = m.taskRows([
    item('none1'), item('new', { commencementDate: '2026-12-30' }), item('old', { commencementDate: '2026-11-01' }),
    item('tie1', { commencementDate: '2026-11-20' }), item('none2', { commencementDate: null }), item('tie2', { commencementDate: '2026-11-20' }),
    item('mid', { commencementDate: '2026-11-19T00:00:00Z' }),
  ], [], '2026-11-19', ZONE)
  assert.deepEqual(order(rows), ['old', 'mid', 'tie1', 'tie2', 'new', 'none1', 'none2'])
})

test('DOC order: an invalid, empty or unreadable date counts as no date (after every dated candidate)', () => {
  const rows = m.taskRows([
    item('bad', { commencementDate: '2026-02-30' }), item('empty', { commencementDate: '' }), item('soon', { commencementDate: 'soon' }),
    item('ok', { commencementDate: '2027-06-01' }), item('missing'),
  ], [], '2026-11-19', ZONE)
  assert.deepEqual(order(rows), ['ok', 'bad', 'empty', 'soon', 'missing'])
  assert.equal(m.docTime('2026-02-30'), null)
  assert.equal(m.docTime(null), null)
  assert.equal(m.docTime(undefined), null)
  assert.ok(m.docTime('2026-11-19') < m.docTime('2026-11-20'))
  assert.equal(m.docTime('2026-11-19'), m.docTime('2026-11-19T23:59:00Z'), 'only the day counts')
})

test('DOC order inside a tie follows the old order: untouched first, a stage waiting, the furthest along', () => {
  const rows = m.taskRows([
    item('far', { stage: 9, commencementDate: '2026-11-20' }), item('waits', { waiting: true, stage: 2, commencementDate: '2026-11-20' }),
    item('early', { stage: 2, commencementDate: '2026-11-20' }),
    item('called', { kind: 'call', status: 'done', stage: null, commencementDate: '2026-11-20' }),
  ], [], '2026-11-19', ZONE)
  assert.deepEqual(order(rows), ['waits', 'far', 'early', 'called'])
})

test('DOC order across several recruiters (the supervisor list) is one list by date, ties by the old order', () => {
  const rows = m.taskRows([
    item('a1', { ownerId: 'asha', commencementDate: '2026-12-05' }), item('b1', { ownerId: 'bala', commencementDate: '2026-11-02' }),
    item('c1', { ownerId: 'chitra' }), item('a2', { ownerId: 'asha', commencementDate: '2026-11-02' }), item('b2', { ownerId: 'bala', commencementDate: '2026-12-01' }),
  ], [], '2026-11-19', ZONE)
  assert.deepEqual(order(rows), ['b1', 'a2', 'b2', 'a1', 'c1'])
  assert.deepEqual(m.sortByDoc([]), [])
  const input = [{ commencementDate: '2026-12-01' }, { commencementDate: '2026-11-01' }]
  m.sortByDoc(input)
  assert.equal(input[0].commencementDate, '2026-12-01', 'the input is not changed')
})

test('S NO follows the displayed order and the table does not re-sort', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /rows\.map\(\(row, index\) =>/)
  assert.match(table, />\{index \+ 1\}<\/td>/)
  assert.doesNotMatch(table, /\.sort\(/)
  assert.match(await code('../src/features/daily-work/daily-work-model.ts'), /return sortByDoc\(rows\)/)
})

/* ------------------------------------------------------------------ status views: the contract, tolerantly */

test('an item without `outcome`, with a null one or with an unknown kind is a current row', () => {
  for (const row of [item('a'), item('b', { outcome: null }), item('c', { outcome: { kind: 'weird' } }), item('d', { outcome: { kind: 'current', label: null, reason: null, stage: null, at: null } })]) {
    assert.equal(m.outcomeOf(row).kind, 'current')
    assert.equal(m.isClosed(row), false)
  }
  assert.equal(m.rowAction(item('a', { applicationId: 'x' })), 'Complete')
})

test('a closed row reads its label, reason, stage and date; the label follows the kind', () => {
  const o = m.outcomeOf({ outcome: { kind: 'declined', label: 'Whatever', reason: '  Offer declined by the candidate ', stage: 5, at: '2026-11-19T07:00:00Z', by: ' Asha ' } })
  assert.deepEqual(o, { kind: 'declined', label: 'Declined', reason: 'Offer declined by the candidate', stage: 5, at: '2026-11-19T07:00:00Z', by: 'Asha' })
  assert.equal(m.outcomeOf({ outcome: { kind: 'travelled', label: null, reason: null, stage: 12, at: null } }).label, 'Travelled')
  assert.equal(m.outcomeOf({ outcome: { kind: 'rejected', stage: 0 } }).stage, null)
  assert.equal(m.outcomeOf({ outcome: { kind: 'rejected', stage: 'x' } }).stage, null)
  assert.equal(m.outcomeOf({ outcome: { kind: 'rejected' } }).by, null, 'a server that does not send `by` is fine')
})

test('the detail under a chip: where ("at 5 · Offer letter"), why, when and, when sent, by whom', () => {
  const titleOf = (no) => ({ 5: 'Offer letter' })[no]
  assert.equal(m.outcomeStageText({ stage: 5 }, titleOf), 'at 5 · Offer letter')
  assert.equal(m.outcomeStageText({ stage: 7 }, titleOf), 'at 7')
  assert.equal(m.outcomeStageText({ stage: null }, titleOf), '')
  const full = m.outcomeDetail(m.outcomeOf({ outcome: { kind: 'declined', reason: 'Offer declined by the candidate', stage: 5, at: '2026-11-19T07:00:00Z', by: 'Asha Devi' } }), titleOf, ZONE)
  assert.deepEqual(full, { where: 'at 5 · Offer letter', why: 'Offer declined by the candidate', when: '19 Nov 2026', by: 'by Asha Devi', line: 'Offer declined by the candidate · 19 Nov 2026 · by Asha Devi' })
  const bare = m.outcomeDetail(m.outcomeOf({ outcome: { kind: 'travelled', stage: 12 } }), titleOf, ZONE)
  assert.deepEqual([bare.why, bare.when, bare.by, bare.line], ['', '', '', ''], 'no reason, no date, no person: nothing invented')
  // the date is on the agency clock: 20:00 UTC on the 19th is already the 20th in India
  assert.equal(m.outcomeDetail(m.outcomeOf({ outcome: { kind: 'rejected', at: '2026-11-19T20:00:00Z' } }), undefined, ZONE).when, '20 Nov 2026')
})

test('a closed row: nothing done today, only View, and it is on nobody\'s card', () => {
  const rows = m.taskRows([item('x', { remark: { body: 'PCC pending', at: '2026-11-19T07:00:00Z', by: 'G', count: 1 } }), closed('d', 'declined', { remark: { body: 'old', at: '2026-11-19T07:00:00Z', by: 'G', count: 1 } })], [], '2026-11-19', ZONE)
  const d = rows.find((r) => r.candidateId === 'd')
  assert.deepEqual(d.today, { called: false, moved: false, remarked: false })
  assert.equal(m.rowAction(d), 'View')
  assert.equal(m.rowAction({ ...d, applicationId: null }), 'View')
  assert.equal(m.recruiterWork(rows).get('u1').assigned, 1, 'only the current candidate counts for the recruiter')
  assert.equal(m.recruiterWork([d]).size, 0)
})

test('closed rows keep a candidate once: a placement on the list wins over the follow-up copy', () => {
  const rows = m.taskRows([closed('d', 'travelled')], [{ id: 'f', applicationId: 'f', taskId: 'tf', candidateId: 'd', ownerId: 'u1', stage: 4, lastUpdateAt: '2026-11-18T00:00:00Z', client: null }], '2026-11-19', ZONE)
  assert.equal(rows.length, 1)
  assert.equal(m.isClosed(rows[0]), true)
})

test('the five views, the URL value and the request', () => {
  assert.deepEqual(m.DAILY_VIEWS.map((v) => v.label), ['Current process', 'Declined', 'Rejected', 'Travelled', 'All'])
  assert.deepEqual(m.DAILY_VIEWS.map((v) => v.key), ['current', 'declined', 'rejected', 'travelled', 'all'])
  for (const key of ['declined', 'rejected', 'travelled', 'all', 'current']) { assert.equal(m.parseView(key), key); assert.equal(m.isDailyView(key), true) }
  for (const bad of [null, undefined, '', 'daily', 'open', 'follow-up', 'DECLINED', 'x']) assert.equal(m.parseView(bad), 'current', String(bad))
  for (const other of ['open', 'due', 'overdue', 'escalated', 'initial', 'missing', 'completed', 'history', 'follow-up']) assert.equal(m.isDailyView(other), false, `${other} stays the 48-hour list's`)
  assert.equal(m.viewParam('current'), null)
  assert.equal(m.viewParam('declined'), 'declined')
  // current sends nothing extra: its request is the one the sidebar and every other screen already share
  assert.equal(m.listRequest('/daily-work'), '/daily-work')
  assert.equal(m.listRequest('/daily-work', undefined, 'current'), '/daily-work')
  assert.equal(m.listRequest('/daily-work', 'all'), '/daily-work?staff=all')
  assert.equal(m.listRequest('/daily-work', 'all', 'declined'), '/daily-work?staff=all&view=declined')
  assert.equal(m.listRequest('/daily-work', undefined, 'travelled'), '/daily-work?view=travelled')
  assert.equal(m.listRequest('/daily-work', 'u 1', 'all'), '/daily-work?staff=u%201&view=all')
  assert.equal(m.listRequest('/follow-up', 'all', 'declined'), '/follow-up?staff=all', 'Follow-up has no views')
})

test('the tab counts come from viewCounts; the screen\'s own current count wins; a missing count shows just the name', () => {
  const counts = { current: 12, declined: 4, rejected: 3, travelled: 7 }
  assert.deepEqual(m.viewTabs(counts).map((t) => [t.key, t.count]), [['current', 12], ['declined', 4], ['rejected', 3], ['travelled', 7], ['all', 26]])
  assert.deepEqual(m.viewTabs(counts, 31).map((t) => t.count), [31, 4, 3, 7, 45], 'the follow-up placements are part of the current process')
  assert.deepEqual(m.viewTabs(undefined).map((t) => t.count), [null, null, null, null, null])
  assert.deepEqual(m.viewTabs({ current: 5 }).map((t) => t.count), [5, null, null, null, null])
  assert.deepEqual(m.viewTabs({ current: -1, declined: 'x' }, null).map((t) => t.count), [null, null, null, null, null])
  assert.equal(m.viewTabText({ label: 'Declined', count: 4 }), 'Declined (4)')
  assert.equal(m.viewTabText({ label: 'Declined', count: null }), 'Declined')
  assert.equal(m.viewTabText({ label: 'Declined', count: 0 }), 'Declined (0)')
})

test('arrow keys move along the filter bar and wrap; Home and End jump; other keys do nothing', () => {
  assert.equal(m.nextViewIndex('ArrowRight', 0, 5), 1)
  assert.equal(m.nextViewIndex('ArrowRight', 4, 5), 0)
  assert.equal(m.nextViewIndex('ArrowLeft', 0, 5), 4)
  assert.equal(m.nextViewIndex('ArrowLeft', 3, 5), 2)
  assert.equal(m.nextViewIndex('Home', 3, 5), 0)
  assert.equal(m.nextViewIndex('End', 1, 5), 4)
  for (const key of ['Enter', ' ', 'Tab', 'a']) assert.equal(m.nextViewIndex(key, 1, 5), null)
  assert.equal(m.nextViewIndex('ArrowRight', 0, 0), null)
})

test('the summary, the empty states and the export follow the view', () => {
  assert.equal(m.viewSummary('current', 12, 3), '12 candidates · 3 recruiters')
  assert.equal(m.viewSummary('current', 1, 1), '1 candidate · 1 recruiter')
  assert.equal(m.viewSummary('current', 5), '5 candidates')
  assert.equal(m.viewSummary('declined', 4, 3), '4 declined candidates · 3 recruiters')
  assert.equal(m.viewSummary('declined', 1), '1 declined candidate')
  assert.equal(m.viewSummary('travelled', 0), '0 travelled candidates')
  assert.equal(m.viewSummary('all', 26), '26 candidates · all statuses')
  assert.equal(m.viewEmptyTitle('declined'), 'No declined candidates')
  assert.equal(m.viewEmptyTitle('rejected'), 'No rejected candidates')
  assert.equal(m.viewEmptyTitle('travelled'), 'No travelled candidates')
  assert.equal(m.viewEmptyTitle('all'), 'No candidates')
  assert.equal(m.VIEW_CAPTION, undefined, 'no explanatory sentence under the tabs')
  assert.equal(m.exportLabel('current'), 'Export Excel')
  assert.equal(m.exportLabel('declined'), 'Export Excel · Declined')
  assert.equal(m.exportPath('all'), '/daily-work/export?format=xlsx&staff=all')
  assert.equal(m.exportPath('all', 'current'), '/daily-work/export?format=xlsx&staff=all')
  assert.equal(m.exportPath('u1', 'declined'), '/daily-work/export?format=xlsx&staff=u1&view=declined')
  assert.equal(m.exportPath(undefined, 'all'), '/daily-work/export?format=xlsx&staff=all&view=all')
  assert.equal(m.exportFileName('2026-11-19', 'Asha Devi', 'travelled'), 'tasks-asha-devi-travelled-2026-11-19.xlsx')
  assert.equal(m.exportFileName('2026-11-19', '', 'rejected'), 'tasks-rejected-2026-11-19.xlsx')
  assert.equal(m.exportFileName('2026-11-19', 'Asha Devi', 'current'), 'tasks-asha-devi-2026-11-19.xlsx')
})

/* ------------------------------------------------------------------ one request per view, previous data kept */

test('one request per view change: each view is its own shared feed, read once; coming back to a view shows it at once', async () => {
  const requests = []
  const feeds = new Map()
  const feedFor = (request) => {
    if (!feeds.has(request)) feeds.set(request, createFeed({ load: async () => { requests.push(request); return { request } } }))
    return feeds.get(request)
  }
  const watch = (feed) => { const off = feed.subscribe(() => {}); return off }
  const settle = () => new Promise((resolve) => setTimeout(resolve, 5))
  const current = feedFor(m.listRequest('/daily-work', 'all', 'current'))
  const offCurrent = watch(current); await settle()
  assert.deepEqual(requests, ['/daily-work?staff=all'])
  const declined = feedFor(m.listRequest('/daily-work', 'all', 'declined'))
  assert.equal(declined.getState().data, null, 'a view not read yet has nothing; the screen keeps the previous rows meanwhile')
  assert.equal(declined.getState().loading, true)
  const offDeclined = watch(declined); await settle()
  assert.deepEqual(requests, ['/daily-work?staff=all', '/daily-work?staff=all&view=declined'], 'exactly one request for the new view')
  assert.deepEqual(declined.getState().data, { request: '/daily-work?staff=all&view=declined' })
  offDeclined(); offCurrent()
  assert.deepEqual(current.getState().data, { request: '/daily-work?staff=all' }, 'the answer is kept for when the person comes back')
  const hook = await code('../src/features/daily-work/use-daily-work.ts')
  assert.match(hook, /listRequest\(path, staff, view\)/)
  assert.match(hook, /feedFor<T>\(sessionId, request\)/, 'one feed per person and request, so views never mix')
  assert.match(hook, /view\?: DailyView/)
})

/* ------------------------------------------------------------------ the screen */

test('the screen keeps the view in ?view=, defaults to current, keeps the old rows while loading and passes the view to the export', async () => {
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /parseView\(params\.get\('view'\)\)/)
  assert.match(screen, /if \(value !== 'current'\) next\.set\('view', value\); else next\.delete\('view'\)/)
  assert.match(screen, /useDailyWork\(\{ staff: staffFilter, view \}\)/)
  assert.match(screen, /downloadDailyWork\(supervisor \? staffFilter : store\.me\.id, agencyDay\(new Date\(\), zone\), person, view\)/)
  assert.match(screen, /<ViewTabs tabs=\{tabs\} active=\{view\} onChange=\{setView\} \/>/)
  assert.match(screen, /viewSummary\(shown\.view, rows\.length/)
  assert.match(screen, /viewEmptyTitle\(view\)/)
  assert.match(screen, /kept\.current = \{ view, data: daily\.data \}/, 'the previous answer stays on screen while the next view loads')
  assert.match(screen, /busy=\{waiting\}/)
  assert.match(screen, /withFollowUp = shown\?\.view === 'current' \|\| shown\?\.view === 'all'/, 'follow-up placements only belong to the current process')
  // the recruiter cards always read the CURRENT process
  assert.match(screen, /useDailyWork\(\{ staff: filtered \? 'all' : staffFilter, enabled: separateBoard \}\)/)
  const page = await code('../src/screens/tasks/tasks.tsx')
  assert.match(page, /isDailyView\(view\)/, '?view=declined opens this screen, not the 48-hour list')
  assert.match(await code('../src/features/daily-work/daily-work-export.ts'), /exportFileName\(day, person, view\)/)
})

test('the filter bar: tabs with counts, one tab stop, arrows move, Enter picks, and no caption sits under them', async () => {
  const bar = await code('../src/screens/tasks/view-tabs.tsx')
  assert.match(bar, /role="tablist"/)
  assert.match(bar, /role="tab"/)
  assert.match(bar, /aria-selected=\{selected\}/)
  assert.match(bar, /tabIndex=\{selected \? 0 : -1\}/)
  assert.match(bar, /nextViewIndex\(event\.key, index, tabs\.length\)/)
  assert.match(bar, /event\.preventDefault\(\)/)
  assert.match(bar, /<button[\s\S]*type="button"/,'real buttons: Enter and Space pick the view')
  assert.doesNotMatch(bar, /VIEW_CAPTION|role="note"/, 'no explanatory sentence under the tabs')
  assert.doesNotMatch(bar, /onFocus/, 'arrowing past a tab never starts a request')
  const table = await code('../src/screens/tasks/daily-table.tsx')
  assert.match(table, /role: 'tabpanel', 'aria-labelledby': labelledBy/)
})

test('a closed row: the outcome replaces the progress dashes; TODAY is a dash; DOC, remark and notes are editable like a current row; the button is View', async () => {
  const cells = await code('../src/screens/tasks/daily-table.tsx')
  const stage = cells.slice(cells.indexOf('export function OutcomeStage'), cells.indexOf('export function StageCell'))
  assert.match(stage, /<b>\{outcome\.label\}<\/b>/)
  assert.match(stage, /detail\.where/)
  assert.match(stage, /detail\.why/)
  assert.match(stage, /\[detail\.when, detail\.by\]/)
  assert.match(stage, /dw-outcome-why/, 'the reason is a visible line, not only a tooltip')
  assert.match(stage, /title=\{tip\}/)
  assert.doesNotMatch(stage, /dw-stage-bar/)
  assert.match(cells, /if \(outcomeOf\(item\)\.kind !== 'current'\) return <OutcomeStage/)
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /const work = canWork\(row\)\s/, 'a closed row is edited like a current one: DOC, remark box and note input')
  assert.doesNotMatch(table, /const work = canWork\(row\) && !closed/)
  assert.match(table, /closed \? <span className="dw-none"[^>]*>—<\/span> : <TodayChips /)
  // what the recruiters wrote stays visible on a closed row and they can add to it: same RemarkCell, NotesCell and DocCell as a current row
  assert.doesNotMatch(table, /closed \? <span className="dw-none"[^>]*>—<\/span> : <RemarkCell/)
  assert.match(table, /<RemarkCell item=\{row\} canWork=\{work\}/)
  assert.match(table, /<NotesCell row=\{row\} zone=\{zone\} canAdd=\{work && \(!!row\.taskId \|\| closed\)\}/)
  assert.match(table, /onClear=\{onClearRemark && \(row\.taskId \|\| closed\)/)
  assert.match(table, /<DocCell item=\{row\} canAdd=\{work\}/)
  // the screen routes a closed row (no task id) to the by-placement writes and lets its recruiter / admin / MD work it
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /canWork=\{\(row\) => \(!!row\.taskId \|\| \(isClosed\(row\) && !!row\.applicationId\)\) && \(store\.can\('work_any_candidate'\) \|\| row\.ownerId === store\.me\.id\)\}/)
  for (const call of ['store.addEndedRemark(row.applicationId, text)', "store.addEndedRemark(row.applicationId, text, 'note')", 'store.clearEndedRemark(row.applicationId)']) assert.ok(screen.includes(call), call)
  const store = await code('../src/shared/state/crm-store.tsx')
  assert.match(store, /\/applications\/\$\{encodeURIComponent\(applicationId\)\}\/ended-remarks`/)
  assert.match(store, /ended-remarks\/clear/)
  assert.match(table, /<StageCell item=\{row\} zone=\{zone\} \/>/)
  assert.match(table, /closed \? 'View stage'|moved \|\| closed \? 'View stage'/)
  assert.match(table, /noCountry && !closed/)
})

test('the outcome chip: red Declined, amber Rejected, green Travelled, wrapping, light and dark through the tokens', async () => {
  const css = await read('../src/styles/tasks-table.css')
  const rule = (selector) => { const at = css.indexOf(`${selector} {`); assert.ok(at >= 0, selector); return css.slice(at, css.indexOf('}', at)) }
  assert.match(rule('.dw .dw-outcome-chip.is-declined'), /var\(--error-edge\)[^}]*var\(--error-fill\)[^}]*var\(--error-ink\)/)
  assert.match(rule('.dw .dw-outcome-chip.is-rejected'), /var\(--warning-edge\)[^}]*var\(--warning-fill\)[^}]*var\(--warning-ink\)/)
  assert.match(rule('.dw .dw-outcome-chip.is-travelled'), /var\(--success-edge\)[^}]*var\(--success-fill\)[^}]*var\(--success-ink\)/)
  for (const part of ['.dw .dw-outcome-stage', '.dw .dw-outcome-why', '.dw .dw-outcome-meta']) assert.match(rule(part), /overflow-wrap: break-word/, part)
  for (const part of ['.dw .dw-outcome-chip', '.dw .dw-outcome-stage', '.dw .dw-outcome-why', '.dw .dw-outcome-meta']) assert.doesNotMatch(rule(part), /text-overflow|line-clamp/, `${part} never cuts a word`)
  assert.doesNotMatch(rule('.dw .dw-outcome-why'), /nowrap/)
  assert.match(rule('.dw .dw-outcome-chip'), /flex-direction: row[^}]*white-space: nowrap/, 'the pill is one line: "Declined · Stage 5"')
  // the phone card shows the same chip, left-aligned
  assert.match(css, /@container dw \(max-width: 1079px\) \{\s*\.dw \.dw-outcome \{ align-items: flex-start/)
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ''), /#[0-9a-fA-F]{3,8}\b|rgb\(/, 'tokens only')
  const bar = await read('../src/styles/daily-work.css')
  assert.match(bar, /\.dw-view\.is-active \{[^}]*var\(--primary\)/)
  assert.match(bar, /\[data-theme='dark'\] \.dw-view\.is-active/)
  assert.match(bar, /\.dw-view:focus-visible \{ outline: 2px solid var\(--primary\)/)
  assert.match(bar, /\.dw-views \{[^}]*overflow-x: auto/, 'the bar scrolls inside itself on a phone, never the page')
})

test('centred table: header and cell text, the person, chips and the actions all centre; no column is left-aligned in the table', async () => {
  const css = await read('../src/styles/tasks-table.css')
  const table = (await code('../src/screens/tasks/task-table.tsx'))
  assert.doesNotMatch(table, /is-left/)
  assert.match(css, /\.record-identity > div \{ align-items: center; text-align: center; \}/)
  assert.match(css, /\.tk-owner \{ justify-content: center; \}/)
  assert.match(css, /\.tw-today \{[^}]*align-items: center/)
  assert.match(css, /\.dw-stage-chip \{[^}]*justify-content: center[^}]*text-align: center/)
  assert.match(css, /is-actions \{ padding: 0 2px; text-align: center; \}/)
  assert.match(css, /\.dw-actions \{ justify-content: center; gap: 4px; \}/)
})

test('a list of ended rows has a wider STAGE and no TODAY column; the Remarks and Notes widths are the Tasks table own', () => {
  const keys = (columns) => columns.map((c) => c.key)
  const widthOf = (columns, key) => columns.find((c) => c.key === key).width
  const plain = m.taskColumnsFor(true, true, 'none')
  assert.equal(widthOf(plain, 'stage'), 106)
  assert.ok(keys(plain).includes('today'))
  assert.deepEqual(keys(m.taskColumnsFor(true, true)), keys(plain), 'two arguments are the unchanged current table')
  const some = m.taskColumnsFor(true, true, 'some')
  assert.equal(widthOf(some, 'stage'), m.ENDED_STAGE_WIDTH)
  assert.ok(keys(some).includes('today'), 'the All tab still has current rows, so TODAY stays')
  const all = m.taskColumnsFor(true, true, 'all')
  assert.equal(widthOf(all, 'stage'), m.ENDED_STAGE_WIDTH)
  assert.ok(!keys(all).includes('today'), 'nothing is done today on an ended row')
  // spare room still goes to REMARKS and NOTES up to the same caps, so those cells keep a readable width at 1900px
  const wide = m.widthsWithSpare(all, 1586)
  assert.equal(wide.remarks, m.REMARKS_MAX)
  assert.equal(wide.notes, m.NOTES_MAX)
  assert.equal(m.closedRowsOf([]), 'none')
  assert.equal(m.closedRowsOf([closed('a', 'declined'), closed('b', 'rejected')]), 'all')
  assert.equal(m.closedRowsOf([closed('a', 'declined'), item('x')]), 'some')
  assert.equal(m.closedRowsOf([item('x')]), 'none')
})

test('a note never leaves its Notes cell: the cell, the list and each note shrink below their longest word and the text breaks anywhere', async () => {
  const css = await read('../src/styles/tasks-table.css')
  const rule = (selector) => { const at = css.indexOf(`${selector} {`); assert.ok(at >= 0, selector); return css.slice(at, css.indexOf('}', at)) }
  assert.match(rule('.tw-notes-cell'), /min-width: 0/)
  assert.match(rule('.tw-notes-cell'), /max-width: 100%/)
  assert.match(rule('.tw-notes-cell'), /overflow: hidden/)
  assert.match(rule('.tw-note-list'), /grid-template-columns: minmax\(0, 1fr\)/, 'an auto grid track grows with a long URL')
  assert.match(rule('.tw-note-list'), /overflow-x: hidden/)
  assert.match(rule('.tw-note-list li'), /min-width: 0/)
  assert.match(rule('.tw-note-body'), /overflow-wrap: anywhere/, 'break-word does not count in the min-content width, anywhere does')
  assert.match(rule('.tw-note-body'), /min-width: 0/)
})

test('whatever is written stays inside its cell on every tab: names and agencies break anywhere, texts keep the full tooltip, the date input and cells never outgrow the card or the column', async () => {
  const tasks = await read('../src/styles/tasks-table.css')
  const daily = await read('../src/styles/daily-work.css')
  assert.match(tasks, /\.dw \.tw-table \.record-name \{[^}]*overflow-wrap: anywhere/, 'a 300-character name wraps inside CANDIDATE')
  assert.match(daily, /\.dw \.record-name \{[^}]*overflow-wrap: anywhere/)
  assert.match(daily, /\.dw-clamp \{[^}]*overflow: hidden; overflow-wrap: break-word/, 'client, job and agency clamp to lines and never widen the column')
  assert.match(tasks, /\.dw \.dw-doc-input \{[^}]*max-width: 100%/, 'the typed date fits a phone card column')
  assert.match(tasks, /\.dw \.tw-table td \{ min-width: 0; max-width: 100%; \}/)
  const workspace = await read('../src/shared/ui/workspace.tsx')
  assert.match(workspace, /<p className="record-name" title=\{name\}>/, 'a clamped name keeps its full text as the tooltip')
  const cells = await code('../src/screens/tasks/daily-table.tsx')
  for (const part of ['title={label}', 'title={item.clientContact ?', "title={`${cell.text}. ${cell.hint}`}", 'title={tip || undefined}']) assert.ok(cells.includes(part), `the full text of a clamped cell stays reachable: ${part}`)
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /<ScrollEdges /, 'the table fills the panel in percentages: no sideways scroll, cards below the breakpoint')
})


/* ------------------------------------------------------------------ a candidate who said no before any placement */

test('a candidate who said no at the initial call (no placement, no stage) is a closed Declined row that opens as View and reads cleanly', () => {
  const row = closed('call-no', 'declined', {
    applicationId: null, stage: null,
    outcome: { kind: 'declined', label: 'Declined', reason: 'Candidate not interested at the initial call', stage: null, at: '2026-11-19T07:00:00Z', by: null },
  })
  assert.equal(m.isClosed(row), true)
  assert.equal(m.rowAction(row), 'View')
  const detail = m.outcomeDetail(row.outcome, undefined, ZONE)
  assert.equal(detail.where, '')
  assert.equal(detail.why, 'Candidate not interested at the initial call')
  assert.match(detail.line, /Candidate not interested at the initial call · 19 Nov 2026/)
  // it sorts with the other ended rows and never becomes today's work
  assert.deepEqual(order(m.taskRows([row, closed('placed', 'declined')], [], '2026-11-20', ZONE)).sort(), ['call-no', 'placed'])
})

test('rejecting or declining in the stage window re-reads the Tasks lists at once: the window says so right after the server confirms, the screen listens', async () => {
  const body = await code('../src/features/workflow/components/stage-modal-body.tsx')
  const completeAt = body.indexOf('const complete = async () =>')
  const afterPost = body.indexOf('/complete`, { data:', completeAt)
  assert.ok(completeAt > 0 && afterPost > completeAt)
  const tail = body.slice(afterPost, body.indexOf('await refreshAfterWrite()', afterPost))
  assert.match(tail, /onStageRecorded\?\.\(\)/, 'told right after the post, before the workspace refresh that follows')
  assert.doesNotMatch(tail, /onStageChanged\?\.\(\)/, 'never the prop the candidate form uses to close its stage panel and reload')
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /<StageModal [^\n]*onStageRecorded=\{\(\) => void refreshAll\(\)/)
})
