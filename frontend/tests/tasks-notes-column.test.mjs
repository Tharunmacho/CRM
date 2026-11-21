import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/*
 * The Tasks page's NOTES column: a column of its own at every table width, an input at the top, ALL the candidate's notes newest first
 * (a note is never the remark), and the TODAY chip that moves into the STAGE cell when the table has no room for its own column.
 */

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const m = await import(moduleUrl(await read('../src/features/daily-work/daily-work-model.ts')))
const { withRoleLabels } = await import('./fixtures/role-labels-url.mjs')
const hide = await import(moduleUrl(withRoleLabels((await read('../src/features/daily-work/note-delete-model.ts')))))

const ZONE = 'Asia/Kolkata'
const note = (id, at, body = id, by = 'Asha') => ({ id, at, body, by, byId: 'u1' })
const sum = (list) => list.reduce((total, c) => total + c.width, 0)

/* ------------------------------------------------------------------ the plan at the breakpoints */

test('the plan: supervisor 11 columns fit 1111px and recruiter 1024px, with NOTES and without TODAY', () => {
  const supervisor = m.taskColumnsFor(true, false)
  assert.deepEqual(supervisor.map((c) => c.key), ['no', 'doc', 'client', 'position', 'candidate', 'staff', 'agency', 'stage', 'remarks', 'notes', 'actions'])
  assert.ok(sum(supervisor) <= 1111, `${sum(supervisor)}px must fit the 1111px panel of a 1440px window`)
  assert.ok(sum(m.taskColumnsFor(false, false)) <= 1024, 'a recruiter has no Recruiter column')
  assert.equal(m.taskPlanWidth(true, false), sum(supervisor))
  const notes = supervisor.find((c) => c.key === 'notes')
  assert.ok(notes.width >= 134, 'a note is readable')
  assert.equal(supervisor.find((c) => c.key === 'doc').width, 90, 'the DOC input keeps its 90px')
  assert.equal(supervisor.find((c) => c.key === 'actions').width, 124, 'the actions keep their 124px')
})

test('the full 12-column plan (with TODAY) needs 1215px for a supervisor and 1128px for a recruiter', () => {
  const full = m.taskColumnsFor(true, true)
  assert.equal(full.length, 12)
  assert.deepEqual(full.map((c) => c.key), ['no', 'doc', 'client', 'position', 'candidate', 'staff', 'agency', 'stage', 'today', 'remarks', 'notes', 'actions'])
  assert.equal(m.taskPlanWidth(true, true), 1215)
  assert.equal(m.taskPlanWidth(false, true), 1128)
  assert.equal(m.taskColumnsFor(true).length, 12, 'the default plan is the full one')
})

test('where TODAY is shown at each panel width', () => {
  const at = (supervisor, width) => m.todayPlace(supervisor, width)
  // the owner's 125% zoom (a 1225px table) holds the supervisor's whole plan only from 1215px
  assert.deepEqual([1080, 1111, 1150, 1211].map((w) => at(true, w)), ['stage', 'stage', 'stage', 'stage'])
  assert.deepEqual([1215, 1225, 1366, 1500, 1590].map((w) => at(true, w)), ['column', 'column', 'column', 'column', 'column'])
  assert.deepEqual([1080, 1111, 1124].map((w) => at(false, w)), ['stage', 'stage', 'stage'])
  assert.equal(at(false, 1128), 'column')
  assert.equal(at(true, 1079), 'row', 'under 1080px a card has a TODAY row')
  assert.equal(at(true, 390), 'row')
  assert.equal(at(true, 0), 'stage', 'not measured yet')
  assert.equal(m.TASK_CARD_BREAKPOINT, 1080)
})

/* ------------------------------------------------------------------ newest first, all of them */

test('notes are sorted newest first whatever the order they arrive in; equal times keep the arrival order', () => {
  const arrived = [note('a', '2026-11-19T05:00:00Z'), note('c', '2026-11-20T04:00:00Z'), note('b', '2026-11-19T07:00:00Z'), note('d', '2026-11-19T07:00:00Z')]
  assert.deepEqual(m.notesNewestFirst(arrived).map((n) => n.id), ['c', 'b', 'd', 'a'])
  assert.deepEqual(arrived.map((n) => n.id), ['a', 'c', 'b', 'd'], 'the input is not changed')
  assert.deepEqual(m.notesNewestFirst(undefined), [])
  assert.deepEqual(m.notesNewestFirst(null), [])
  assert.deepEqual(m.notesNewestFirst([note('x', 'not a date'), note('y', '2026-11-19T07:00:00Z')]).map((n) => n.id), ['y', 'x'])
})

