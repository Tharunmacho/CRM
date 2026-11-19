import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
/** A source file without its comments, so a rule about rendered words is not tripped by a comment that explains what was removed. */
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const daily = await import(moduleUrl(await read('../src/features/daily-work/daily-work-model.ts')))
const select = await import(moduleUrl(await read('../src/features/daily-work/remark-select-model.ts')))
const templates = await import(moduleUrl(await read('../src/features/remark-templates/remark-templates-model.ts')))

const slotsUrl = moduleUrl(await read('../src/shared/model/document-slots.ts'))
const readReportUrl = moduleUrl(await read('../src/features/candidates/read-report.ts'))
const form = await import(moduleUrl((await read('../src/features/application-form/form-model.ts'))
  .replace("'@/shared/model/document-slots'", `'${slotsUrl}'`).replace("'@/features/candidates/read-report'", `'${readReportUrl}'`)))

const SEEDS = ['Visa in progress', 'Visa stamping in progress', 'Awaiting Gamca Medical', 'Offer Accepted', 'Offer Declined by Candidate', 'PCC Pending', 'Trial feedback pending']
const list = SEEDS.map((text, index) => ({ id: `t${index + 1}`, text, active: true, sortOrder: index + 1 }))

/* ------------------------------------------------------------------ columns */

test('the Tasks columns: S NO, DOC, Recruiter (supervisors), JOB, then client, candidate, stage, today, remarks, actions', () => {
  assert.deepEqual(daily.taskColumnsFor(true).map((c) => c.title),
    ['S NO', 'DOC', 'Recruiter', 'JOB', 'CLIENT / COUNTRY', 'CANDIDATE NAME', 'STAGE', 'TODAY', 'REMARKS', 'Actions'])
  assert.deepEqual(daily.taskColumnsFor(false).map((c) => c.title),
    ['S NO', 'DOC', 'JOB', 'CLIENT / COUNTRY', 'CANDIDATE NAME', 'STAGE', 'TODAY', 'REMARKS', 'Actions'])
  assert.deepEqual(daily.taskColumnsFor(true).map((c) => c.key), ['no', 'doc', 'staff', 'position', 'client', 'candidate', 'stage', 'today', 'remarks', 'actions'])
  assert.equal(daily.taskColumnsFor(true).at(-1).quiet, true, 'the actions header has no visible title')
})

test('the supervisor column plan fits the 1111px panel at a 1440px window', () => {
  const sum = (list) => list.reduce((total, c) => total + c.width, 0)
  assert.ok(sum(daily.taskColumnsFor(true)) <= 1111, `${sum(daily.taskColumnsFor(true))}px`)
  assert.ok(daily.taskColumnsFor(true).find((c) => c.key === 'actions').width >= 142, 'the Open / Complete button and the menu are not clipped')
  assert.ok(daily.taskColumnsFor(true).find((c) => c.key === 'remarks').width >= 160, 'the dropdown keeps room for a remark')
  assert.ok(daily.taskColumnsFor(true).find((c) => c.key === 'doc').width >= 80, '"19 Nov 2026" fits')
})

