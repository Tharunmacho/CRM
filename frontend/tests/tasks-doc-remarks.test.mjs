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

test('the Tasks columns: S NO, DOC, CLIENT, JOB, CANDIDATE, Recruiter (supervisors), AGENCY, STAGE, TODAY, REMARKS, NOTES, actions', () => {
  assert.deepEqual(daily.taskColumnsFor(true).map((c) => c.title),
    ['S NO', 'D O C', 'CLIENT', 'JOB TITLE', 'CANDIDATE', 'RECRUITER', 'AGENCY', 'STAGE', 'TODAY', 'REMARKS', 'NOTES', 'Actions'])
  assert.deepEqual(daily.taskColumnsFor(false).map((c) => c.title),
    ['S NO', 'D O C', 'CLIENT', 'JOB TITLE', 'CANDIDATE', 'AGENCY', 'STAGE', 'TODAY', 'REMARKS', 'NOTES', 'Actions'])
  assert.deepEqual(daily.taskColumnsFor(true).map((c) => c.key), ['no', 'doc', 'client', 'position', 'candidate', 'staff', 'agency', 'stage', 'today', 'remarks', 'notes', 'actions'])
  assert.equal(daily.taskColumnsFor(true).at(-1).quiet, true, 'the actions header has no visible title')
  for (const c of daily.taskColumnsFor(true)) assert.doesNotMatch(c.title, /\//, 'one short word per header, never "CLIENT / COUNTRY"')
})

test('the column plan is within 1% of the 1111px panel of a 1440px window and gives every column its measured longest word', () => {
  const sum = (list) => list.reduce((total, c) => total + c.width, 0)
  const without = (supervisor) => daily.taskColumnsFor(supervisor, false)
  const narrowest = daily.taskColumnsFor(true, false, 'none', false)   // TODAY and the Recruiter both folded: the table of a 1440px window
  assert.ok(sum(narrowest) <= 1111, `${sum(narrowest)}px of the 1111px panel`)   // REMARKS (140) and NOTES (184) are wide enough to wrap at spaces only
  assert.ok(sum(without(false)) <= 1111, 'the plan without the Recruiter column fits the 1111px panel too')
  assert.ok(sum(without(true)) === daily.taskPlanWidth(true, false) && daily.taskColumnsFor(true, false).some((c) => c.key === 'notes'), 'NOTES is in the narrow plan too')
  const width = (key) => daily.taskColumnsFor(true).find((c) => c.key === key).width
  // The longest words were measured in Poppins at the table's sizes (the font file's advance widths, the same numbers as canvas measureText):
  // a column is its word plus 13px (6px padding on both sides and the 1px column line).
  assert.ok(width('client') >= 99, '"International" is 86px at 13px / 600 and the country line is smaller')
  assert.ok(width('candidate') >= 134, '"EMAYAVARAMBAN" is 121px at 13px / 600')
  assert.ok(width('position') >= 91, '"SUPERVISOR" is 78px at 13px / 500')
  assert.ok(width('agency') >= 99, '"LAVOROTECH" is 86px at 13px / 500')
  assert.ok(width('staff') >= 87, '"Srivathsala" is 74px at 13px / 500 (the RECRUITER header is 65px)')
  assert.ok(width('no') >= 44, 'the "S NO" header is 29px at 11.5px / 600 with its letter spacing, with 8px on each side')
  assert.ok(width('actions') >= 118, 'the 72px Open / Complete button, the 4px gap and the 36px menu (112px) fit the cell: 118px less its 2px padding on each side and the 1px line is 113px')
  assert.ok(width('remarks') >= 140, 'a remark wraps at its spaces: the longest word ("attestation", ~65px) fits next to the chevron')
  assert.ok(width('doc') >= 84, 'the typed dd/mm/yyyy date (62px at 11px) fits: 84px less 13px of cell padding and line, less the input padding')
  assert.ok(width('stage') >= 106, 'the stage chip keeps room for "Prescreening" (77px + 16px chip padding), the outcome chip for "Travelled" and the TODAY chip under it')
  assert.ok(width('today') >= 104, 'the TODAY chip keeps room for "Moved 10:45" on one line (75px + 16px chip padding)')
  for (const c of daily.taskColumnsFor(true)) assert.equal('left' in c, false, `${c.key}: every column is centred, none is "left"`)
})

test('NOTES is a column of every table; TODAY is a column only when the panel holds the whole plan (otherwise its chip is in STAGE)', () => {
  const notes = daily.TASK_LIST_COLUMNS.find((c) => c.key === 'notes')
  assert.equal(notes.roomy, undefined, 'NOTES is never dropped')
  assert.ok(notes.width >= 184, 'a note wraps at its spaces in its own cell')
  assert.deepEqual(daily.taskColumnsFor(true, false).map((c) => c.key), ['no', 'doc', 'client', 'position', 'candidate', 'staff', 'agency', 'stage', 'remarks', 'notes', 'actions'])
  assert.deepEqual(daily.taskColumnsFor(false, false).map((c) => c.key), ['no', 'doc', 'client', 'position', 'candidate', 'agency', 'stage', 'remarks', 'notes', 'actions'])
  for (const supervisor of [true, false]) {
    const plan = daily.taskPlanWidth(supervisor, true)
    assert.equal(daily.todayPlace(supervisor, Math.max(plan, 1100)), 'column')   // (below 1100px it is a card with a TODAY row, whatever the plan)
    assert.equal(daily.todayPlace(supervisor, plan - 1), 'stage')
    assert.equal(daily.todayPlace(supervisor, 1111), 'stage', 'a 1440px window has no room for TODAY: its chip is in the stage cell')
    assert.equal(daily.todayPlace(supervisor, 1590), 'column', 'a 1920px window has')
    assert.equal(daily.todayPlace(supervisor, 0), 'stage', 'before the first measurement')
    assert.equal(daily.todayPlace(supervisor, 1099), 'row', 'a phone card has a TODAY row of its own')
    assert.equal(daily.todayPlace(supervisor, 1100), 'stage', 'the narrowest table')
    assert.equal(plan, daily.taskPlanWidth(supervisor, false) + daily.TASK_LIST_COLUMNS.find((c) => c.key === 'today').width)
  }
})

test('the Tasks table renders the model columns and no longer has a "Remark added" chip', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.match(table, /taskColumnsFor\(supervisor, place !== 'stage', closedRowsOf\(rows\), staff !== 'candidate'\)/)
  assert.doesNotMatch(table, /Remark added|is-remark/)
  assert.match(table, /Nothing yet/)
  assert.match(table, /todayChips\(/)
  const order = [...table.matchAll(/data-label="([^"]+)"/g)].map((m) => m[1])
  assert.deepEqual(order, ['S NO', 'D O C', 'CLIENT', 'JOB TITLE', 'CANDIDATE NAME', 'RECRUITER', 'AGENCY', 'STAGE', 'TODAY', 'REMARKS', 'NOTES', 'Actions'])   // (the Recruiter cell is left out, not renamed, when it folds under the candidate)
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

test('the DOC cell is a typed dd/mm/yyyy box, and only where there is a placement to date', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  assert.match(table, /export function DocCell/)
  assert.match(table, /<DocInput/)
  assert.match(table, /canAdd && item\.applicationId/)
  const screen = await code('../src/screens/tasks/daily-work.tsx')
  assert.match(screen, /saveApplicationForm\(row\.candidateId, \{ applicationId: row\.applicationId \}, \{ application: \{ commencementDate: iso \} \}\)/)
  const input = await code('../src/screens/tasks/doc-input.tsx')
  assert.match(input, /placeholder="dd\/mm\/yyyy"/)
  assert.match(input, /event\.key === 'Enter'/)
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
  assert.ok(screen.indexOf('Refresh') < screen.indexOf('exportLabel(view)'), 'next to Refresh')
  assert.equal(daily.exportLabel('current'), 'Export Excel')
  assert.match(screen, /Preparing…/)
  assert.match(screen, /role="alert"/)
  assert.match(screen, /supervisor \? staffFilter : store\.me\.id/, 'supervisors: the current recruiter filter; recruiters: their own list')
  const exporter = await code('../src/features/daily-work/daily-work-export.ts')
  assert.match(exporter, /downloadApiFile\(exportPath\(staff, view\)/)
  const tracker = await code('../src/features/reports/client-tracker-api.ts')
  assert.match(tracker, /downloadApiFile\(trackerPath\(filters, format\)/)
  assert.doesNotMatch(tracker + exporter, /createObjectURL/, 'one download helper, not a copy')
  assert.match(await code('../src/shared/api/download.ts'), /credentials: 'include'/)
})

/* ------------------------------------------------------------------ the REMARKS dropdown */

test('the dropdown shows the current remark as its value, "Select remark" when there is none, and "Select remark" is ALWAYS the first entry', () => {
  const none = select.remarkSelectView(null, list)
  assert.equal(none.value, '')
  assert.equal(none.options[0].label, 'Select remark')
  assert.deepEqual(none.options.slice(1).map((o) => o.label), [...SEEDS, 'Others'])
  const current = select.remarkSelectView('PCC Pending', list)
  assert.equal(current.value, 't6')
  assert.equal(current.custom, false)
  // The owner's rule (replaces "the prompt goes once a remark is chosen"): the prompt stays on top, so a person can go back to no remark.
  assert.equal(current.options[0].label, 'Select remark', 'the prompt stays once a remark is chosen')
  assert.equal(current.options[0].value, select.CLEAR_VALUE)
  assert.deepEqual(current.options.map((o) => o.label), ['Select remark', ...SEEDS, 'Others'])
  assert.notEqual(current.value, current.options[0].value, 'no check on the prompt while a remark is chosen')
  assert.equal(none.value, none.options[0].value, 'the check is on the prompt only while nothing is chosen')
  for (const [remark, canAdd] of [[null, false], ['PCC Pending', false], ['Typed long ago', true], [null, true], ['pcc pending', true]]) {
    for (const loaded of [true, false]) {
      const options = select.remarkSelectView(remark, list, loaded, canAdd).options
      assert.deepEqual(options[0], { value: '', label: 'Select remark', muted: true }, `${remark} ${loaded}: first, muted`)
      assert.equal(options.filter((o) => o.label === 'Select remark').length, 1)
    }
  }
  assert.equal(select.remarkSelectView(null, [], true, false).options[0].label, 'Select remark', 'even with no templates')
})

test('choosing "Select remark" clears the remark; it saves no text and does nothing when there is no remark', () => {
  assert.equal(select.isClearChoice(''), true)
  assert.equal(select.isClearChoice('t1'), false)
  assert.equal(select.isClearChoice(select.CUSTOM_VALUE), false)
  assert.equal(select.textForChoice(select.CLEAR_VALUE, list), null, 'the prompt is never saved as a remark')
})

test('a template remark is recognised whatever its capitals and spacing', () => {
  assert.equal(select.remarkSelectView('  visa   IN progress ', list).value, 't1')
  assert.equal(select.remarkSelectView('visa in progress', list).custom, false)
})

test('a free-text remark that is not a template stays selected as the value and listed first (it is never labelled custom on screen)', () => {
  const view = select.remarkSelectView('Called, will join next week', list)
  assert.equal(view.value, select.CUSTOM_VALUE)
  assert.equal(view.options[0].label, 'Select remark', 'the prompt is still the first entry')
  assert.deepEqual(view.options[1], { value: select.CUSTOM_VALUE, label: 'Called, will join next week' }, 'the selected remark is right under it')
  assert.deepEqual(view.options.slice(2, 4).map((o) => o.label), SEEDS.slice(0, 2), 'the templates follow')
  assert.equal(view.options.length, SEEDS.length + 3, 'the prompt, the remark, the templates and Others')
  // Choosing the custom entry again, or the prompt, saves nothing.
  assert.equal(select.textForChoice(select.CUSTOM_VALUE, list), null)
  assert.equal(select.textForChoice('', list), null)
})

test('while the templates are still loading nothing is called custom, and with none the remark still shows', () => {
  assert.equal(select.remarkSelectView('PCC Pending', [], false).custom, false)
  assert.equal(select.remarkSelectView('PCC Pending', [], true).custom, true)
  assert.equal(select.remarkSelectView('PCC Pending', [], false).value, select.CUSTOM_VALUE)
})

test('choosing a template saves its text; the status says Saving, Saved or the error and NEVER "Custom remark"', () => {
  assert.equal(select.textForChoice('t4', list), 'Offer Accepted')
  assert.equal(select.textForChoice('nope', list), null)
  assert.deepEqual(select.remarkStatus('saving'), { text: 'Saving…', tone: 'busy' })
  assert.deepEqual(select.remarkStatus('saved'), { text: 'Saved', tone: 'ok' })
  assert.deepEqual(select.remarkStatus('error', 'Offline'), { text: 'Offline', tone: 'error' })
  assert.equal(select.remarkStatus('error').text, 'The remark could not be saved.')
  assert.deepEqual(select.remarkStatus('idle', '', 'Last update 3 days ago'), { text: 'Last update 3 days ago', tone: 'lead' })
  assert.deepEqual(select.remarkStatus('idle'), { text: '', tone: 'none' })
  for (const state of ['idle', 'saving', 'saved', 'error']) assert.doesNotMatch(select.remarkStatus(state).text, /custom/i)
})

test('"Others": the last template-like entry, before "+ Add a template"; the typed text is trimmed and an empty box cancels', () => {
  assert.equal(select.OTHERS_LABEL, 'Others')
  assert.equal(select.OTHERS_MAX, 500)
  for (const [remark, canAdd] of [[null, false], ['PCC Pending', false], ['Typed long ago', false], [null, true], ['Typed long ago', true]]) {
    const options = select.remarkSelectView(remark, list, true, canAdd).options
    const labels = options.map((o) => o.label)
    assert.equal(labels.includes('Others'), true, String(remark))
    assert.equal(labels.at(-1), canAdd ? '+ Add a template' : 'Others')
    assert.equal(labels.indexOf('Others'), labels.length - (canAdd ? 2 : 1), 'Others sits right before the admin add row')
  }
  assert.equal(select.isOthersChoice(select.OTHERS_VALUE), true)
  assert.equal(select.isOthersChoice('t1'), false)
  assert.equal(select.textForChoice(select.OTHERS_VALUE, list), null, 'choosing Others saves nothing; the typed text does')
  assert.equal(select.othersText('   '), null)
  assert.equal(select.othersText(''), null)
  assert.equal(select.othersText('  Will  join   next week '), 'Will join next week')
  assert.equal(select.othersText('x'.repeat(600)).length, 500)
})

test('only an admin or MD is offered "+ Add a template", as the last row', () => {
  assert.equal(select.canAddTemplate('admin'), true)
  assert.equal(select.canAddTemplate('manager'), true)
  for (const role of ['staff', 'recruiter', 'telemarketer', '', null, undefined]) assert.equal(select.canAddTemplate(role), false, String(role))
  const admin = select.remarkSelectView('PCC Pending', list, true, true)
  assert.deepEqual(admin.options.at(-1), { value: select.ADD_VALUE, label: '+ Add a template' })
  assert.deepEqual(admin.options.at(-2), { value: select.OTHERS_VALUE, label: 'Others' })
  assert.equal(select.isAddChoice(select.ADD_VALUE), true)
  assert.equal(select.textForChoice(select.ADD_VALUE, list), null, 'the add row is never saved as a remark')
  const recruiter = select.remarkSelectView('PCC Pending', list, true, false)
  assert.equal(recruiter.options.some((o) => o.value === select.ADD_VALUE), false)
  // With no remark and a custom remark the add row is still last.
  assert.equal(select.remarkSelectView(null, list, true, true).options.at(-1).value, select.ADD_VALUE)
  assert.equal(select.remarkSelectView('Typed long ago', list, true, true).options.at(-1).value, select.ADD_VALUE)
  assert.equal(select.remarkSelectView(null, [], true, true).options.at(-1).value, select.ADD_VALUE, 'an empty list can still be started')
  assert.equal(select.remarkSelectView(null, [], true, false).options.at(-1).value, select.OTHERS_VALUE, 'a recruiter can always type through Others')
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

test('the Remarks cell types only through Others (and the admin add row), saves through the old remark call, and is labelled for the candidate', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  const cell = table.slice(table.indexOf('export function RemarkCell'), table.indexOf('export function DocCell'))
  assert.doesNotMatch(cell, /Textarea|<textarea|Add remark|remark-save|placeholder="Add remark"/)
  assert.match(cell, /aria-label=\{`Remark for \$\{item\.candidate\}`\}/)
  assert.match(cell, /await onSave\(text\)/)
  // "Select remark" (always the first entry) clears the cell through onClear, only while a remark is shown; the option is muted
  assert.match(cell, /onClear\?: \(\) => Promise<void>/)
  assert.match(cell, /if \(isClearChoice\(value\)\) \{ if \(last\) await clearRemark\(\); return \}/)
  assert.match(cell, /await onClear\(\)/)
  assert.match(cell, /data-muted=\{option\.muted \? '' : undefined\}/)
  assert.match(cell, /aria-live="polite"/)
  // the one input serves Others (everyone) and the admin's add-template row
  assert.equal((cell.match(/<input/g) ?? []).length, 1)
  assert.match(cell, /typing \? \(/)
  assert.match(cell, /canAddTemplate\(useStore\(\)\.me\.role\)/)
  assert.match(cell, /event\.key === 'Escape'/)
  assert.match(cell, /createRemarkTemplate\(check\.text\)/)
  assert.match(cell, /maxLength=\{mode === 'others' \? OTHERS_MAX : TEMPLATE_MAX\}/)
  assert.match(cell, /isOthersChoice\(value\)/)
  assert.match(cell, /othersText\(typed\)/)
  assert.match(cell, /type="submit"[^>]*aria-label=\{mode === 'others' \? 'Save the remark'/, 'the small check button saves')
  assert.match(cell, /autoFocus/)
  assert.match(cell, /save\(text, TYPED_VALUE\)/, 'the saved text is shown as the select value while it saves')
  assert.doesNotMatch(cell, /Custom remark|is-custom|tone: 'custom'/, 'a saved remark is never labelled custom')
  assert.doesNotMatch(cell, /disabled=\{state === 'saving' \|\| \(empty/, 'Others means the select is never empty or disabled for lack of templates')
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
  assert.match(card, /isAdminLevel\(store\.me\.role\)/)
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

/* ------------------------------------------------------------------ CLIENT, AGENCY and the professional pass */

test('the CLIENT cell is the client name with the country name under it in a smaller muted line: plain text, no flag, nothing when there is no country', async () => {
  const table = await code('../src/screens/tasks/daily-table.tsx')
  const cell = table.slice(table.indexOf('export function ClientCell'), table.indexOf('export function AgencyCell'))
  assert.match(cell, /dw-cell-strong dw-clamp/)
  assert.match(cell, /clientCountry\(/)
  assert.match(cell, /dw-client-country/)
  assert.match(cell, /if \(!line\.text\) return name/, 'no country, nothing under the name')
  assert.doesNotMatch(cell, /flag|<img/i)
  assert.doesNotMatch(table, /CountryChip|ClientCountryCell|flagSrc|dw-ctry/)
  const tasks = await code('../src/screens/tasks/task-table.tsx')
  assert.match(tasks, /<ClientCell item=\{row\} country \/>/)
  assert.doesNotMatch(tasks, /CLIENT \/ COUNTRY|<img|flagSrc|\bFlag\b/)
  for (const css of ['daily-work.css', 'tasks-table.css']) assert.doesNotMatch(await read(`../src/styles/${css}`), /\.dw-ctry|\.dw-cc\b/)
  const css = await read('../src/styles/tasks-table.css')
  assert.match(css, /\.dw-client-country \{[^}]*font-size: 11\.5px[^}]*color: var\(--text-muted\)/)
  assert.match(css, /\.dw-client \{[^}]*flex-direction: column[^}]*align-items: center/)
  // the line is the order's (or the preferred) country by name; a city is not a country
  assert.deepEqual(daily.clientCountry({ country: 'Saudi Arabia', countrySource: 'order', locationHint: null }), { text: 'Saudi Arabia', hint: "The order's country" })
  assert.equal(daily.clientCountry({ country: '  United   Arab Emirates ', countrySource: 'order', locationHint: null }).text, 'United Arab Emirates')
  assert.equal(daily.clientCountry({ country: 'UAE, Qatar', countrySource: 'preference', locationHint: null }).text, 'UAE, Qatar')
  assert.equal(daily.clientCountry({ country: 'Kuwait', countrySource: null, locationHint: null }).text, 'Kuwait', 'an older server sends no source')
  for (const none of [{ country: null, countrySource: null, locationHint: null }, { country: '  ', countrySource: 'order', locationHint: null }, { country: null, countrySource: null, locationHint: 'Chennai, India' }]) {
    assert.deepEqual(daily.clientCountry(none), { text: '', hint: '' })
  }
  // the recruiter cards carry no flags either
  assert.doesNotMatch(await code('../src/screens/tasks/daily-staff-board.tsx'), /flag|<img/i)
})

test('the AGENCY column shows the recorded agency and uses only explicit referrals on older servers', () => {
  const row = (source, label, detail = null, extra = {}) => ({ referral: { source, label, detail }, ...extra })
  // the server's agency (the tracker's Ref) wins, also for a candidate who arrived by email and later got an agency
  assert.equal(daily.agencyLabel(row('email', 'Email', null, { agency: ' Lotus Overseas ' })), 'Lotus Overseas')
  assert.equal(daily.agencyLabel(row('referral', 'Referral', 'Old detail', { agency: 'Sigma Agency' })), 'Sigma Agency')
  // the server says there is none: a dash, not "Email" / "WhatsApp"
  assert.equal(daily.agencyLabel(row('whatsapp', 'WhatsApp', null, { agency: null })), '')
  assert.equal(daily.agencyLabel(row('referral', 'Referral', 'Sunrise', { agency: '' })), '')
  // An older server has only the referrer and intake channel.
  assert.equal(daily.agencyLabel(row('referral', 'Referral', ' Sunrise Overseas ')), 'Sunrise Overseas')
  assert.equal(daily.agencyLabel(row('referral', 'Referral')), '')
  assert.equal(daily.agencyLabel(row('whatsapp', 'WhatsApp')), '')
  assert.equal(daily.agencyLabel(row('other', 'Other', 'Current candidate documents import 2026-11-12')), '')
  assert.equal(daily.agencyLabel(row('', '')), '')
})

test('the professional pass: one 60px row, a 44px one-line header, one padding for header and cells, a line under every row', async () => {
  const css = await read('../src/styles/tasks-table.css')
  const rule = (selector) => {
    const at = css.indexOf(`${selector} {`)
    assert.ok(at >= 0, `${selector} rule exists`)
    return css.slice(at, css.indexOf('}', at))
  }
  const P = ':root .dw .tw-table.dw-table.dw-main'
  assert.match(rule(`${P} tbody td`), /height: 60px/, 'at least 60px; a row grows when a name or client wraps')
  assert.match(rule(`${P} tbody td`), /vertical-align: middle/)
  assert.match(rule(`${P} tbody td`), /border: 0; border-bottom: var\(--table-line-width\) solid var\(--table-line\)/, 'a line under every row and nothing else')
  assert.match(css, /tbody td:not\(:last-child\) \{ border-right: var\(--table-line-width\) solid var\(--table-column-line\); \}/, 'a LIGHT column line in the body (fainter than the row line)')
  assert.doesNotMatch(css, /tr:last-child > td \{ border-bottom: 0/, 'the last row has its line too')
  assert.match(rule(`${P} thead th`), /height: 44px/)
  assert.match(rule(`${P} thead th`), /font-size: 11\.5px; font-weight: 600; letter-spacing: \.04em/)
  assert.match(rule(`${P} thead th`), /white-space: nowrap/)
  assert.match(rule(`${P} thead th`), /text-align: center/, 'the headings are centred over their columns')
  assert.match(rule(`${P} tbody td`), /text-align: center/, 'and so are the cells')
  assert.doesNotMatch(rule(`${P} thead th`), /text-align: left/)
  assert.match(rule(`${P} thead th`), /padding: 0 var\(--tw-pad-r\) 0 var\(--tw-pad-l\)/)
  assert.match(rule(`${P} thead th`), /border-bottom: var\(--table-frame-width\) solid var\(--table-line-strong\)/, 'the header edge is the strong line')
  assert.match(rule(`${P} tbody td`), /padding: 8px var\(--tw-pad-r\) 8px var\(--tw-pad-l\)/, 'the same left edge as the header')
  assert.match(css, /\.tw-table\.dw-table\.dw-main \{[^}]*width: 100%; min-width: 0; border-collapse: separate/, 'the table fills its panel and never scrolls sideways')
  // controls
  // (the remark trigger is no longer a fixed 36px box: it grows with the remark, so the whole text can be read; 36px is only its minimum)
  assert.match(css, /\.dw \.tw-table \.dw-remark \.custom-select-trigger \{ box-sizing: border-box; width: 100%; height: auto !important; min-height: 36px !important/)
  assert.match(css, /\.dw-act \{[^}]*width: 72px[^}]*height: 36px/)
  assert.match(css, /\.dw-more-btn \{ width: 36px; height: 36px/)
  assert.match(css, /\.dw-doc-add \{[^}]*height: 32px[^}]*text-decoration: none/)
  assert.match(css, /\.tw-chip \{[^}]*width: max-content; max-width: 100%; min-height: 28px[^}]*white-space: normal/, 'activity chips are one line (as wide as their text) and wrap inside the Today column rather than being clipped when it is narrower')
  assert.match(css, /\.dw-stage-chip \{[^}]*min-height: 28px/)
  // the status never adds a line: a badge on the control, quiet states only for screen readers
  assert.match(css, /\.dw-remark-status \{ position: absolute/)
  assert.match(css, /\.dw-remark-status:is\(\.is-lead, \.is-none\) \{[^}]*clip: rect/)
  assert.doesNotMatch(css, /is-custom/)
  // the metrics only apply to the table (a panel of 1100px or more); under that a candidate is a card with 44px targets and 16px inputs
  assert.match(css, /@container dw \(min-width: 1100px\)/)
  const cards = css.slice(css.indexOf('@container dw (max-width: 1099px)'))
  assert.match(cards, /\.dw-more-btn \{ width: 44px; height: 44px/)
  assert.match(cards, /\.dw-remark \.custom-select-trigger \{ height: auto !important; min-height: 44px !important[^}]*font-size: 16px/)
  assert.match(cards, /\.dw-remark-add input \{[^}]*height: 44px[^}]*font-size: 16px/)
  // tokens only
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ''), /#[0-9a-fA-F]{3,8}\b|rgb\(/)
  // no zebra on the Tasks table
  assert.doesNotMatch(await code('../src/screens/tasks/task-table.tsx'), /is-alt/)
})

test('the TaskTable renders the Agency cell and moves the Recruiter cell after the candidate', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.ok(table.indexOf('<AgencyCell') > table.indexOf('<OwnerTag'), 'Agency follows Recruiter')
  assert.ok(table.indexOf('<OwnerTag') > table.indexOf('<RecordIdentity'), 'Recruiter follows the candidate')
  assert.ok(table.indexOf('<ClientCell') < table.indexOf('<PositionCellView') && table.indexOf('<DocCell') < table.indexOf('<ClientCell'), 'DOC, CLIENT, JOB')
})

test('there is no DOT column on the Tasks list: not in the model, not in the table, not in the cards', async () => {
  assert.equal(daily.TASK_LIST_COLUMNS.find((c) => c.key === 'dot'), undefined)
  assert.ok(!daily.taskColumnsFor(true).some((c) => c.title === 'DOT'))
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.doesNotMatch(table, /data-label="DOT"|<DotCell/)
  assert.equal(daily.docLabel('2026-07-09'), '9 Jul 2026')
})