test('a note shows "11:40" today and "18 Nov 09:15" on an earlier day', () => {
  const now = '2026-11-19T10:00:00Z'
  assert.equal(m.noteWhen('2026-11-19T06:10:00Z', ZONE, now), '11:40')
  assert.equal(m.noteWhen('2026-11-18T03:45:00Z', ZONE, now), '18 Nov 09:15')
  assert.equal(m.noteWhen('2026-11-01T03:45:00Z', ZONE, now), '1 Nov 09:15')
  assert.equal(m.noteWhen('2026-11-17T19:00:00Z', ZONE, now), '18 Nov 00:30', 'midnight follows the agency clock, not UTC')
  assert.equal(m.noteWhen('garbage', ZONE, now), '--:--')
})

test('the rows keep every note, and deleting hides it from both lists', () => {
  const row = { notes: [note('n2', '2026-11-19T07:00:00Z'), note('n1', '2026-11-18T07:00:00Z')], todayNotes: [note('n2', '2026-11-19T07:00:00Z')] }
  const gone = hide.withoutNotes(row, new Set(['n2']))
  assert.deepEqual(gone.notes.map((n) => n.id), ['n1'])
  assert.deepEqual(gone.todayNotes, [])
  assert.equal(hide.withoutNotes(row, new Set(['zzz'])), row, 'nothing to hide: the same object')
  assert.deepEqual(hide.withoutNotes({ todayNotes: [note('a', 'x')] }, new Set(['a'])), { todayNotes: [] }, 'an older server sends no notes list')
})

/* ------------------------------------------------------------------ typing a note */

test('a typed note is trimmed and never empty', () => {
  assert.equal(m.noteText('  Called him; will pay in the afternoon \n'), 'Called him; will pay in the afternoon')
  assert.equal(m.noteText('   \n '), null)
  assert.equal(m.noteText('x'.repeat(10050)).length, 10000)
})

test('sendNote shows the note at once, saves it and drops the pending copy once the server and the list answered', async () => {
  const log = []
  const now = new Date('2026-11-19T06:10:00Z')
  let release
  const add = (body) => new Promise((resolve) => { log.push(`add ${body}`); release = resolve })
  const running = m.sendNote('  Will pay at 3  ', add, { pending: (n) => log.push(`pending ${n.body} ${n.by} ${n.id.startsWith(m.PENDING_NOTE)}`), settled: () => log.push('settled') }, 'Yuvaraj', now)
  assert.deepEqual(log, ['pending Will pay at 3 Yuvaraj true', 'add Will pay at 3'], 'the note is shown before the request answers')
  release()
  assert.deepEqual(await running, { sent: true, body: 'Will pay at 3' })
  assert.equal(log.at(-1), 'settled')
})

test('sendNote keeps the text and says why when the server refuses, and sends nothing for an empty box', async () => {
  const calls = []
  const refused = await m.sendNote('Hello', async () => { throw new Error('Only the assigned recruiter or a supervisor can update this task') }, { settled: () => calls.push('settled') })
  assert.deepEqual(refused, { sent: false, body: 'Hello', error: 'Only the assigned recruiter or a supervisor can update this task' })
  assert.deepEqual(calls, ['settled'])
  const odd = await m.sendNote('Hello', async () => { throw 'nope' })
  assert.match(odd.error, /could not be saved/)
  let called = false
  assert.deepEqual(await m.sendNote('   ', async () => { called = true }), { sent: false, body: '' })
  assert.equal(called, false)
})