test('the Tasks table renders the model columns and no longer has a "Remark added" chip', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /taskColumnsFor\(supervisor\)/)
  assert.doesNotMatch(table, /Remark added|is-remark/)
  assert.match(table, /Nothing yet/)
  assert.match(table, /todayChips\(/)
  const order = [...table.matchAll(/data-label="([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(order, ['S NO', 'DOC', 'Recruiter', 'JOB', 'CLIENT / COUNTRY', 'CANDIDATE NAME', 'STAGE', 'TODAY', 'REMARKS', 'Actions'])
  assert.doesNotMatch(await read('../src/styles/daily-work-board.css'), /\.tw-chip\.is-remark/)
})

/* ------------------------------------------------------------------ today chips and DOC */

test('the TODAY chips are "Called 10:30" and "Stage moved 10:45"; a remark is never a chip', () => {
  const zone = 'Asia/Kolkata'
  const none = { today: { called: false, moved: false, remarked: false }, completedAt: null }
  assert.deepEqual(daily.todayChips(none, zone), [])
  assert.equal(daily.nothingYet(none.today), true)
  const called = { today: { called: true, moved: false, remarked: false }, completedAt: '2026-11-19T05:00:00Z' }
  assert.deepEqual(daily.todayChips(called, zone), [{ key: 'called', text: 'Called 10:30' }])
  const moved = { today: { called: false, moved: true, remarked: false, movedAt: '2026-11-19T05:15:00Z' }, completedAt: null }
  assert.deepEqual(daily.todayChips(moved, zone), [{ key: 'moved', text: 'Stage moved 10:45' }])
  const remarkedOnly = { today: { called: false, moved: false, remarked: true }, completedAt: null }
  assert.deepEqual(daily.todayChips(remarkedOnly, zone), [], 'no chip for a remark')
  assert.equal(daily.nothingYet(remarkedOnly.today), false, 'but the day is not "Nothing yet" either')
  for (const row of [none, called, moved, remarkedOnly, { today: { called: true, moved: true, remarked: true }, completedAt: '2026-11-19T05:00:00Z' }]) {
    for (const chip of daily.todayChips(row, zone)) assert.doesNotMatch(chip.text, /remark/i)
  }
})

test('the remark still counts towards the recruiter cards (only the chip is gone)', () => {
  const base = { kind: 'stage', status: 'open', waiting: false, completedAt: null, referral: { source: '', label: '', detail: null }, stage: 3 }
  const rows = daily.taskRows([{ ...base, id: 'a', candidateId: 'c1', ownerId: 'u1', remark: { body: 'PCC Pending', at: '2026-11-19T07:00:00Z', by: 'G', count: 1 } }], [], '2026-11-19', 'Asia/Kolkata')
  assert.equal(rows[0].today.remarked, true)
  assert.equal(daily.recruiterWork(rows).get('u1').remarks, 1)
})

test('DOC reads as "19 Nov 2026"; an empty or impossible date is blank', () => {
  assert.equal(daily.docLabel('2026-11-19'), '19 Nov 2026')
  assert.equal(daily.docLabel('2027-01-05'), '5 Jan 2027')
  assert.equal(daily.docLabel('2026-12-31T00:00:00Z'), '31 Dec 2026')
  for (const bad of [null, undefined, '', 'soon', '2026-02-30', '2026-13-01']) assert.equal(daily.docLabel(bad), '', String(bad))
})

test('the DOC cell is a link to the form when empty, and only where there is a placement to date', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  assert.match(table, /export function DocCell/)
  assert.match(table, /Add date/)
  assert.match(table, /!canAdd \|\| !item\.applicationId/)
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /applicationFormHref\(row\.candidateId, null, row\.applicationId\)\}#overview/)
})

/* ------------------------------------------------------------------ Excel */

test('the Excel export asks for the same list the screen shows and names the file by day and person', () => {
  assert.equal(daily.exportPath('all'), '/daily-work/export?format=xlsx&staff=all')
  assert.equal(daily.exportPath(undefined), '/daily-work/export?format=xlsx&staff=all')
  assert.equal(daily.exportPath('u 1'), '/daily-work/export?format=xlsx&staff=u%201')
  assert.equal(daily.exportFileName('2026-11-19'), 'tasks-2026-11-19.xlsx')
  assert.equal(daily.exportFileName('2026-11-19', ''), 'tasks-2026-11-19.xlsx')
  assert.equal(daily.exportFileName('2026-11-19', 'Asha  Devi (Chennai)'), 'tasks-asha-devi-chennai-2026-11-19.xlsx')
  assert.equal(daily.exportFileName('2026-11-19', 'José Ñandú'), 'tasks-jose-nandu-2026-11-19.xlsx')
})

