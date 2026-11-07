import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(`../src/features/${path}`, import.meta.url), 'utf8')
const load = async (path, imports = {}) => {
  let source = await read(path)
  for (const [from, url] of Object.entries(imports)) source = source.replaceAll(`'${from}'`, `'${url}'`)
  const url = moduleUrl(source)
  return { url, module: await import(url) }
}

const phoneModule = await load('candidates/entry-phone.ts')
const modelModule = await load('candidates/entry-model.ts')
const extrasModule = await load('candidates/entry-extras.ts', { './entry-phone': phoneModule.url })
const slotsUrl = moduleUrl(await readFile(new URL('../src/shared/model/document-slots.ts', import.meta.url), 'utf8'))
const formModelModule = await load('application-form/form-model.ts', { '@/shared/model/document-slots': slotsUrl })
const bridgeModule = await load('application-form/entry-bridge.ts', { './form-model': formModelModule.url })
const completenessModule = await load('candidates/entry-completeness.ts', {
  '@/features/application-form/entry-bridge': bridgeModule.url, './entry-extras': extrasModule.url, '@/shared/model/document-slots': slotsUrl,
})
const ocrModule = await load('candidates/ocr-form.ts')

const { splitPhone, joinPhone, phoneHint } = phoneModule.module
const { EMPTY_CANDIDATE_FORM: EMPTY, draftSnapshot, restoredCandidateDraft, DRAFT_FORM_KEYS } = modelModule.module
const { effectiveWhatsapp, whatsappIsSame, effectiveNativePlace, screeningPayload } = extrasModule.module
const { entryCompleteness, nextDocument, screeningMissing } = completenessModule.module
const { parseRows, serializeRows, educationPayload, historyPayload, emptyEntryRow, EDUCATION_KEYS } = bridgeModule.module
const { mergeOcrForm } = ocrModule.module

test('phone numbers split into a country code and a number, and join back', () => {
  assert.deepEqual(splitPhone('+91 98400 00000'), { code: '+91', national: '98400 00000' })
  assert.deepEqual(splitPhone('98400 00000'), { code: '+91', national: '98400 00000' })
  assert.deepEqual(splitPhone('919840000000'), { code: '+91', national: '9840000000' })
  assert.deepEqual(splitPhone('+974 5512 3456'), { code: '+974', national: '5512 3456' })
  assert.deepEqual(splitPhone('+971501234567'), { code: '+971', national: '501234567' })
  assert.deepEqual(splitPhone('+999 123'), { code: '', national: '+999 123' })
  assert.deepEqual(splitPhone(''), { code: '+91', national: '' })
  assert.equal(joinPhone('+91', ' 98400 00000 '), '+91 98400 00000')
  assert.equal(joinPhone('+91', '  '), '', 'no number typed means an empty field, so the required check still fires')
  assert.equal(joinPhone('', '+999 123'), '+999 123')
})

test('the phone hint names the country and checks the digit count without blocking', () => {
  assert.deepEqual(phoneHint('+91', ''), { text: 'Selected: India (+91) - requires 10 digits', tone: 'info' })
  assert.equal(phoneHint('+91', '98400 00000').tone, 'ok')
  assert.equal(phoneHint('+91', '098400 00000').tone, 'ok', 'a leading trunk 0 is not counted')
  const short = phoneHint('+91', '98400')
  assert.equal(short.tone, 'warn')
  assert.match(short.text, /10 digits.*typed 5/)
  assert.equal(phoneHint('+974', '55123456').tone, 'ok')
})

test('"same as" switches decide what is saved, and a different number read from a document wins', () => {
  const base = { ...EMPTY, phone: '+91 98400 00000', currentCity: 'Kochi' }
  assert.equal(effectiveWhatsapp({ ...base, whatsappSame: 'yes', whatsapp: '' }), '+91 98400 00000')
  assert.equal(whatsappIsSame({ ...base, whatsappSame: 'yes', whatsapp: '98400 00000' }), true)
  // A document read a different WhatsApp number: the switch no longer applies.
  assert.equal(whatsappIsSame({ ...base, whatsappSame: 'yes', whatsapp: '+91 97000 11111' }), false)
  assert.equal(effectiveWhatsapp({ ...base, whatsappSame: 'yes', whatsapp: '+91 97000 11111' }), '+91 97000 11111')
  assert.equal(effectiveWhatsapp({ ...base, whatsappSame: 'no', whatsapp: '' }), '')
  assert.equal(effectiveNativePlace({ ...base, nativeSame: 'yes', nativePlace: 'Thrissur' }), 'Kochi')
  assert.equal(effectiveNativePlace({ ...base, nativeSame: '', nativePlace: ' Thrissur ' }), 'Thrissur')
})

