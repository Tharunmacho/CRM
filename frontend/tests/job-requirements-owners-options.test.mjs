import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/* Several recruiters on one requirement, and the field catalog the staff add to (src/features/job-requirements/job-requirements-model.ts). */
const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const m = await import(moduleUrl(await read('../src/features/job-requirements/job-requirements-model.ts')))

const item = (over = {}) => ({
  id: 'a', docDate: '2026-11-21', clientName: 'xyz company', country: 'Kuwait', details: {}, adUrl: null, adName: null, status: 'active', custom: {},
  createdAt: '2026-11-21T08:00:00Z', updatedAt: '2026-11-21T08:00:00Z', createdBy: null, updatedBy: null, ...over,
})
const withOwners = (...names) => item({ owners: names.map((name, i) => ({ id: `u${i + 1}`, name })), ownerIds: names.map((_, i) => `u${i + 1}`), ownerId: 'u1', ownerName: names[0] })

test('the owners of a row: the list, or the single owner of an older answer; the cell says "Asha", "Asha, Ravi" or "Asha +2"', () => {
  assert.deepEqual(m.itemOwners(withOwners('Asha', 'Ravi')).map((o) => o.name), ['Asha', 'Ravi'])
  assert.deepEqual(m.itemOwners(item({ ownerId: 'u9', ownerName: 'Old' })), [{ id: 'u9', name: 'Old' }], 'an answer without the list still reads')
  assert.deepEqual(m.itemOwners(item()), [])
  assert.equal(m.ownersLabel([]), 'Unassigned')
  assert.equal(m.ownersLabel([{ id: '1', name: 'Asha Menon' }]), 'Asha Menon')
  assert.equal(m.ownersLabel([{ id: '1', name: 'Asha Menon' }, { id: '2', name: 'Ravi Kumar' }]), 'Asha, Ravi', 'first names when there are several')
  assert.equal(m.ownersLabel([{ id: '1', name: 'Asha' }, { id: '2', name: 'Ravi' }, { id: '3', name: 'Mia' }, { id: '4', name: 'Zed' }]), 'Asha +3')
  assert.equal(m.ownersFull([{ id: '1', name: 'Asha' }, { id: '2', name: 'Ravi' }]), 'Asha, Ravi')
  assert.equal(m.ownersFull([]), 'nobody')
  assert.equal(m.OWNERS_MAX, 15)
})

test('choosing owners: the optimistic copy has the ids, the names (from the people list) and the first owner as the legacy owner', () => {
  const people = [{ id: 'u1', name: 'Asha' }, { id: 'u2', name: 'Ravi' }]
  const edited = m.applyPatch(item({ ownerId: 'u9', ownerName: 'Old' }), { ownerIds: ['u2', 'u1'] }, people)
  assert.deepEqual(edited.ownerIds, ['u2', 'u1'])
  assert.deepEqual(edited.owners.map((o) => o.name), ['Ravi', 'Asha'])
  assert.deepEqual([edited.ownerId, edited.ownerName], ['u2', 'Ravi'])
  const cleared = m.applyPatch(withOwners('Asha'), { ownerIds: [] })
  assert.deepEqual([cleared.ownerIds, cleared.owners, cleared.ownerId, cleared.ownerName], [[], [], null, null], '[] unassigns')
  assert.equal(m.applyPatch(withOwners('Asha'), { ownerIds: ['u1'] }).owners[0].name, 'Asha', 'a name already on the row is kept without a people list')
  assert.deepEqual(m.applyPatch(withOwners('Asha'), { clientName: 'x' }).ownerIds, ['u1'], 'untouched when the patch has no ownerIds')
  assert.deepEqual(m.mergePatches({ ownerIds: ['a'] }, { ownerIds: ['a', 'b'] }), { ownerIds: ['a', 'b'] })
  assert.equal(m.duplicateBody(withOwners('Asha', 'Ravi')).ownerIds, undefined, 'a duplicate goes to the person who makes it')
  const held = m.mergeRead([item()], { a: { ownerIds: ['u1'] } }, [], new Set(), people)
  assert.equal(held[0].owners[0].name, 'Asha', 'a held change laid over a fresh read shows names too')
})

