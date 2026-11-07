import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

const slotsUrl = moduleUrl(await src('shared/model/document-slots.ts'))
const modelUrl = moduleUrl((await src('features/application-form/form-model.ts')).replace("'@/shared/model/document-slots'", `'${slotsUrl}'`))
const expiryUrl = moduleUrl(await src('features/candidates/passport-expiry.ts'))
const fillUrl = moduleUrl((await src('features/application-form/fill/fill-fields.ts'))
  .replace("'../form-model'", `'${modelUrl}'`)
  .replace("'@/features/candidates/passport-expiry'", `'${expiryUrl}'`))
const model = await import(modelUrl)
const fill = await import(fillUrl)

/**
 * The fields of CareIndia_Application_Form_Order_Wise.docx that a person fills, in its order, by the form's own keys:
 * 1 Personal details, 2 Passport details, 3 Educational details, 4 Previous experience. Sections 5 (documents),
 * 6 (client and recruitment) and 7 (tracking) are the shell's.
 */
const DOCX = {
  personal: ['candidateName', 'fatherName', 'dateOfBirth', 'maritalStatus', 'gender', 'nationality', 'languagesKnown', 'mobileNumber', 'currentAddress'].map((key) => `personal.${key}`),
  passport: ['passportNumber', 'placeOfIssue', 'issueDate', 'expiryDate'].map((key) => `passport.${key}`),
  experience: ['totalExperience', 'experienceIndia', 'experienceAbroad'].map((key) => `experience.${key}`),
}

const draft = (patch = {}) => ({
  personal: { candidateName: 'Asha Rao', fatherName: 'K Rao', dateOfBirth: '1990-05-17', maritalStatus: 'married', gender: 'female', nationality: 'Indian', languagesKnown: ['English'], mobileNumber: '+91 98765 43210', currentAddress: 'Chennai', whatsapp: '', email: '' },
  passport: { passportNumber: 'M1234567', placeOfIssue: 'Chennai', issueDate: '2020-01-01', expiryDate: '2030-01-01' },
  education: [{ rowId: 'e1', level: 'B.Tech', passedYear: '2012', institute: 'ABC College, Chennai' }],
  experience: { totalExperience: '6', experienceIndia: '4', experienceAbroad: '2', history: [{ rowId: 'h1', company: 'XYZ', designation: 'Welder', country: 'India', period: '2018 to 2020' }] },
  extras: {},
  ...patch,
})
const flat = (cards) => cards.flatMap((card) => card.fields)
const item = (key) => fill.ALL_FILL_FIELDS.find((entry) => entry.key === key)

/* ------------------------------------------------------------------ Only the docx fields, none missed */

test('the view has exactly the fields of the docx, in its order: personal, passport, experience', () => {
  const cards = fill.cardsFor()
  assert.deepEqual(cards[0].fields.map((entry) => entry.key), DOCX.personal)
  assert.deepEqual(fill.PASSPORT_FILL_FIELDS.map((entry) => entry.key), DOCX.passport)
  assert.deepEqual(cards[1].fields.map((entry) => entry.key), DOCX.passport)
  assert.deepEqual(cards[3].fields.map((entry) => entry.key), DOCX.experience)
  assert.deepEqual(fill.FILL_KEYS, [...DOCX.personal, ...DOCX.passport, ...DOCX.experience])
  assert.equal(new Set(fill.FILL_KEYS).size, fill.FILL_KEYS.length, 'a field appears twice')
})

test('every docx field is a field of the form model, and the model fields the docx lacks are not shown', () => {
  const modelKeys = new Set([...model.PERSONAL_FIELDS, ...model.PASSPORT_FIELDS, ...model.EXPERIENCE_FIELDS].map((def) => def.path))
  for (const key of [...DOCX.personal, ...DOCX.passport, ...DOCX.experience]) assert.ok(modelKeys.has(key), `${key} is a form field`)
  const shown = new Set(fill.FILL_KEYS)
  assert.deepEqual([...modelKeys].filter((key) => !shown.has(key)).sort(), ['personal.email', 'personal.whatsapp'], 'only the model fields the docx does not have are left out')
  for (const def of model.EXTRA_FIELDS) assert.ok(!shown.has(def.path), `${def.path} is not on the docx`)
})

test('the assignment, documents, tracking and screening fields are not here', () => {
  assert.ok(fill.FILL_KEYS.every((key) => !/^(recruitment|assignment|screening|documents|tracking)\./.test(key)))
  const titles = fill.cardsFor().map((card) => card.title)
  assert.ok(!titles.some((title) => /document|contact|assessment|follow|client|tracking/i.test(title)))
})