test('the screening keys of the create body leave empty values out and keep the server names', () => {
  const body = screeningPayload({ ...EMPTY, nativePlace: 'Thrissur', familyNumber: '+91 90000 11111', countryPreference: 'Qatar',
    interestLevel: 'interested', serviceChargesStatus: 'agreed', nextFollowUp: '2026-11-20' })
  assert.deepEqual(body, { native_place: 'Thrissur', family_member_number: '+91 90000 11111', country_preference: 'Qatar',
    job_duration_preference: undefined, best_time_to_contact: undefined, service_charges_status: 'agreed',
    interest_level: 'interested', next_follow_up_date: '2026-11-20' })
  const empty = screeningPayload(EMPTY)
  assert.equal(empty.service_charges_status, 'not_discussed')
  assert.equal(empty.interest_level, undefined)
  // The levels are an admin-managed list; the server checks the value against it, so the page sends what was picked.
  assert.equal(screeningPayload({ ...EMPTY, interestLevel: 'maybe_later' }).interest_level, 'maybe_later')
  assert.equal(screeningPayload({ ...EMPTY, interestLevel: '  ' }).interest_level, undefined)
})

test('completeness counts the same 19 items as the saved application form', () => {
  const none = entryCompleteness(EMPTY, { resume: false, passport: false, photo: false })
  assert.deepEqual([none.percent, none.filled, none.total], [0, 0, 19])
  assert.equal(none.missing.length, 19)
  const form = { ...EMPTY, name: 'Arjun', fatherName: 'Prasad', dob: '1993-03-12', maritalStatus: 'Married', gender: 'male', nationality: 'Indian',
    languages: 'English, Hindi', phone: '+91 98400 00000', currentAddress: 'Kochi', totalExperience: '0',
    education: JSON.stringify([{ level: 'ITI', passedYear: '2012', institute: 'Govt ITI' }]) }
  const part = entryCompleteness(form, { resume: true, passport: false, photo: false })
  // 9 personal + total experience ("0" counts: it was answered) + education + resume = 12 of 19.
  assert.deepEqual([part.filled, part.percent], [12, 63])
  assert.deepEqual(part.missing.map((item) => item.key).sort(), ['documents.passport', 'documents.photo', 'passportExpiry', 'passportIssue', 'passportNo', 'passportPlace', 'workHistory'].sort())
  assert.deepEqual(part.groups.find((group) => group.section === 'personal'), { section: 'personal', label: 'Personal', filled: 9, total: 9 })
  // A row of blanks is not an answer.
  assert.equal(entryCompleteness({ ...EMPTY, education: JSON.stringify([emptyEntryRow(EDUCATION_KEYS)]) }, { resume: false, passport: false, photo: false }).filled, 0)
})

test('the next document to ask for is the passport after a resume, and the resume after a passport', () => {
  assert.equal(nextDocument({ resume: true, passport: false, photo: false }), 'passport')
  assert.equal(nextDocument({ resume: false, passport: true, photo: false }), 'resume')
  assert.equal(nextDocument({ resume: true, passport: true, photo: false }), 'photo')
  assert.equal(nextDocument({ resume: true, passport: true, photo: true }), null)
  assert.equal(nextDocument({ resume: false, passport: false, photo: false }), null)
})

test('screening details that are not recorded yet are listed but never counted', () => {
  assert.equal(screeningMissing(EMPTY).length, 8)
  const filled = screeningMissing({ ...EMPTY, nativePlace: 'Kochi', familyNumber: '1', designation: 'Welder', countryPreference: 'Qatar', jobDurationPreference: '2 years',
    bestTimeToContact: 'Evening', interestLevel: 'interested', serviceChargesStatus: 'agreed' })
  assert.deepEqual(filled, [])
})

const scan = (fields) => ({ fields, warnings: [], raw: { text: 'document' }, extraction_status: 'complete' })