test('the Export Excel button sits next to Refresh and reuses the shared blob download (the client tracker uses the same one)', async () => {
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.ok(screen.indexOf('Refresh') < screen.indexOf('Export Excel'), 'next to Refresh')
  assert.match(screen, /Preparing…/)
  assert.match(screen, /role="alert"/)
  assert.match(screen, /supervisor \? staffFilter : store\.me\.id/, 'supervisors: the current recruiter filter; recruiters: their own list')
  const exporter = await code('../src/features/daily-work/daily-work-export.ts')
  assert.match(exporter, /downloadApiFile\(exportPath\(staff\)/)
  const tracker = await code('../src/features/reports/client-tracker-api.ts')
  assert.match(tracker, /downloadApiFile\(trackerPath\(filters, format\)/)
  assert.doesNotMatch(tracker + exporter, /createObjectURL/, 'one download helper, not a copy')
  assert.match(await code('../src/shared/api/download.ts'), /credentials: 'include'/)
})

/* ------------------------------------------------------------------ the REMARKS dropdown */

test('the dropdown shows the current remark as its value, "Select remark" when there is none', () => {
  const none = select.remarkSelectView(null, list)
  assert.equal(none.value, '')
  assert.equal(none.options[0].label, 'Select remark')
  assert.deepEqual(none.options.slice(1).map((o) => o.label), SEEDS)
  const current = select.remarkSelectView('PCC Pending', list)
  assert.equal(current.value, 't6')
  assert.equal(current.custom, false)
  assert.equal(current.options.some((o) => o.label === 'Select remark'), false, 'the prompt goes once a remark is chosen')
  assert.deepEqual(current.options.map((o) => o.label), SEEDS)
})

test('a template remark is recognised whatever its capitals and spacing', () => {
  assert.equal(select.remarkSelectView('  visa   IN progress ', list).value, 't1')
  assert.equal(select.remarkSelectView('visa in progress', list).custom, false)
})

test('a free-text remark that is not a template stays selected, listed first and marked custom', () => {
  const view = select.remarkSelectView('Called, will join next week', list)
  assert.equal(view.value, select.CUSTOM_VALUE)
  assert.equal(view.custom, true)
  assert.deepEqual(view.options[0], { value: select.CUSTOM_VALUE, label: 'Called, will join next week' })
  assert.equal(view.options.length, SEEDS.length + 1)
  // Choosing the custom entry again, or the prompt, saves nothing.
  assert.equal(select.textForChoice(select.CUSTOM_VALUE, list), null)
  assert.equal(select.textForChoice('', list), null)
})

test('while the templates are still loading nothing is called custom, and with none the remark still shows', () => {
  assert.equal(select.remarkSelectView('PCC Pending', [], false).custom, false)
  assert.equal(select.remarkSelectView('PCC Pending', [], true).custom, true)
  assert.equal(select.remarkSelectView('PCC Pending', [], false).value, select.CUSTOM_VALUE)
})

test('choosing a template saves its text; the status line says Saving, Saved, the error, or Custom remark', () => {
  assert.equal(select.textForChoice('t4', list), 'Offer Accepted')
  assert.equal(select.textForChoice('nope', list), null)
  assert.deepEqual(select.remarkStatus('saving', false), { text: 'Saving…', tone: 'busy' })
  assert.deepEqual(select.remarkStatus('saved', true), { text: 'Saved', tone: 'ok' })
  assert.deepEqual(select.remarkStatus('error', false, 'Offline'), { text: 'Offline', tone: 'error' })
  assert.equal(select.remarkStatus('error', false).text, 'The remark could not be saved.')
  assert.deepEqual(select.remarkStatus('idle', true), { text: 'Custom remark', tone: 'custom' })
  assert.deepEqual(select.remarkStatus('idle', false, '', 'Last update 3 days ago'), { text: 'Last update 3 days ago', tone: 'lead' })
  assert.deepEqual(select.remarkStatus('idle', false), { text: '', tone: 'none' })
})

test('only an admin is offered "+ Add a template", as the last row', () => {
  assert.equal(select.canAddTemplate('admin'), true)
  for (const role of ['manager', 'recruiter', 'telemarketer', '', null, undefined]) assert.equal(select.canAddTemplate(role), false, String(role))
  const admin = select.remarkSelectView('PCC Pending', list, true, true)
  assert.deepEqual(admin.options.at(-1), { value: select.ADD_VALUE, label: '+ Add a template' })
  assert.equal(select.isAddChoice(select.ADD_VALUE), true)
  assert.equal(select.textForChoice(select.ADD_VALUE, list), null, 'the add row is never saved as a remark')
  const recruiter = select.remarkSelectView('PCC Pending', list, true, false)
  assert.equal(recruiter.options.some((o) => o.value === select.ADD_VALUE), false)
  // With no remark and a custom remark the add row is still last.
  assert.equal(select.remarkSelectView(null, list, true, true).options.at(-1).value, select.ADD_VALUE)
  assert.equal(select.remarkSelectView('Typed long ago', list, true, true).options.at(-1).value, select.ADD_VALUE)
  assert.equal(select.remarkSelectView(null, [], true, true).options.at(-1).value, select.ADD_VALUE, 'an empty list can still be started')
})

test('the add row: empty, too long and duplicate are calm sentences; a server duplicate is selected, not an error', () => {
  assert.deepEqual(templates.newTemplateCheck('   ', list), { ok: false, message: 'Write the remark first.' })
  const long = templates.newTemplateCheck('x'.repeat(templates.TEMPLATE_MAX + 1), list)
  assert.equal(long.ok, false)
  assert.match(long.message, /under 120 characters/)
  assert.equal(templates.newTemplateCheck('x'.repeat(templates.TEMPLATE_MAX), list).ok, true)
  assert.deepEqual(templates.newTemplateCheck('  pcc   PENDING ', list), { ok: false, message: 'That remark is already in the list.' })
  assert.deepEqual(templates.newTemplateCheck('  Visa  cleared  ', list), { ok: true, text: 'Visa cleared' })
  // POST answers the created item, or the existing one (200) when the text exists: either way that text is what gets selected.
  assert.equal(templates.createdText({ id: 't9', text: 'Visa cleared', active: true, sortOrder: 8 }, 'visa cleared'), 'Visa cleared')
  assert.equal(templates.createdText({ item: { id: 't6', text: 'PCC Pending' } }, 'pcc pending'), 'PCC Pending', 'the existing one comes back with its own capitals')
  assert.equal(templates.createdText({}, '  typed   text '), 'typed text')
  assert.equal(templates.createdText(null, 'typed'), 'typed')
})

test('the Remarks cell has no text box except the admin add row, saves through the old remark call, and is labelled for the candidate', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  const cell = table.slice(table.indexOf('export function RemarkCell'), table.indexOf('export function DocCell'))
  assert.doesNotMatch(cell, /Textarea|<textarea|Add remark|remark-save|placeholder="Add remark"/)
  assert.match(cell, /aria-label=\{`Remark for \$\{item\.candidate\}`\}/)
  assert.match(cell, /await onSave\(text\)/)
  assert.match(cell, /aria-live="polite"/)
  // the one input is the admin's add-template box
  assert.equal((cell.match(/<input/g) ?? []).length, 1)
  assert.match(cell, /adding \? \(/)
  assert.match(cell, /canAddTemplate\(useStore\(\)\.me\.role\)/)
  assert.match(cell, /event\.key === 'Escape'/)
  assert.match(cell, /createRemarkTemplate\(check\.text\)/)
  assert.match(cell, /maxLength=\{TEMPLATE_MAX\}/)
  // the page keeps the call the old box made
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /store\.addRemark\(row\.taskId, text\)/)
  assert.match(screen, /onRemark=/)
})

test('no layout shift and 44px targets on phones: the status line keeps its height and the trigger grows to 44px', async () => {
  const css = await read('../src/styles/daily-work.css')
  assert.match(css, /\.dw-remark-status \{[^}]*min-height: 15px/)
  assert.match(css, /\.dw \.dw-remark \.custom-select-trigger \{[^}]*height: 36px/)
  assert.match(css, /@media \(max-width: 640px\), \(pointer: coarse\) \{[\s\S]*\.dw \.dw-remark \.custom-select-trigger \{ height: 44px/)
  assert.match(css, /\.dw \.dw-remark-add input \{[^}]*height: 36px/)
  assert.match(css, /\.dw \.dw-remark-add input \{ height: 44px/)
})

/* ------------------------------------------------------------------ template list model (admin card) */

test('templates are read from {items:[...]} in the server order; junk rows are dropped', () => {
  const read = templates.normalizeTemplates({ items: [
    { id: 'b', text: ' Offer  Accepted ', active: true, sortOrder: 2 },
    { id: 'a', text: 'Visa in progress', active: true, sortOrder: 1 },
    { id: 'c', text: 'PCC Pending', active: false, sortOrder: 3 },
    { id: '', text: 'no id' }, { id: 'd', text: '   ' }, null, 'text',
  ] })
  assert.deepEqual(read.map((t) => t.id), ['a', 'b', 'c'])
  assert.equal(read[1].text, 'Offer Accepted')
  assert.deepEqual(templates.activeTemplates(read).map((t) => t.id), ['a', 'b'])
  assert.deepEqual(templates.normalizeTemplates(undefined), [])
  assert.deepEqual(templates.normalizeTemplates([{ id: 1, text: 'x', sortOrder: 'bad' }]), [{ id: '1', text: 'x', active: true, sortOrder: 0 }])
})

test('admin validation: empty, too long, duplicate (a rename may keep its own text)', () => {
  assert.equal(templates.templateProblem('', list), 'Write the remark first.')
  assert.match(templates.templateProblem('y'.repeat(121), list), /\(it is 121\)/)
  assert.equal(templates.templateProblem('offer accepted', list), 'That remark is already in the list.')
  assert.equal(templates.templateProblem('Offer Accepted', list, 't4'), '', 'renaming a template to its own text is fine')
  assert.equal(templates.templateProblem('Offer Accepted!', list, 't4'), '')
  assert.equal(templates.templateProblem('Offer Accepted', list, 't5'), 'That remark is already in the list.')
  assert.equal(templates.sameText(' A  b ', 'a B'), true)
})

test('moving a template up or down renumbers the list and sends only what changed; the ends do nothing', () => {
  assert.deepEqual(templates.moveTemplate(list, 't1', -1), [])
  assert.deepEqual(templates.moveTemplate(list, 't7', 1), [])
  assert.deepEqual(templates.moveTemplate(list, 'missing', 1), [])
  assert.deepEqual(templates.moveTemplate(list, 't3', -1), [{ id: 't3', sortOrder: 2 }, { id: 't2', sortOrder: 3 }])
  // The server may hold equal numbers: the whole list is numbered 1..n so a swap is never lost.
  const tied = list.map((t) => ({ ...t, sortOrder: 0 }))
  const changes = templates.moveTemplate(tied, 't3', 1) // equal numbers sort by name: t3 (Awaiting...) is first
  assert.equal(changes.length, 7, 'every row gets its number')
  const after = templates.applyOrder(tied, changes)
  assert.deepEqual(after.map((t) => t.id).slice(0, 3), ['t4', 't3', 't5'])
})

test('the admin card is for admins only, under Agency settings, with add, rename, switch and move', async () => {
  const card = await code('../src/screens/users/remark-templates-card.tsx')
  assert.match(card, /if \(!isAdmin\) return null/)
  assert.match(card, /store\.me\.role === 'admin'/)
  for (const word of ['Remark templates', 'Add', 'Rename', 'is offered', 'Move ', 'templateProblem', 'moveTemplate']) assert.ok(card.includes(word), word)
  assert.match(await code('../src/screens/users/agency-settings.tsx'), /<RemarkTemplatesCard editable=\{editable\} \/>/)
  const api = await code('../src/features/remark-templates/remark-templates-api.ts')
  assert.match(api, /'\/remark-templates\?all=1'/)
  assert.match(api, /api\.post<unknown>\('\/remark-templates', \{ text \}\)/)
  assert.match(api, /api\.patch<unknown>\(`\/remark-templates\//)
  assert.match(api, /api\.del<unknown>\(`\/remark-templates\//)
})

/* ------------------------------------------------------------------ the form's DOC field */

test('the date of commencement is an optional form field with the path application.commencementDate', () => {
  assert.equal(form.COMMENCEMENT_PATH, 'application.commencementDate')
  const def = form.EXTRA_FIELDS.find((f) => f.path === form.COMMENCEMENT_PATH)
  assert.equal(def.kind, 'date')
  assert.equal(def.label, 'Date of commencement (DOC)')
  assert.equal(def.required, false)
  assert.equal(def.counted, false)
})

const payload = (commencementDate) => ({
  candidate: { id: 'c1', canEdit: true },
  application: { id: 'a1', orderId: 'o1', status: 'active', currentStage: 5, commencementDate },
  personal: {}, passport: {}, education: [], experience: { history: [] }, tracking: { summary: {}, rows: [] },
})

test('the date comes from application.commencementDate and is saved with the form save (ISO, or null to clear)', () => {
  const loaded = form.normalizeForm(payload('2026-11-19T00:00:00Z'))
  assert.equal(loaded.extras[form.COMMENCEMENT_PATH], '2026-11-19')
  assert.equal(form.normalizeForm(payload(null)).extras[form.COMMENCEMENT_PATH], '')
  const base = form.toDraft(form.normalizeForm(payload(null)))
  const typed = { ...base, extras: { ...base.extras, [form.COMMENCEMENT_PATH]: '2026-12-01' } }
  assert.deepEqual(form.changedPaths(base, typed), ['application.commencementDate'])
  assert.deepEqual(form.buildSavePayload(base, typed), { application: { commencementDate: '2026-12-01' } })
  assert.equal(form.isDirty(base, typed), true)
  const saved = form.toDraft(form.normalizeForm(payload('2026-12-01')))
  const cleared = { ...saved, extras: { ...saved.extras, [form.COMMENCEMENT_PATH]: '' } }
  assert.deepEqual(form.buildSavePayload(saved, cleared), { application: { commencementDate: null } })
  assert.deepEqual(form.buildSavePayload(saved, saved), {})
})

test('the date is never in "Still missing" and a draft that only has it is complete as before', () => {
  const draft = form.toDraft(form.normalizeForm(payload(null)))
  assert.equal(form.completenessOf(draft).missing.some((m) => /commencement/i.test(m.key + m.label)), false)
})

test('a typed date survives a live refresh of the form (rebase keeps what was typed)', () => {
  const oldBase = form.toDraft(form.normalizeForm(payload(null)))
  const typed = { ...oldBase, extras: { ...oldBase.extras, [form.COMMENCEMENT_PATH]: '2026-12-01' } }
  const newBase = form.toDraft(form.normalizeForm(payload(null)))
  assert.equal(form.rebaseDraft(oldBase, typed, newBase).extras[form.COMMENCEMENT_PATH], '2026-12-01')
})

test('the Overview strip shows the field next to Stage, with the same date picker; without a placement it is a one-line hint', async () => {
  const field = await code('../src/features/application-form/components/commencement-field.tsx')
  assert.match(field, /import \{ DatePicker \} from '@\/shared\/ui\/date-picker'/)
  assert.match(field, /Choose a job first/)
  assert.match(field, /canWork\s*\n?\s*\? <DatePicker/)
  assert.match(field, /Date of commencement \(DOC\)/)
  assert.match(field, /!ctx\.form\.application/)
  const screen = await code('../src/screens/candidates/application-form.tsx')
  assert.match(screen, /<StageControl [^>]*\/><CommencementField ctx=\{ctx\.fill\} canWork=\{canWork\} \/>/)
  // the passport dates use the very same component
  assert.match(await code('../src/features/application-form/fill/fill-controls.tsx'), /import \{ DatePicker \} from '@\/shared\/ui\/date-picker'/)
})