test('every item the completeness check can report has a home', () => {
  const cards = fill.cardsFor()
  const documents = { resume: { uploaded: false, required: true, label: 'Resume' }, passport: { uploaded: false, required: true, label: 'Passport' }, photo: { uploaded: false, required: true, label: 'Photo' } }
  const empty = draft({ personal: { ...draft().personal, candidateName: '', mobileNumber: '', languagesKnown: [] }, passport: { passportNumber: '', placeOfIssue: '', issueDate: '', expiryDate: '' }, education: [], experience: { totalExperience: '', experienceIndia: '', experienceAbroad: '', history: [] } })
  const { missing } = model.completenessOf(empty, documents)
  assert.ok(missing.length > 10)
  for (const entry of missing) {
    const owners = cards.filter((card) => fill.cardMissing(card, [entry]).length === 1)
    if (entry.key.startsWith('documents.')) {
      assert.equal(owners.length, 0, `${entry.key} is counted by the Documents block`)
    } else {
      assert.equal(owners.length, 1, `${entry.key} belongs to exactly one section (got ${owners.map((card) => card.title)})`)
    }
  }
})

/* ------------------------------------------------------------------ Sections */

test('sections carry the paper form\'s own numbers and titles', () => {
  assert.deepEqual(fill.cardsFor().map((card) => [card.no, card.title]), [
    [1, 'Personal details'],
    [2, 'Passport details'],
    [3, 'Educational details'],
    [4, 'Previous experience'],
  ])
  assert.deepEqual(fill.cardsFor().map((card) => card.kind), ['fields', 'passport', 'education', 'experience'])
})

test('the passport section is four equal fields on the shared grid', () => {
  assert.deepEqual(fill.cardsFor()[1].fields.map((entry) => entry.span), [3, 3, 3, 3])
  assert.deepEqual(fill.PASSPORT_FILL_FIELDS.map((entry) => entry.label), ['Passport number', 'Place of issue', 'Date of issue', 'Date of expiry'])
})

test('Personal details: three equal fields beside the photo, then languages and mobile number, then the address in full', () => {
  const personal = fill.cardsFor()[0]
  assert.equal(personal.lane, 9, 'the photo tile takes three of the twelve columns')
  const span = (key) => personal.fields.find((entry) => entry.key === key).span
  assert.deepEqual(['candidateName', 'fatherName', 'dateOfBirth', 'maritalStatus', 'gender', 'nationality'].map((key) => span(`personal.${key}`)), [3, 3, 3, 3, 3, 3])
  assert.equal(span('personal.languagesKnown') + span('personal.mobileNumber'), 9, 'languages and mobile number fill the third row')
  assert.ok(span('personal.mobileNumber') >= 4, 'the country code and the number fit side by side')
  assert.equal(span('personal.currentAddress'), 12, 'the address has the full width under the photo')
  assert.equal(item('personal.mobileNumber').control, 'phone')
  assert.equal(item('personal.mobileNumber').hint, undefined, 'no helper text under the phone field')
})

/* ------------------------------------------------------------------ Layout */

const f = (span, key = 'k') => ({ key, label: key, control: 'text', span, spanMd: span >= 9 ? 12 : 6, required: false, extra: false })

test('layout: every row adds up to the lane, so no row ends half empty', () => {
  for (const card of fill.cardsFor()) {
    if (!card.fields.length) continue
    let used = 0
    for (const entry of card.fields) {
      if (entry.span > card.lane) { assert.equal(used, 0, `${entry.key} starts a row of its own`); assert.equal(entry.span, 12); continue }
      used += entry.span
      assert.ok(used <= card.lane, `${card.title}: ${entry.key} overflows its row`)
      if (used === card.lane) used = 0
    }
    assert.equal(used, 0, `${card.title} ends with a half empty row`)
    let half = 0
    for (const entry of card.fields) {
      half += entry.spanMd === 12 ? 12 : 6
      if (half === 12) half = 0
    }
    assert.equal(half, 0, `${card.title} ends with a half empty tablet row`)
  }
})

