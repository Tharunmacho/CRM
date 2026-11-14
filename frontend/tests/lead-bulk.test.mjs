import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const b = await load('../src/features/leads/lead-bulk-model.ts')
const labelOf = (status) => ({ contacted: 'Contacted', not_interested: 'Not interested', new: 'New' })[status] ?? status

test('selection survives page changes and the table reports exactly what is ticked', () => {
  let selection = b.EMPTY_SELECTION
  selection = b.changeSelection(selection, ['a', 'b'])            // page 1: two rows ticked
  selection = b.changeSelection(selection, [...selection.ids, 'x']) // page 2: one more; page 1's ids are still in the list the table passes back
  assert.deepEqual(selection, { ids: ['a', 'b', 'x'], allMatching: false })
  assert.equal(b.selectionCount(selection, 3000), 3)
  assert.deepEqual(b.visibleSelected(selection, ['p1', 'p2']), ['a', 'b', 'x'])
  selection = b.changeSelection(selection, ['a', 'b', 'a'])         // duplicates collapse
  assert.deepEqual(selection.ids, ['a', 'b'])
  assert.equal(b.hasSelection(b.EMPTY_SELECTION, 100), false)
})

test('select all matching is filter based, counts the whole result and leaves on any manual change', () => {
  let selection = b.changeSelection(b.EMPTY_SELECTION, ['a', 'b', 'c'])
  assert.equal(b.canOfferAllMatching(selection, 3000), true)
  assert.equal(b.canOfferAllMatching(selection, 3), false, 'everything is already ticked')
  assert.equal(b.canOfferAllMatching(b.EMPTY_SELECTION, 3000), false, 'nothing ticked: no offer')
  selection = b.selectAllMatching(selection)
  assert.equal(selection.allMatching, true)
  assert.equal(b.selectionCount(selection, 3000), 3000)
  assert.equal(b.canOfferAllMatching(selection, 3000), false)
  assert.equal(b.selectionLine(selection, 3000), 'All 3,000 matching leads are selected')
  assert.equal(b.selectionLine(b.changeSelection(b.EMPTY_SELECTION, ['a']), 3000), '1 selected')
  assert.deepEqual(b.visibleSelected(selection, ['p1', 'p2']), ['p1', 'p2'], 'every row of the page shows ticked')
  const left = b.changeSelection(selection, ['p2']) // the person unticked p1
  assert.deepEqual(left, { ids: ['p2'], allMatching: false })
  assert.equal(b.selectionCount(left, 3000), 1)
})

test('more matches than one call can hold cannot be selected as a whole', () => {
  assert.equal(b.BULK_LIMIT, 10000)
  assert.equal(b.allMatchingBlock(10000), null)
  assert.match(b.allMatchingBlock(10001), /10,001 leads match.*up to 10,000/)
})

test('the filter sent for "all matching" is the list query without paging, sorting or extras', () => {
  const query = 'q=chef%2Ckerala&status=new%2Ccontacted&owner=none&created_from=2026-11-01&sort=created&direction=desc&page=2&page_size=25&extra=*'
  assert.deepEqual(b.filterFromQuery(query), { q: 'chef,kerala', status: 'new,contacted', owner: 'none', created_from: '2026-11-01' })
  assert.deepEqual(b.filterFromQuery('status=new&status=interested&campaign=&sort=name'), { status: ['new', 'interested'] })
  assert.deepEqual(b.filterFromQuery(''), {})
})

test('request bodies: explicit ids, or the filter with the count the person saw', () => {
  const filter = { q: 'chef' }
  assert.deepEqual(b.bulkBody({ ids: ['a', 'b'], allMatching: false }, filter, 50, 'status', { status: 'contacted' }), { action: 'status', ids: ['a', 'b'], status: 'contacted' })
  assert.deepEqual(b.bulkBody({ ids: ['a'], allMatching: true }, filter, 3000, 'assign', { ownerId: 'auto' }), { action: 'assign', filter, expectedCount: 3000, ownerId: 'auto' })
  assert.deepEqual(b.bulkBody({ ids: ['a'], allMatching: false }, filter, 50, 'not_interested', { status: 'contacted' }), { action: 'not_interested', ids: ['a'] }, 'a status option is only sent with the status action')
  assert.deepEqual(b.bulkBody({ ids: [], allMatching: true }, filter, 40, 'delete', { confirm: 'DELETE' }), { action: 'delete', filter, expectedCount: 40, confirm: 'DELETE' })
  assert.deepEqual(b.bulkBody({ ids: ['a'], allMatching: false }, filter, 50, 'export', { sort: 'name', direction: 'asc' }), { action: 'export', ids: ['a'], sort: 'name', direction: 'asc' })
})

test('the confirmation states the exact count and the action', () => {
  const assign = b.confirmCopy({ action: 'assign', count: 3000, allMatching: true, ownerName: 'auto' })
  assert.equal(assign.title, 'Assign 3,000 leads')
  assert.match(assign.lines[0], /^All 3,000 leads that match the current search and filters \(not only the ones on this page\) will be assigned to the available recruiters, spread evenly\.$/)
  assert.equal(assign.confirmLabel, 'Assign 3,000 leads')
  assert.match(b.confirmCopy({ action: 'assign', count: 1, allMatching: false, ownerName: 'Asha' }).lines[0], /^1 lead will be assigned to Asha\.$/)
  const status = b.confirmCopy({ action: 'status', count: 12, allMatching: false, statusLabel: 'Contacted' })
  assert.equal(status.title, 'Change status of 12 leads')
  assert.match(status.lines[0], /^12 leads will be marked Contacted\.$/)
  assert.equal(b.confirmCopy({ action: 'not_interested', count: 2, allMatching: false }).title, 'Mark 2 leads not interested')
  assert.equal(status.danger, false)
})

