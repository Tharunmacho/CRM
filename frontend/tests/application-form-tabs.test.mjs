import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

const tabs = await import(moduleUrl(await src('features/application-form/form-tabs.ts')))
const slotsUrl = moduleUrl(await src('shared/model/document-slots.ts'))
const rows = await import(moduleUrl((await src('features/application-form/document-rows.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`)))
const slotsModule = await import(slotsUrl)

/* ------------------------------------------------------------------ The section navigator */

test('the page has the eight sections in the owner’s order, each with its old element id kept', () => {
  assert.deepEqual(tabs.FORM_TABS.map((tab) => tab.id), ['overview', 'personal', 'passport', 'education', 'experience', 'documents', 'invoice', 'notes'])
  assert.deepEqual(tabs.FORM_TABS.map((tab) => tab.label), ['Overview', 'Personal', 'Passport', 'Education', 'Experience', 'Documents', 'Invoice', 'Notes'])
  assert.deepEqual(tabs.FORM_TABS.map((tab) => tab.anchor), ['af-assignment', 'af-fill-section-1', 'af-fill-section-2', 'af-fill-section-3', 'af-fill-section-4', 'af-documents', 'af-invoice', 'af-notes'])
  assert.equal(tabs.DEFAULT_TAB, 'personal', 'the first screen is the personal and passport details')
})

test('invoice and notes are tabs only for the people who may see them', () => {
  assert.deepEqual(tabs.availableTabs({ invoice: true, notes: true }), ['overview', 'personal', 'passport', 'education', 'experience', 'documents', 'invoice', 'notes'])
  assert.deepEqual(tabs.availableTabs({ invoice: false, notes: true }), ['overview', 'personal', 'passport', 'education', 'experience', 'documents', 'notes'])
  assert.deepEqual(tabs.availableTabs({ invoice: false, notes: false }), ['overview', 'personal', 'passport', 'education', 'experience', 'documents'])
})

test('deep links open their section: the tab name, the old element id, and a few friendly names', () => {
  assert.equal(tabs.tabFromHash('#documents'), 'documents')
  assert.equal(tabs.tabFromHash('documents'), 'documents')
  assert.equal(tabs.tabFromHash('#af-documents'), 'documents')
  assert.equal(tabs.tabFromHash('#AF-FILL-SECTION-2'), 'passport')
  assert.equal(tabs.tabFromHash('#af-fill-section-3'), 'education')
  assert.equal(tabs.tabFromHash('#af-notes'), 'notes')
  assert.equal(tabs.tabFromHash('#af-process'), 'overview')
  assert.equal(tabs.tabFromHash('#assignment'), 'overview')
  assert.equal(tabs.tabFromHash('#nothing-here'), null)
  assert.equal(tabs.tabFromHash(''), null)
  assert.equal(tabs.tabFromHash(undefined), null)
  assert.equal(tabs.tabFromHash('#%E0%A4%A'), null, 'a malformed link is not a crash')
  for (const tab of tabs.FORM_TABS) assert.equal(tabs.tabFromHash(tabs.hashForTab(tab.id)), tab.id)
})

test('the paper form’s numbered sections land on the same tabs', () => {
  assert.deepEqual([1, 2, 3, 4, 5, 6, 7].map(tabs.tabForSection), ['personal', 'passport', 'education', 'experience', 'documents', 'overview', 'overview'])
  assert.equal(tabs.tabForSection(99), 'personal')
})

test('a "Still missing" item jumps to the section that holds its field or document', () => {
  const item = (key, section = 1, kind = 'field') => ({ key, section, kind, label: key, message: '' })
  assert.equal(tabs.tabForMissing(item('personal.fatherName')), 'personal')
  assert.equal(tabs.tabForMissing(item('passport.expiryDate', 2)), 'passport')
  assert.equal(tabs.tabForMissing(item('education', 3)), 'education')
  assert.equal(tabs.tabForMissing(item('experience.history', 4)), 'experience')
  assert.equal(tabs.tabForMissing(item('experience.totalExperience', 4)), 'experience')
  assert.equal(tabs.tabForMissing(item('documents.passport', 5, 'document')), 'documents')
  assert.equal(tabs.tabForMissing(item('something.new', 6)), 'overview', 'an unknown key falls back to its section number')
  const counts = tabs.missingByTab([item('personal.fatherName'), item('personal.maritalStatus'), item('passport.passportNumber', 2), item('documents.passport', 5, 'document')])
  assert.deepEqual(counts, { personal: 2, passport: 1, documents: 1 })
})

test('a wide screen shows Personal with Passport and Education with Experience; a narrow one shows only the chosen section', () => {
  assert.deepEqual(tabs.visibleCards('personal', true), [1, 2])
  assert.deepEqual(tabs.visibleCards('passport', true), [1, 2])
  assert.deepEqual(tabs.visibleCards('education', true), [3, 4])
  assert.deepEqual(tabs.visibleCards('experience', true), [3, 4])
  assert.deepEqual(['personal', 'passport', 'education', 'experience'].map((tab) => tabs.visibleCards(tab, false)), [[1], [2], [3], [4]])
  for (const tab of ['overview', 'documents', 'invoice', 'notes']) {
    assert.deepEqual(tabs.visibleCards(tab, true), [], tab)
    assert.equal(tabs.isDetailTab(tab), false)
  }
  assert.equal(tabs.paneIdFor('passport'), 'af-pane-details')
  assert.equal(tabs.paneIdFor('documents'), 'af-pane-documents')
})

test('the arrow keys, Home and End move between tabs and wrap round', () => {
  const list = ['overview', 'personal', 'passport', 'documents']
  assert.equal(tabs.nextTab(list, 'personal', 'ArrowRight'), 'passport')
  assert.equal(tabs.nextTab(list, 'documents', 'ArrowRight'), 'overview')
  assert.equal(tabs.nextTab(list, 'overview', 'ArrowLeft'), 'documents')
  assert.equal(tabs.nextTab(list, 'passport', 'Home'), 'overview')
  assert.equal(tabs.nextTab(list, 'passport', 'End'), 'documents')
  assert.equal(tabs.nextTab(list, 'passport', 'Enter'), null)
  assert.equal(tabs.nextTab([], 'passport', 'ArrowRight'), null)
})

test('a tab’s spoken name carries its count', () => {
  assert.equal(tabs.tabAccessibleName('Documents', null), 'Documents')
  assert.equal(tabs.tabAccessibleName('Documents', { count: 3, kind: 'missing' }), 'Documents, 3 missing')
  assert.equal(tabs.tabAccessibleName('Notes', { count: 1, kind: 'notes' }), 'Notes, 1 note')
  assert.equal(tabs.tabAccessibleName('Notes', { count: 4, kind: 'notes' }), 'Notes, 4 notes')
  assert.equal(tabs.tabAccessibleName('Notes', { count: 0, kind: 'overdue' }), 'Notes, a note is overdue')
  assert.equal(tabs.tabAccessibleName('Personal', { count: 0, kind: 'missing' }), 'Personal')
})

/* ------------------------------------------------------------------ The documents grid */

const form = (extra = []) => {
  const documentOrder = [...slotsModule.DOCUMENT_SLOT_KEYS.filter((key) => key !== 'application_form'), ...extra]
  const documents = Object.fromEntries(documentOrder.map((key) => [key, { slot: key, label: slotsModule.documentLabel(key), uploaded: key === 'resume', required: false, requiredFromStage: null, multiple: false, files: [], current: null }]))
  return { documents, documentOrder }
}

test('documents are grouped Identity, Process, Certificates, Other, each in the owner’s order, and none is lost', () => {
  const states = rows.documentRowStates(form(), true)
  const groups = rows.groupDocumentRows(states)
  assert.deepEqual(groups.map((group) => group.label), ['Identity', 'Process', 'Certificates', 'Other'])
  assert.deepEqual(groups[0].rows.map((row) => row.slot.slot), ['resume', 'passport', 'aadhaar', 'photo'])
  assert.deepEqual(groups[1].rows.map((row) => row.slot.slot), ['medical', 'signed_offer', 'pcc', 'visa', 'visa_stamping', 'air_ticket', 'undertaking_letter', 'emigration', 'insurance', 'invoice'])
  assert.deepEqual(groups[2].rows.map((row) => row.slot.slot), ['education_certificate', 'experience_certificate', 'google_review'])
  assert.deepEqual(groups[3].rows.map((row) => row.slot.slot), ['other'])
  assert.equal(groups.flatMap((group) => group.rows).length, states.length)
})

test('a document no group names goes to Other instead of disappearing, and empty groups are left out', () => {
  const states = rows.documentRowStates(form(['brand_new_kind']), true)
  const groups = rows.groupDocumentRows(states)
  assert.deepEqual(groups[3].rows.map((row) => row.slot.slot), ['other', 'brand_new_kind'])
  assert.deepEqual(rows.groupDocumentRows([]), [])
  const onlyOther = rows.groupDocumentRows(states.filter((row) => row.slot.slot === 'other'))
  assert.deepEqual(onlyOther.map((group) => group.key), ['other'])
})

test('the total stays "N of M documents" and each group shows its own share', () => {
  const states = rows.documentRowStates(form(), true)
  assert.equal(rows.documentsCountText(rows.documentRowsProgress(states.map((row) => row.state))), '1 of 11 documents')
  const groups = rows.groupDocumentRows(states)
  assert.equal(rows.groupProgressText(groups[0].rows), '1 of 2', 'the resume is in, the passport is not; Aadhaar and photo are optional')
  assert.equal(rows.groupProgressText(groups[1].rows), '0 of 9')
  assert.equal(rows.groupProgressText(groups[2].rows), '', 'a group of optional papers says nothing')
  assert.equal(rows.groupProgressText(groups[3].rows), '')
})

/* ------------------------------------------------------------------ The wiring, read from the source (the screen needs React) */

test('every panel stays mounted and hidden, so typing and uploads survive moving between sections', async () => {
  const screen = await src('screens/candidates/application-form.tsx')
  for (const id of ['af-pane-overview', 'af-pane-details', 'af-pane-documents', 'af-pane-invoice', 'af-pane-notes']) assert.ok(screen.includes(`id="${id}"`), id)
  assert.equal([...screen.matchAll(/role="tabpanel"/g)].length, 5)
  assert.equal([...screen.matchAll(/hidden=\{/g)].length >= 5, true)
  assert.match(screen, /<FormTabs tabs=\{tabs\}/)
  assert.match(screen, /cards=\{shownCards\}/)
  assert.match(screen, /tabFromHash\(window\.location\.hash\) \?\? DEFAULT_TAB/, 'a deep link opens its section')
  assert.match(screen, /addEventListener\('hashchange'/)
  assert.match(screen, /window\.history\.replaceState\(window\.history\.state/, 'the address follows the tab without a navigation or a page jump')
  // The details are still reached by "Fill in" and "Upload": the section is shown first, then the field is focused.
  assert.match(screen, /selectTab\(tabForMissing\(item\)/)
  assert.match(screen, /row\.querySelector<HTMLInputElement>\('input\[type="file"\]'\)\?\.click\(\)\s*\n?\s*selectTab\('documents'/, 'the file picker opens inside the click, before the section is shown')
  // Everything that was on the page is still on it.
  for (const piece of ['<AssignmentBar', '<RecruitmentStages', '<StageModal', '<FillView', '<DocumentsSection', '<RecordDownloads', '<InvoiceCard', '<NotesPanel', '<StageClockBar', '<SidePanel', '<FormSheet', 'Unsaved changes', 'Preview form', 'Print or save as PDF']) {
    assert.ok(screen.includes(piece), piece)
  }
})

test('the tab bar is one tab stop with roving focus, and each tab names the panel it controls', async () => {
  const bar = await src('features/application-form/components/form-tabs.tsx')
  assert.match(bar, /role="tablist"/)
  assert.match(bar, /role="tab"/)
  assert.match(bar, /tabIndex=\{selected \? 0 : -1\}/)
  assert.match(bar, /aria-selected=\{selected\}/)
  assert.match(bar, /aria-controls=\{paneIdFor\(tab\.id\)\}/)
  assert.match(bar, /onKeyDown=\{onKeyDown\}/)
})

test('the detail cards stay mounted when another section is chosen', async () => {
  const view = await src('features/application-form/fill/fill-view.tsx')
  const card = await src('features/application-form/fill/fill-card.tsx')
  assert.match(view, /hidden=\{!visible\(card\.no\)\}/)
  assert.match(card, /hidden=\{hidden\}/)
  assert.doesNotMatch(view, /\.filter\(\(card\) => visible/, 'cards are hidden, never unmounted')
})

test('education and experience carry column titles for the table layout and keep a label on every box', async () => {
  const rowsSource = await src('features/application-form/fill/fill-rows.tsx')
  assert.match(rowsSource, /className="aff-table-head" aria-hidden="true"/)
  assert.match(rowsSource, /<label htmlFor=\{id\}>\{column\.label\}<\/label>/, 'the label stays for the screen reader; the table hides it visually')
  assert.match(rowsSource, /data-editable=/)
  assert.match(rowsSource, /Add \{|copy\.add/)
})

test('the side panel is only the completeness card and the missing list: the tab bar is the section navigator', async () => {
  const side = await src('features/application-form/components/side-panel.tsx')
  assert.doesNotMatch(side, /Form sections|onJump|af-nav/)
  assert.match(side, /Still missing/)
  assert.match(side, /aria-expanded=\{listOpen\}/)
})

test('the tab stylesheet is loaded last by the screen and sizes the cards by their own width', async () => {
  const screen = await src('screens/candidates/application-form.tsx')
  const css = await src('styles/application-form-tabs.css')
  const imports = [...screen.matchAll(/import '@\/styles\/([\w-]+)\.css'/g)].map((match) => match[1])
  assert.equal(imports.at(-1), 'application-form-tabs')
  assert.match(css, /@container affc \(min-width: 560px\)/)
  assert.match(css, /@container affc \(min-width: 700px\)/)
  assert.match(css, /\.af-tabs \{ position: sticky; top: 0/)
  assert.match(css, /\.af-pane\[hidden\]/, 'the hidden attribute still hides a flex or grid panel')
  assert.doesNotMatch(css, /overflow-x: ?(scroll|auto)[^}]*\.af-scroll/, 'the page itself never scrolls sideways')
})

/* ------------------------------------------------------------------ The design rules, read from the stylesheet */

test('every spacing value in the tab stylesheet is on the 4 / 8 / 12 / 16 / 20 / 24 / 32 scale', async () => {
  const css = (await src('styles/application-form-tabs.css')).replace(/\/\*[\s\S]*?\*\//g, '')
  const allowed = new Set([0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64])
  const bad = []
  for (const match of css.matchAll(/(?:^|[;{\s])((?:padding|margin|gap|row-gap|column-gap)(?:-(?:top|right|bottom|left|block|inline))?)\s*:\s*([^;}]+)/g)) {
    for (const px of match[2].matchAll(/(-?\d+(?:\.\d+)?)px/g)) if (!allowed.has(Math.abs(Number(px[1])))) bad.push(`${match[1]}: ${match[2].trim()}`)
  }
  assert.deepEqual(bad, [])
})

test('the design rules hold: 48px tab bar, 40px controls with an 8px radius, 28px section icon, 24px chips, 20px count pills, 36px buttons', async () => {
  const css = await src('styles/application-form-tabs.css')
  assert.match(css, /\.af \.af-tablist \{[^}]*height: 48px/)
  assert.match(css, /\.af \.af-tab \{[^}]*height: 48px/)
  assert.match(css, /\.af-tab\[aria-selected='true'\]::after \{ background: var\(--primary\)/, 'the underline is the primary colour')
  assert.match(css, /\.af-tab-count \{[^}]*min-width: 20px; height: 20px/)
  assert.match(css, /\.af-main :is\(\.ui-input:not\(textarea\), \.custom-select-trigger, \.date-picker-trigger\) \{[^}]*height: 40px/)
  assert.match(css, /border: 1px solid var\(--border-control\); border-radius: 8px/)
  assert.match(css, /\.aff-tile \{ width: 28px; height: 28px/)
  assert.match(css, /\.aff \.aff-grid \{ gap: 16px 12px; \}/, '16px between rows, 12px between columns')
  assert.match(css, /\.aff-lab, \.af \.aff \.ac-lab \{ height: 20px/, 'every label row is the same height, so controls share a top edge')
  assert.match(css, /\.ui-button\.min-h-10 \{ min-height: 36px/)
  assert.match(css, /\.af-docgroup \.af-docrow \{[^}]*min-height: 88px/)
  assert.doesNotMatch(css.replace(/\/\*[\s\S]*?\*\//g, ''), /#[0-9a-fA-F]{3,8}\b/, 'tokens only, no colour of its own')
})

test('the context strip reads client, job, recruiter, stage and DOC as equal cells, and the tab bar fades at the edges it can scroll past', async () => {
  const strip = await src('features/application-form/components/context-strip.tsx')
  assert.match(strip, /data-ctx="doc"/)
  assert.match(strip, /commencement\?: string/)
  const bar = await src('features/application-form/components/form-tabs.tsx')
  assert.match(bar, /data-fade-start=\{edges\.start\}/)
  assert.match(bar, /data-fade-end=\{edges\.end\}/)
  const css = await src('styles/application-form-tabs.css')
  assert.match(css, /scroll-snap-type: x proximity/)
  assert.match(css, /\.af-tablist-wrap\[data-fade-start='true'\]::before/)
})

test('on a phone the document groups fold (the first one with something to collect is open) and every target is 44px', async () => {
  const docs = await src('features/application-form/components/documents-section.tsx')
  assert.match(docs, /PHONE_QUERY = '\(max-width: 760px\)'/)
  assert.match(docs, /aria-expanded=\{isOpen\(group\.key\)\}/)
  assert.match(docs, /hidden=\{!isOpen\(group\.key\)\}/)
  const css = await src('styles/application-form-tabs.css')
  const phone = css.slice(css.indexOf('@media (max-width: 760px)'))
  for (const rule of [/\.af-docbtn \{ height: 44px/, /\.af-bar-back \{[^}]*height: 44px/, /\.af-missing button \{ height: 44px/, /\.af-context-change \{ width: 100%; height: 44px/, /font-size: 16px/]) assert.match(phone, rule)
})