test('layout: widens a short row equally, and a lone field takes the whole row', () => {
  assert.deepEqual(fill.layoutFields([f(3), f(3), f(3), f(3), f(3), f(3)]).map((entry) => entry.span), [3, 3, 3, 3, 6, 6])
  assert.deepEqual(fill.layoutFields([f(3), f(3), f(12)]).map((entry) => entry.span), [6, 6, 12])
  assert.deepEqual(fill.layoutFields([f(4), f(4), f(4), f(6)]).map((entry) => entry.span), [4, 4, 4, 12])
  assert.deepEqual(fill.layoutFields([f(6), f(3), f(3)]).map((entry) => entry.span), [6, 3, 3])
  assert.deepEqual(fill.layoutFields([f(3), f(3), f(3)]).map((entry) => entry.span), [4, 4, 4])
  assert.deepEqual(fill.layoutFields([f(3), f(3), f(3), f(5), f(4), f(12)], 9).map((entry) => entry.span), [3, 3, 3, 5, 4, 12])
  const input = [f(3)]
  fill.layoutFields(input)
  assert.equal(input[0].span, 3, 'the input is not changed')
})

test('layout: the tablet shows two across and widens an unpaired field', () => {
  assert.deepEqual(fill.layoutFields([f(4), f(4), f(4)]).map((entry) => entry.spanMd), [6, 6, 12])
})

/* ------------------------------------------------------------------ Wording */

test('labels are whole words in proper English, with no abbreviations', () => {
  const abbreviations = /\b(dob|no\.|nos|tel|ph|addr|pref|prefs|yrs|yr|exp|qual|dept|esp|info|num|mob|alt|cont|org|desig|edu|nat)\b/i
  const labels = [...fill.ALL_FILL_FIELDS.map((entry) => entry.label), ...fill.EDUCATION_COLUMNS_FILL.map((c) => c.label), ...fill.EXPERIENCE_COLUMNS_FILL.map((c) => c.label), ...fill.cardsFor().map((card) => card.title)]
  for (const label of labels) {
    assert.ok(/^[A-Z]/.test(label), `${label} starts with a capital`)
    assert.ok(!abbreviations.test(label), `${label} has an abbreviation`)
    assert.ok(!/[:.]$/.test(label) && !/\s{2}/.test(label) && label === label.trim(), `${label} is tidy`)
  }
  for (const wanted of ['Candidate name', "Father's name", 'Date of birth', 'Marital status', 'Gender', 'Nationality', 'Languages known', 'Mobile number', 'Current address', 'Passport number', 'Place of issue', 'Date of issue', 'Date of expiry', 'Total experience', 'Experience in India', 'Experience abroad']) {
    assert.ok(labels.includes(wanted), `${wanted} is a label`)
  }
  assert.deepEqual(fill.EDUCATION_COLUMNS_FILL.map((c) => c.label), ['Level of education or course', 'Year passed', 'Name of institution', 'Address of institution'])
  assert.deepEqual(fill.EXPERIENCE_COLUMNS_FILL.map((c) => c.label), ['Name of company', 'Designation', 'Country', 'Period of employment'])
})

test('placeholders show an example, never an instruction in capitals or an ellipsis', () => {
  for (const entry of fill.ALL_FILL_FIELDS) if (entry.placeholder) assert.ok(!/\.\.\.|…/.test(entry.placeholder) && !/^[A-Z ]+$/.test(entry.placeholder), entry.placeholder)
  assert.equal(item('personal.candidateName').placeholder, 'As written in the passport')
})

test('required fields carry the asterisk the form model says', () => {
  const required = fill.ALL_FILL_FIELDS.filter((entry) => entry.required).map((entry) => entry.key).sort()
  const expected = [...model.PERSONAL_FIELDS, ...model.PASSPORT_FIELDS, ...model.EXPERIENCE_FIELDS].filter((def) => def.required && fill.FILL_KEYS.includes(def.path)).map((def) => def.path).sort()
  assert.deepEqual(required, expected)
})

test('hints are one short line or left out', () => {
  for (const card of fill.cardsFor()) {
    assert.ok(card.hint.length <= 70, `${card.title}: hint is short`)
    assert.ok(!card.hint.toLowerCase().startsWith(card.title.toLowerCase()), `${card.title}: hint does not repeat the title`)
  }
})

/* ------------------------------------------------------------------ Row columns */

test('education and work history columns use the keys of the paper form', () => {
  assert.deepEqual(fill.EDUCATION_COLUMNS_FILL.map((c) => c.key), fill.SHEET_EDUCATION_KEYS)
  assert.deepEqual(fill.EXPERIENCE_COLUMNS_FILL.map((c) => c.key), fill.SHEET_EXPERIENCE_KEYS)
  assert.equal(fill.EDUCATION_COLUMNS_FILL.at(-1).span, 12, 'the address of the institution spans the row')
  for (const columns of [fill.EDUCATION_COLUMNS_FILL, fill.EXPERIENCE_COLUMNS_FILL]) {
    let used = 0
    for (const column of columns) { used += column.span; if (used === 12) used = 0; assert.ok(used < 12) }
    assert.equal(used, 0)
  }
  assert.deepEqual(fill.EDUCATION_COLUMNS_FILL.map(fill.columnSpanMd), [6, 6, 12, 12])
  assert.deepEqual(fill.EXPERIENCE_COLUMNS_FILL.map(fill.columnSpanMd), [6, 6, 6, 6])
})

