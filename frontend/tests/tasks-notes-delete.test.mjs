import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(withRoleLabels(outputText)).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const notes = await import(moduleUrl(await read('../src/features/daily-work/note-delete-model.ts')))
const daily = await import(moduleUrl(await read('../src/features/daily-work/daily-work-model.ts')))

/* ------------------------------------------------------------------ who may delete a note (the server's rule, mirrored) */

test("a note may be deleted by its author, the candidate's recruiter, an Admin and an MD, and by nobody else", () => {
  const row = { ownerId: 'asha' }
  const note = { byId: 'ravi' }
  assert.equal(notes.canDeleteNote(note, row, { id: 'ravi', role: 'staff' }), true, 'the author')
  assert.equal(notes.canDeleteNote(note, row, { id: 'asha', role: 'staff' }), true, "the candidate's recruiter")
  assert.equal(notes.canDeleteNote(note, row, { id: 'boss', role: 'admin' }), true, 'an Admin')
  assert.equal(notes.canDeleteNote(note, row, { id: 'md', role: 'manager' }), true, 'an MD')
  assert.equal(notes.canDeleteNote(note, row, { id: 'other', role: 'staff' }), false, 'another recruiter')
  assert.equal(notes.canDeleteNote({}, row, { id: 'other', role: 'staff' }), false, 'a note with no known author')
  assert.equal(notes.canDeleteNote({ byId: null }, { ownerId: '' }, { id: '', role: 'staff' }), false, 'nobody signed in')
  assert.equal(notes.canDeleteNote(note, row, null), false)
  assert.equal(notes.canDeleteNote(note, row, undefined), false)
})

test('a deleted note leaves the row at once: only the hidden ids go, the row is returned as it was when none is hidden', () => {
  const row = { id: 'c1', todayNotes: [{ id: 'n1', body: 'a' }, { id: 'n2', body: 'b' }] }
  assert.equal(notes.withoutNotes(row, new Set()), row)
  assert.equal(notes.withoutNotes(row, new Set(['zzz'])), row, 'nothing to hide: the same object (no needless re-render)')
  assert.deepEqual(notes.withoutNotes(row, new Set(['n1'])).todayNotes.map((n) => n.id), ['n2'])
  assert.deepEqual(notes.withoutNotes(row, new Set(['n1', 'n2'])).todayNotes, [])
  assert.deepEqual(row.todayNotes.map((n) => n.id), ['n1', 'n2'], 'the original is untouched')
  assert.deepEqual(notes.withoutNotes({ id: 'c2' }, new Set(['n1'])), { id: 'c2' })
})

test('hiding the last note of a row also takes the "Note 10:30" mark off it (the row is re-derived)', () => {
  const base = { kind: 'stage', status: 'open', waiting: false, completedAt: null, referral: { source: '', label: '', detail: null }, stage: 3, ownerId: 'u1', remark: null }
  const item = { ...base, id: 'a', candidateId: 'c1', todayNotes: [{ id: 'n1', body: 'Called', at: '2026-11-19T07:00:00Z', by: 'Asha' }] }
  assert.equal(daily.taskRows([item], [], '2026-11-19', 'Asia/Kolkata')[0].today.remarked, true)
  const hidden = notes.withoutNotes(item, new Set(['n1']))
  assert.equal(daily.taskRows([hidden], [], '2026-11-19', 'Asia/Kolkata')[0].today.remarked, false)
})

test('a failed delete says what the server said, else a calm default', () => {
  assert.equal(notes.deleteFailure(new Error('Only the person who wrote this note can delete it')), 'Only the person who wrote this note can delete it')
  assert.equal(notes.deleteFailure(new Error('')), 'The note could not be deleted. Please try again.')
  assert.equal(notes.deleteFailure('boom'), notes.DELETE_NOTE_ERROR)
  assert.equal(notes.DELETE_NOTE_LABEL, 'Delete note')
})