test('delete: dangerous, says it comes back with a later upload, and above 20 leads needs the typed word', () => {
  const few = b.confirmCopy({ action: 'delete', count: 20, allMatching: false })
  assert.equal(few.danger, true)
  assert.equal(few.typed, false)
  assert.ok(few.lines.some((line) => line.includes('If this person is in a later upload they will be added again.')))
  assert.ok(few.lines.some((line) => line.includes('converted to a candidate are skipped')))
  const many = b.confirmCopy({ action: 'delete', count: 21, allMatching: true })
  assert.equal(many.typed, true)
  assert.equal(many.title, 'Delete 21 leads')
  assert.ok(many.lines.includes('Type DELETE to confirm.'))
  assert.equal(b.needsTypedConfirm('delete', 20), false)
  assert.equal(b.needsTypedConfirm('delete', 21), true)
  assert.equal(b.needsTypedConfirm('status', 5000), false)
  assert.equal(b.typedConfirmOk('DELETE'), true)
  assert.equal(b.typedConfirmOk(' DELETE '), true)
  assert.equal(b.typedConfirmOk('delete'), false)
  const one = b.deleteOneCopy('Test Lead 00001')
  assert.match(one.lines[0], /^Test Lead 00001 will be permanently deleted/)
  assert.ok(one.lines.some((line) => line.includes('later upload they will be added again')))
})

test('result toast: N leads updated, K skipped (reasons)', () => {
  assert.equal(b.resultToast({ updated: 5, skipped: 0 }), '5 leads updated.')
  assert.equal(b.resultToast({ updated: 1, skipped: 0 }, 'deleted'), '1 lead deleted.')
  assert.equal(
    b.resultToast({ updated: 2998, skipped: 2, skippedReasons: [{ code: 'converted', count: 2, message: 'converted or linked to a candidate' }] }),
    '2,998 leads updated, 2 skipped (2 converted or linked to a candidate).',
  )
  assert.equal(
    b.resultToast({ updated: 0, skipped: 5, skippedReasons: [{ code: 'already_set', count: 3, message: 'already had that status' }, { code: 'gone', count: 2, message: 'not found (deleted meanwhile)' }, { code: 'x', count: 0, message: 'hidden' }] }, 'assigned'),
    '0 leads assigned, 5 skipped (3 already had that status; 2 not found (deleted meanwhile)).',
  )
  assert.equal(b.resultVerb('delete'), 'deleted')
  assert.equal(b.resultVerb('assign'), 'assigned')
  assert.equal(b.resultVerb('status'), 'updated')
})

test('undo: remembers the previous status, restores it with the previous value, and expires', () => {
  const lead = { id: 'l1', name: 'Test Lead', status: 'new' }
  const entry = b.undoEntry(lead, 'not_interested', 1000)
  assert.deepEqual(entry, { leadId: 'l1', name: 'Test Lead', from: 'new', to: 'not_interested', at: 1000 })
  assert.equal(b.undoEntry(lead, 'new'), null, 'no change, nothing to undo')
  assert.equal(b.statusToast('not_interested', labelOf), 'Marked not interested.')
  assert.equal(b.statusToast('contacted', labelOf), 'Status changed to Contacted.')
  assert.deepEqual(b.undoBody(entry), { status: 'new' })
  assert.equal(b.undoneToast(entry, labelOf), 'Status restored to New.')
  assert.equal(b.undoAlive(entry, 1000 + b.UNDO_MS - 1), true)
  assert.equal(b.undoAlive(entry, 1000 + b.UNDO_MS), false)
  assert.equal(b.undoAlive(null), false)
})

test('make unassigned: ownerId "none", its own confirmation and toast wording', () => {
  assert.equal(b.UNASSIGNED, 'none')
  assert.deepEqual(b.bulkBody({ ids: ['a', 'b'], allMatching: false }, {}, 2, 'assign', { ownerId: b.UNASSIGNED }), { action: 'assign', ids: ['a', 'b'], ownerId: 'none' })
  const copy = b.confirmCopy({ action: 'assign', count: 3, allMatching: false, ownerName: 'none' })
  assert.equal(copy.title, 'Make 3 leads unassigned')
  assert.equal(copy.confirmLabel, 'Make 3 leads unassigned')
  assert.match(copy.lines[0], /^3 leads will be taken back from their recruiter and will not be assigned to anyone\.$/)
  assert.match(copy.lines[1], /status and history stay/)
  assert.equal(copy.danger, false)
  assert.equal(b.resultVerb('assign', 'none'), 'made unassigned')
  assert.equal(b.resultVerb('assign', 'u1'), 'assigned')
  assert.equal(b.resultToast({ updated: 2, skipped: 1, skippedReasons: [{ code: 'already_unassigned', count: 1, message: 'already not assigned to anyone' }] }, b.resultVerb('assign', 'none')),
    '2 leads made unassigned, 1 skipped (1 already not assigned to anyone).')
})

test('make unassigned for one lead names the person it is taken from', () => {
  const copy = b.unassignOneCopy('Priya Nair', 'Asha Nair')
  assert.equal(copy.title, 'Make this lead unassigned')
  assert.equal(copy.confirmLabel, 'Make unassigned')
  assert.match(copy.lines[0], /^Priya Nair will be taken back from Asha Nair and will not be assigned to anyone\.$/)
  assert.match(copy.lines[1], /^Asha Nair no longer sees it/)
  assert.match(b.unassignOneCopy('', '').lines[0], /^This lead will be taken back from the current recruiter/)
})