test('every current owner stays among the choices, even an inactive or removed one; admins never appear', () => {
  const users = [
    { id: 's1', name: 'Zed', role: 'staff', active: true }, { id: 'm1', name: 'Mia', role: 'manager', active: true }, { id: 'a1', name: 'Boss', role: 'admin', active: true },
    { id: 's2', name: 'Off', role: 'staff', active: false },
  ]
  assert.deepEqual(m.ownerChoices(users, { owners: [{ id: 's2', name: 'Off' }, { id: 's3', name: 'Gone' }] }).map((u) => u.name), ['Gone', 'Mia', 'Off', 'Zed'])
  assert.deepEqual(m.ownerChoices(users, { ownerId: 's2', ownerName: 'Off' }).map((u) => u.name), ['Mia', 'Off', 'Zed'])
  assert.deepEqual(m.ownerChoices(users).map((u) => u.name), ['Mia', 'Zed'])
})

test("after a save the server's owners (with names) are taken, unless the person has changed them since", () => {
  const local = m.applyPatch(item(), { ownerIds: ['u1', 'u2'] })
  const saved = item({ ownerIds: ['u1', 'u2'], owners: [{ id: 'u1', name: 'Asha' }, { id: 'u2', name: 'Ravi' }], ownerId: 'u1', ownerName: 'Asha' })
  assert.deepEqual(m.mergeSaved(local, saved).owners.map((o) => o.name), ['Asha', 'Ravi'])
  const changedSince = m.applyPatch(local, { ownerIds: ['u3'] })
  assert.deepEqual(m.mergeSaved(changedSince, saved).ownerIds, ['u3'], 'what was ticked since stays')
})

test('the summary counts every owner of an active row', () => {
  const summary = m.localSummary([withOwners('Asha', 'Ravi'), withOwners('Asha'), { ...withOwners('Mia'), status: 'closed' }], '2026-11-21')
  assert.deepEqual(summary.byOwner.map((o) => [o.id, o.name, o.active]), [['u1', 'Asha', 2], ['u2', 'Ravi', 1]])
})

/* ---- the field catalog ---- */

const opt = (id, kind, label, over = {}) => ({ id, kind, label, standard: false, sort: 0, ...over })

test("the pickers suggest the built-in names, then the catalog's, each name once whatever its case", () => {
  const options = [opt('1', 'requirement', 'visa status', { sort: 2 }), opt('2', 'requirement', 'QUALIFICATION'), opt('3', 'requirement', 'Height', { sort: 1 }), opt('4', 'particular', 'Joining date')]
  const names = m.mergeSuggestions(m.REQUIREMENT_FIELD_SUGGESTIONS, options, 'requirement')
  assert.deepEqual(names.slice(0, m.REQUIREMENT_FIELD_SUGGESTIONS.length), [...m.REQUIREMENT_FIELD_SUGGESTIONS])
  assert.deepEqual(names.slice(m.REQUIREMENT_FIELD_SUGGESTIONS.length), ['Height', 'visa status'], 'a duplicate of a built-in name is left out, the rest in catalog order; other kinds are not mixed in')
  assert.deepEqual(m.mergeSuggestions([], options, 'particular'), ['Joining date'])
  assert.deepEqual(m.mergeSuggestions(['A', ' a '], [], 'requirement'), ['A'])
  assert.deepEqual(m.optionsOf(options, 'requirement').map((o) => o.label), ['QUALIFICATION', 'Height', 'visa status'])
})

test('standard particulars are rows of every form after the built-in eight, and an empty one is not stored', () => {
  const options = [opt('1', 'particular', 'Joining date', { standard: true, sort: 1 }), opt('2', 'particular', 'Medical'), opt('3', 'particular', 'food', { standard: true }), opt('4', 'requirement', 'Height', { standard: true })]
  assert.deepEqual(m.defaultParticulars(options), [...m.GENERAL_DEFAULTS, 'Joining date'], 'a standard name that is already built in is not added twice; requirement fields are never particulars')
  const form = m.formFor(item(), options)
  assert.deepEqual(form.general.map((r) => r.label), [...m.GENERAL_DEFAULTS, 'Joining date'])
  assert.deepEqual(m.buildPatch(form, m.formFor(item(), options)), {}, 'nothing typed, nothing sent')
  const typed = m.formFor(item(), options)
  typed.general[8].details = '1 January'
  assert.deepEqual(m.buildPatch(form, typed).general.map((r) => r.label), ['Joining date'])
  const saved = m.formFor(item({ general: [{ id: 'g', label: 'joining DATE', details: 'Soon', remarks: '' }] }), options)
  assert.equal(saved.general[8].details, 'Soon', 'a saved row of that name fills the standard row')
  assert.equal(saved.general.length, 9)
  assert.equal(m.formFor(item()).general.length, 8, 'without a catalog it is the built-in eight')
})