test('the institution is shown as a name and an address and joined back into one text', () => {
  const row = { rowId: 'r', level: 'B.Tech', passedYear: '2012', institute: 'ABC College, Chennai' }
  assert.deepEqual(fill.educationView(row), { rowId: 'r', level: 'B.Tech', passedYear: '2012', institutionName: 'ABC College', institutionAddress: 'Chennai' })
  assert.equal(fill.changeEducationRow(row, 'institutionAddress', 'Madurai').institute, 'ABC College, Madurai')
  assert.equal(fill.changeEducationRow(row, 'institutionName', 'XYZ').institute, 'XYZ, Chennai')
  assert.equal(fill.changeEducationRow({ ...row, institute: 'ABC' }, 'institutionName', 'ABC ').institute, 'ABC ', 'a space typed at the end is kept')
  assert.equal(fill.changeEducationRow(row, 'passedYear', '2014').passedYear, '2014')
  assert.equal(fill.changeExperienceRow({ rowId: 'x', company: 'A', designation: '', country: '', period: '' }, 'company', 'B').company, 'B')
})

/* ------------------------------------------------------------------ Counts */

test('each section counts only its own missing items', () => {
  const cards = fill.cardsFor()
  const missing = [
    { key: 'personal.candidateName', section: 1, kind: 'field' },
    { key: 'personal.mobileNumber', section: 1, kind: 'field' },
    { key: 'personal.currentAddress', section: 1, kind: 'field' },
    { key: 'passport.passportNumber', section: 2, kind: 'field' },
    { key: 'passport.expiryDate', section: 2, kind: 'field' },
    { key: 'education', section: 3, kind: 'field' },
    { key: 'experience.totalExperience', section: 4, kind: 'field' },
    { key: 'experience.history', section: 4, kind: 'field' },
    { key: 'documents.photo', section: 5, kind: 'document' },
  ]
  const count = (no) => fill.cardMissing(cards.find((card) => card.no === no), missing).length
  assert.deepEqual([1, 2, 3, 4].map(count), [3, 2, 1, 2])
  assert.equal(fill.toAddText(3), '3 to add')
  assert.equal(fill.toAddText(0), '')
})

test('every section has required items, so a finished one can say it is complete', () => {
  assert.ok(fill.cardsFor().every((card) => fill.cardCounts(card)))
})

/* ------------------------------------------------------------------ Draft helpers */

test('readField and writeField address the draft by the same key the missing items use', () => {
  const base = draft({ extras: { 'screening.skills': 'Welding, Fitting' } })
  assert.equal(fill.readField(base, item('personal.candidateName')), 'Asha Rao')
  assert.deepEqual(fill.readField(base, item('personal.languagesKnown')), ['English'])
  assert.equal(fill.readField(base, item('passport.passportNumber')), 'M1234567')
  const extra = { key: 'screening.skills', control: 'text', extra: true }
  assert.equal(fill.readField(base, extra), 'Welding, Fitting')
  assert.equal(fill.writeField(base, { key: 'screening.nativePlace', extra: true }, 'Madurai').extras['screening.nativePlace'], 'Madurai')
  const next = fill.writeField(base, item('personal.candidateName'), 'Asha K Rao')
  assert.equal(next.personal.candidateName, 'Asha K Rao')
  assert.equal(base.personal.candidateName, 'Asha Rao', 'the old draft is untouched')
  assert.equal(next.personal.mobileNumber, base.personal.mobileNumber)
  assert.equal(fill.writeField(base, item('experience.totalExperience'), '7').experience.totalExperience, '7')
  assert.deepEqual(fill.writeField(base, item('personal.languagesKnown'), ['English', 'Tamil']).personal.languagesKnown, ['English', 'Tamil'])
  // The paper view and the completeness read the same draft: clearing a required field makes it a missing item at once.
  const cleared = fill.writeField(base, item('personal.fatherName'), '')
  assert.ok(model.completenessOf(cleared).missing.some((entry) => entry.key === 'personal.fatherName'))
})

test('lists convert both ways', () => {
  assert.equal(fill.listToText(['Welder', 'Fitter']), 'Welder, Fitter')
  assert.deepEqual(fill.textToList(' Welder ,, Fitter,'), ['Welder', 'Fitter'])
})

/* ------------------------------------------------------------------ Source chips */