test('the server note carries its author id (byId) so the button knows who may delete it', async () => {
  const model = await code('../src/features/daily-work/daily-work-model.ts')
  assert.match(model, /byId\?: string \| null/)
  const note = { id: 'n1', body: 'x', at: '2026-11-19T07:00:00Z', by: 'Asha', byId: 'u1' }
  assert.equal(notes.canDeleteNote(note, { ownerId: 'someone' }, { id: 'u1', role: 'staff' }), true)
})

/* ------------------------------------------------------------------ the screen */

test('every note of the NOTES list (table and phone card) has a "Delete note" icon button that asks first in a dialog (never window.confirm)', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /aria-label=\{DELETE_NOTE_LABEL\}/)
  assert.equal((table.match(/<NoteDeleteButton /g) ?? []).length, 1, 'one button per note of the NOTES list (the table and the phone card are the same cell)')
  assert.match(table, /mayDelete\(row, note\)/)
  assert.match(table, /note\.id\.startsWith\(PENDING_NOTE\)/, 'a note that is not saved yet has no delete button')
  assert.match(table, /<Dialog open=\{!!asking\}/)
  assert.match(table, /variant="danger"/)
  assert.doesNotMatch(table, /window\.confirm|confirm\(/)
  assert.match(table, /onDeleteNote\?\.\(target\.row, target\.note\)/, 'nothing is deleted before the dialog is confirmed')
  assert.match(table, /canDeleteNote\?\.\(row, note\)/, 'without the rule no button is drawn')
  assert.match(table, /onClear=\{onClearRemark && \(row\.taskId/)
})

test('the page hides the note at once, deletes through the store, refreshes, and brings the note back with a message when the server refuses', async () => {
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /setHiddenNotes\(\(previous\) => new Set\(previous\)\.add\(id\)\)/)
  assert.match(screen, /await store\.deleteRemark\(id\)/)
  assert.match(screen, /catch \(failure\) \{ forgetNote\(id\); setNoteError\(deleteFailure\(failure\)\); return \}/)
  assert.match(screen, /await refreshAll\(\)/)
  assert.match(screen, /\{noteError && <p className="is-warn" role="alert">/)
  assert.match(screen, /canDeleteNote=\{\(row, note\) => canDeleteNote\(note, row, store\.me\)\}/)
  assert.match(screen, /store\.clearRemark\(row\.taskId\)/)
  assert.match(screen, /taskRows\(hide\(shown\?\.data\.items/, 'the hidden notes are removed before the rows are derived')
})

test('the store deletes a note with DELETE /remarks/{id} and clears a remark cell with POST /tasks/{id}/remarks/clear', async () => {
  const store = await code('../src/shared/state/crm-store.tsx')
  assert.match(store, /deleteRemark: async \(remarkId\) => \{\s*await run\(\(\) => api\.del\(`\/remarks\/\$\{encodeURIComponent\(remarkId\)\}`\)/)
  assert.match(store, /clearRemark: async \(taskId\) => \{\s*await run\(\(\) => api\.post\(`\/tasks\/\$\{encodeURIComponent\(taskId\)\}\/remarks\/clear`\)/)
})

test('the note button is a small quiet trash (44px on touch), red on hover, and the "Select remark" entry is muted in the popup', async () => {
  const css = await read('../src/styles/tasks-table.css')
  assert.match(css, /\.tw-note-del \{[^}]*width: 24px; height: 24px/)
  assert.match(css, /\.tw-note-del:hover \{[^}]*var\(--error-fill\)/)
  assert.match(css, /@media \(pointer: coarse\) \{ \.tw-note-del \{ width: 44px; height: 44px; \} \}/)
  assert.match(css, /\.tw-note-del:focus-visible/)
  const controls = await read('../src/styles/controls.css')
  assert.match(controls, /\.custom-select-option\.is-muted \{[^}]*color: var\(--text-muted\)/)
  const picker = await code('../src/shared/ui/select.tsx')
  assert.match(picker, /muted: child\.props\['data-muted'\] !== undefined/)
  assert.match(picker, /option\.muted && 'is-muted'/)
})