test('a document fills only empty or still auto-filled fields; what a person typed is never replaced', () => {
  const edited = new Set()
  let form = { ...EMPTY }
  // The resume fills the empty form.
  form = mergeOcrForm(form, EMPTY, { resume: scan({ name: 'Arjun Nair', fatherName: 'Prasad Nair', currentAddress: 'Kochi', education: [{ level: 'SSLC', passedYear: 2010, institute: 'St Mary' }] }) }, edited)
  assert.equal(form.name, 'Arjun Nair')
  assert.deepEqual(JSON.parse(form.education), [{ level: 'SSLC', passedYear: 2010, institute: 'St Mary' }])
  // The person corrects the father's name, then the passport arrives: it fills the passport fields and the name
  // (still auto-filled) but never the typed father's name.
  form = { ...form, fatherName: 'Typed By Person' }
  edited.add('fatherName')
  form = mergeOcrForm(form, EMPTY, {
    resume: scan({ name: 'Arjun Nair', fatherName: 'Prasad Nair', currentAddress: 'Kochi' }),
    passport: scan({ name: 'ARJUN PRASAD NAIR', fatherName: 'PRASAD NAIR', passportNo: 'K8123456', passportPlace: 'COCHIN' }),
  }, edited)
  assert.equal(form.fatherName, 'Typed By Person')
  assert.equal(form.name, 'ARJUN PRASAD NAIR', 'the passport is the authority for a value that was only auto-filled')
  assert.equal(form.passportNo, 'K8123456')
  assert.equal(form.currentAddress, 'Kochi')
  // A field the person cleared on purpose stays empty.
  edited.add('currentAddress')
  form = mergeOcrForm({ ...form, currentAddress: '' }, EMPTY, { resume: scan({ currentAddress: 'Kochi' }) }, edited)
  assert.equal(form.currentAddress, '')
  // A read that no longer carries a value clears an auto-filled one (the document was replaced) but not a typed one.
  form = mergeOcrForm(form, EMPTY, { resume: scan({ nationality: 'Indian' }) }, edited)
  assert.equal(form.passportNo, '')
  assert.equal(form.fatherName, 'Typed By Person')
  assert.equal(form.nationality, 'Indian')
})

test('repeating rows survive the round trip through the flat form, and blank rows are dropped on save', () => {
  assert.deepEqual(parseRows('', EDUCATION_KEYS), [])
  assert.deepEqual(parseRows('not json', EDUCATION_KEYS), [])
  assert.deepEqual(parseRows(JSON.stringify([{ level: 'ITI', passedYear: 2012, extra: 'x' }]), EDUCATION_KEYS), [{ level: 'ITI', passedYear: '2012', institute: '' }])
  assert.equal(serializeRows([emptyEntryRow(EDUCATION_KEYS)]), '', 'only blank rows keep the field untouched')
  const rows = [{ level: 'ITI', passedYear: '2012', institute: 'Govt ITI' }, emptyEntryRow(EDUCATION_KEYS)]
  assert.equal(parseRows(serializeRows(rows), EDUCATION_KEYS).length, 2, 'a row you just added stays while you fill it in')
  assert.deepEqual(educationPayload(serializeRows(rows)), [{ level: 'ITI', passedYear: 2012, institute: 'Govt ITI' }])
  assert.deepEqual(educationPayload(JSON.stringify([{ level: 'B.Tech', passedYear: '20x', institute: '' }])), [{ level: 'B.Tech', passedYear: null, institute: null }])
  assert.deepEqual(historyPayload(JSON.stringify([{ company: 'Gulf Steel', designation: ' Fitter ', country: 'Qatar', period: '' }, { company: '', designation: '', country: '', period: '' }])),
    [{ company: 'Gulf Steel', designation: 'Fitter', country: 'Qatar', period: null }])
})

test('a draft autosave carries the keys the server accepts, under the server names, and restores them', () => {
  const form = { ...EMPTY, name: 'Arjun', nativePlace: 'Kochi', interestLevel: 'interested', whatsappSame: 'no', familyNumber: '9876543210', nextFollowUp: '2026-12-01', nativeSame: 'yes', assignTo: 'u1', fatherName: 'Prasad' }
  const draft = draftSnapshot(form, new Set(['name', 'nativePlace', 'interestLevel', 'fatherName', 'familyNumber', 'nativeSame', 'assignTo']))
  assert.equal(draft.form.nativePlace, 'Kochi')
  assert.equal(draft.form.familyMemberNumber, '9876543210')
  assert.equal(draft.form.nextFollowUpDate, '2026-12-01')
  assert.equal(draft.form.whatsappSameAsPhone, 'no')
  assert.equal(draft.form.familyNumber, undefined)
  assert.equal(draft.form.nativeSame, undefined)
  assert.equal(draft.form.assignTo, undefined)
  const serverKeys = new Set(Object.keys(draft.form))
  assert.ok([...serverKeys].every((key) => key in { ...Object.fromEntries(DRAFT_FORM_KEYS.map((k) => [k, 1])), familyMemberNumber: 1, nextFollowUpDate: 1, whatsappSameAsPhone: 1 }))
  assert.deepEqual(draft.editedFields.sort(), ['familyMemberNumber', 'fatherName', 'interestLevel', 'name', 'nativePlace'])
  const restored = restoredCandidateDraft(draft)
  assert.equal(restored.form.familyNumber, '9876543210')
  assert.equal(restored.form.nextFollowUp, '2026-12-01')
  assert.equal(restored.form.whatsappSame, 'no')
  assert.ok(restored.edited.has('familyNumber') && restored.edited.has('nativePlace'))
})