const sourced = (sources, filled = {}) => ({ fieldSources: sources, documents: { resume: { extraction: { filled: filled.resume ?? [] } }, passport: { extraction: { filled: filled.passport ?? [] } } } })

test('a value a document filled shows "From passport" or "From resume"; a typed one shows nothing', () => {
  const saved = draft()
  const form = sourced({ 'personal.fatherName': 'extracted', 'personal.candidateName': 'extracted', 'personal.nationality': 'manual', 'passport.passportNumber': 'extracted' }, { passport: ['personal.fatherName'], resume: ['personal.candidateName'] })
  assert.equal(fill.sourceChipFor(form, saved, saved, item('personal.fatherName')), 'passport')
  assert.equal(fill.sourceChipFor(form, saved, saved, item('personal.candidateName')), 'resume')
  assert.equal(fill.sourceChipFor(form, saved, saved, item('passport.passportNumber')), 'passport')
  assert.equal(fill.sourceChipFor(form, saved, saved, item('personal.nationality')), null, 'typed by a person')
  assert.equal(fill.sourceChipFor(form, saved, saved, item('personal.gender')), null, 'no source recorded')
  const typedOver = fill.writeField(saved, item('personal.fatherName'), 'Someone Else')
  assert.equal(fill.sourceChipFor(form, saved, typedOver, item('personal.fatherName')), null, 'the chip goes as soon as the value is changed')
  const cleared = fill.writeField(saved, item('personal.fatherName'), '')
  assert.equal(fill.sourceChipFor(form, saved, cleared, item('personal.fatherName')), null)
  assert.equal(fill.SOURCE_CHIP_TEXT.passport, 'From passport')
  assert.equal(fill.SOURCE_CHIP_TEXT.resume, 'From resume')
})

test('the education list shows its chip only while it is as the document filled it', () => {
  const saved = draft()
  const form = sourced({ education: 'extracted' }, { resume: ['education'] })
  assert.equal(fill.rowsSourceChip(form, saved, saved, 'education'), 'resume')
  const edited = { ...saved, education: [{ ...saved.education[0], level: 'Diploma' }] }
  assert.equal(fill.rowsSourceChip(form, saved, edited, 'education'), null)
  const added = { ...saved, education: [...saved.education, model.emptyEducationRow()] }
  assert.equal(fill.rowsSourceChip(form, saved, added, 'education'), null)
  assert.equal(fill.rowsSourceChip(sourced({}), saved, saved, 'education'), null)
})

/* ------------------------------------------------------------------ Passport */

test('the passport status follows the date in the draft', () => {
  const now = new Date('2026-11-06T00:00:00Z')
  assert.equal(fill.passportStatus('', now), 'unknown')
  assert.equal(fill.passportStatus('not a date', now), 'unknown')
  assert.equal(fill.passportStatus('2030-01-01', now), 'valid')
  assert.equal(fill.passportStatus('2027-01-01', now), 'expiring_soon')
  assert.equal(fill.passportStatus('2026-01-01', now), 'expired')
  assert.equal(fill.PASSPORT_STATUS_TEXT.valid, 'Passport valid')
  assert.equal(fill.passportExpiryHint('2030-01-01', now), '')
  assert.match(fill.passportExpiryHint('2027-01-01', now), /six months/)
  assert.match(fill.passportExpiryHint('2026-01-01', now), /expired/i)
})

test('the three experience totals are independent: no rule ties India and abroad to the total', async () => {
  const view = await src('features/application-form/fill/fill-view.tsx')
  assert.ok(!/experienceSplitHint|do not add up/.test(view), 'the Fill view shows no split warning')
  assert.ok(!Object.keys(fill).some((name) => /split/i.test(name)))
  const odd = draft({ experience: { totalExperience: '24', experienceIndia: '0', experienceAbroad: '2', history: [] } })
  assert.deepEqual(Object.keys(model.validateDraft(odd)).filter((key) => key.startsWith('experience.')), [], 'no error for totals that do not add up')
})

test('validation errors use the same keys as the fields', () => {
  const bad = draft({ passport: { passportNumber: 'M1', placeOfIssue: 'X', issueDate: '2025-01-01', expiryDate: '2024-01-01' }, personal: { ...draft().personal, mobileNumber: '12', dateOfBirth: '2999-01-01' } })
  const errors = model.validateDraft(bad)
  for (const key of ['passport.expiryDate', 'personal.mobileNumber', 'personal.dateOfBirth']) {
    assert.ok(errors[key], key)
    assert.ok(fill.FILL_KEYS.includes(key), `${key} has a field to show its error`)
  }
})