test('a field name is required, at most 100 characters and not already in the list (built-in or added, any case)', () => {
  const options = [opt('1', 'requirement', 'Height'), opt('2', 'particular', 'Joining date')]
  assert.equal(m.optionNameError('', 'requirement', options), 'Type a name')
  assert.equal(m.optionNameError('   ', 'requirement', options), 'Type a name')
  assert.match(m.optionNameError('x'.repeat(101), 'requirement', options), /100/)
  assert.match(m.optionNameError('  height ', 'requirement', options), /already/)
  assert.match(m.optionNameError('experience', 'requirement', options), /already/, 'a built-in name')
  assert.match(m.optionNameError('Food', 'particular', options), /already/)
  assert.equal(m.optionNameError('Food', 'requirement', options), '', 'the other list does not count')
  assert.equal(m.optionNameError('Weight', 'requirement', options), '')
  assert.equal(m.optionNameError('HEIGHT', 'requirement', options, '1'), '', 'renaming a field to its own name in another case is fine')
  assert.equal(m.optionNameError('x'.repeat(100), 'requirement', options), '')
  assert.deepEqual(m.upsertOption(options, opt('3', 'requirement', 'Age', { sort: -1 })).map((o) => o.id), ['3', '1', '2'])
  assert.ok(m.upsertOption(options, opt('1', 'requirement', 'Renamed')).some((o) => o.id === '1' && o.label === 'Renamed'))
})

/* ---- the screens ---- */

const form = await read('../src/screens/job-requirements/job-requirement-form.tsx')
const page = await read('../src/screens/job-requirements/job-requirements.tsx')
const owners = await read('../src/screens/job-requirements/job-requirement-owners.tsx')
const hook = await read('../src/features/job-requirements/use-job-requirements.ts')
const api = await read('../src/features/job-requirements/job-requirements-api.ts')
const suggest = await read('../src/shared/ui/suggest-input.tsx')

test('Assigned to is a checklist popover that saves once, when it closes, and only if the choice changed', () => {
  assert.match(page, /<OwnersCell item=\{item\} choices=\{ownerChoices\(store\.users, item\)\}/)
  assert.match(page, /keep\(item\.id, \{ ownerIds: ids \}\)/)
  assert.doesNotMatch(page, /className="jr-owner"/, 'the single select is gone')
  assert.match(owners, /if \(!sameSet\(picked, before\)\) save\(picked\)/, 'saved on close, only when changed')
  assert.match(owners, /event\.key === 'Escape'/)
  assert.match(owners, /event\.key !== 'Tab'/, 'Tab stays inside the list')
  assert.match(owners, /aria-haspopup="dialog" aria-expanded=\{open\}/)
  assert.match(owners, /Select all/)
  assert.match(owners, /Clear/)
  assert.match(owners, /OWNERS_MAX/)
  assert.match(owners, /ownersFull\(owners\)/, 'the screen reader hears every name')
  assert.match(hook, /useJobRequirements\(filters: ListFilters, people: readonly OwnerChoice\[\] = \[\]\)/)
})

test('a new field name is created from the picker, kept for the next save, and managed in its own dialog', () => {
  assert.match(form, /onCreate=\{onCreateOption \? \(typed\) => void createField\('requirement', row\.id, typed\)/)
  assert.match(form, /onCreate=\{onCreateOption \? \(typed\) => void createField\('particular', row\.id, typed\)/)
  assert.match(form, /\+ Create "\$\{typed\}"/)
  assert.match(form, /rememberNewFields: true/, 'Save asks the server to remember new names')
  assert.doesNotMatch(form, /Manage fields|Settings2|onManage/, 'the form has no Manage fields button')
  assert.match(form, /title="Job requirements"/, 'the dialog title is just Job requirements')
  assert.doesNotMatch(form, /fillToBeConfirmed|Fill empty|jrf-chip/, 'no fill-with-TBC button and no helper captions')
  assert.match(form, /<Input value=\{position\.vacancies\}/, 'vacancies is a plain text box, not a picker')
  assert.match(form, /createErrors\[id\]/, 'a failed create is shown on its row')
  assert.match(suggest, /onCreate\?: \(typed: string\) => void/)
  assert.match(suggest, /options\.some\(\(option\) => option\.trim\(\)\.toLowerCase\(\) === typed\.toLowerCase\(\)\)/, 'no Create choice for a name that exists')
  assert.doesNotMatch(page, /Manage fields|FieldManagerDialog/, 'there is no Manage fields screen')
  assert.match(api, /\/job-requirement-options/)
  assert.match(hook, /if \(page\.options\) setOptions\(page\.options\)/, 'the catalog comes with the list read, so a live refresh keeps it current')
  assert.match(hook, /if \(outgoing\.rememberNewFields\) rerun\.current = true/)
})