test('the store writes a note with kind "note" to the same endpoint, and a remark as before', async () => {
  const store = await code('../src/shared/state/crm-store.tsx')
  assert.match(store, /addRemark: async \(taskId, body, kind = 'remark'\) => \{\s*await run\(\(\) => api\.post\(`\/tasks\/\$\{taskId\}\/remarks`, kind === 'note' \? \{ body, kind \} : \{ body \}\)/)
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /store\.addRemark\(row\.taskId, text, 'note'\)/)
  assert.match(screen, /onRemark=\{async \(row, text\) => \{ if \(row\.taskId\) await store\.addRemark\(row\.taskId, text\); else if \(isClosed\(row\)/, 'the Remarks select still writes a remark')
})

/* ------------------------------------------------------------------ the screen and the styles */

test('the table: NOTES always a column, the Remarks cell holds only the select, the TODAY chip is in STAGE or in its own column', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.doesNotMatch(table, /has-notes|tw-remark-notes|showsNotesColumn|tw-remark-note-text/)
  assert.match(table, /todayPlace\(supervisor, panelWidth\)/)
  assert.match(table, /taskColumnsFor\(supervisor, place !== 'stage', closedRowsOf\(rows\)\)/)
  assert.match(table, /<StageCell item=\{row\} zone=\{zone\} \/>\{place === 'stage' && !closed && <TodayChips row=\{row\} zone=\{zone\} inStage \/>\}/)
  assert.match(table, /\{showToday && \(\s*<td data-label="TODAY"/)
  // the REMARKS cell: the select (or a dash) and nothing else
  const remarks = table.slice(table.indexOf('<td data-label="REMARKS"'), table.indexOf('<td data-label="NOTES"'))
  assert.match(remarks, /<RemarkCell /)
  assert.doesNotMatch(remarks, /note|Note|Trash|NoteDeleteButton/)
  // the NOTES cell: every note, newest first, no "+N more" and no dialog list
  assert.doesNotMatch(table, /\+\{more\} more|tw-notes-more|setListing/)
  assert.match(table, /\[\.\.\.pending, \.\.\.notesNewestFirst\(row\.notes \?\? row\.todayNotes\)\]/)
  assert.match(table, /notes\.map\(\(note\) =>/)
  assert.match(table, /noteWhen\(note\.at, zone\)/)
  assert.match(table, /<small>\{note\.by\}<\/small>/)
})

test('the input: always visible for people who may work the candidate, Enter saves, Shift+Enter is a new line, Escape clears, disabled while saving', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /canAdd=\{work && \(!!row\.taskId \|\| closed\)\}/, 'the same rule as the Remarks select')
  assert.match(table, /placeholder="Add a note…"/)
  assert.match(table, /event\.key === 'Enter' && !event\.shiftKey && !event\.nativeEvent\.isComposing/)
  assert.match(table, /event\.key === 'Escape' && text/)
  assert.match(table, /disabled=\{saving\}/)
  assert.match(table, /useAutoGrow\(box, text, \{ minRows: 1, maxRows: 3/)
  assert.match(table, /aria-label=\{`Save note for \$\{row\.candidate\}`\}/)
  assert.match(table, /role="alert">\{error\}/, 'an error is shown inline')
  assert.match(table, /if \(result\.error\) \{ setError\(result\.error\); setText\(result\.body\) \}/, 'the text comes back after an error')
  assert.match(table, /\{typing && \(/, 'a read-only person sees the notes without the input')
})

test('the styles: no second line under the remark, the list is at most 260px and scrolls, the card variant is larger', async () => {
  const css = await read('../src/styles/tasks-table.css')
  assert.doesNotMatch(css, /has-notes|tw-remark-notes|tw-notes-more/)
  assert.match(css, /\.tw-note-list \{[^}]*max-height: 260px[^}]*overflow-y: auto/)
  assert.match(css, /\.tw-note-input \{[^}]*min-height: 32px/)
  assert.match(css, /\.tw-note-body \{[^}]*overflow-wrap: anywhere/)
  assert.match(css, /td\[data-label='NOTES'\] \{ grid-column: 1 \/ -1; \}/)
  assert.match(css, /\.tw-today\.is-in-stage \{ margin-top: 6px; \}/)
  assert.match(css, /--tw-pad-l: 6px; --tw-pad-r: 6px;/)
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ''), /#[0-9a-fA-F]{3,8}\b|rgb\(/, 'tokens only')
})
