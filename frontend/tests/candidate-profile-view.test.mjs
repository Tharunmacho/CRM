import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const { outputText } = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
  })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}

const ocrViewUrl = moduleUrl(await readFile(new URL('../src/features/candidates/ocr-view.ts', import.meta.url), 'utf8'))
const source = await readFile(new URL('../src/features/candidates/profile-view.ts', import.meta.url), 'utf8')
const { buildProfileModel, dateKey, monthLabel, periodLabel } = await import(moduleUrl(source.replace("'./ocr-view'", `'${ocrViewUrl}'`)))

const fmt = { money: (n) => `AED ${n}`, date: (v) => `on ${v.slice(0, 10)}` }

const candidate = (patch = {}) => ({
  id: 'c1', ref: 'CND-1', name: 'Asha Rao', phone: '9876543210', nationality: '', currentCity: '',
  languages: [], designations: [], totalExperience: 0, gulfExperience: 0, skills: [],
  source: 'email', status: 'new', ownerId: '', addedAt: '2026-10-01T08:00:00Z', photoTone: 0,
  documents: [], resumeText: '', ...patch,
})

const veris = {
  resume: {
    name: 'Asha R',
    designation: 'Site Engineer',
    industry: 'Construction',
    highest_qualification: 'B.E. Civil',
    contact: { emails: ['asha@example.test'], phones: ['9876543210'], address: '12 Lake Road, Chennai', whatsapp: '9876543210', websites: ['a.example.test', 'b.example.test'], linkedin: 'linkedin.com/in/asha' },
    personal_info: { date_of_birth: '25th May 1976', gender: 'female', nationality: 'Indian', marital_status: 'married', languages_known: ['English', 'Tamil'] },
    passport_details: { passport_number: 'P1234567', date_of_issue: '1 June 2020', date_of_expiry: '31/05/2030', place_of_issue: 'Chennai' },
    total_experience_months: 62, total_experience_human: '5 years 2 months',
    overseas_experience_months: 24, overseas_experience_human: '2 years',
    experience: [
      { title: 'Junior Engineer', company: 'Alpha', start: '2015-01', end: '2018-12', duration_months: 48, duration_human: '4 years' },
      { title: 'Senior Engineer', company: 'Gamma', location: 'Dubai', is_overseas: true, start: 'Jan 2021', end: 'Present', duration_months: 24, duration_human: '2 years', description: ' Led crews. ' },
      { title: 'Engineer', company: 'Beta', country: 'India', start: '2019-02', end: '2020-12' },
      { title: '', company: '' },
    ],
    skills: ['AutoCAD', 'Surveying', 'AutoCAD'],
    education: [{ degree: 'B.E. Civil', institution: 'Anna University', start: '2011', end: '2015' }, { degree: '', institution: '' }],
    certifications: ['PMP', ' ', 'OSHA'],
    achievements: ['Safety award'],
    projects: [{ name: 'Metro viaduct', description: 'Pier works' }, { name: '' }],
  },
  _sources: { resume: { source_file: { file_name: 'asha-cv.pdf', channel: 'email' } } },
}

test('a record with no stored reading shows only what was saved and invents nothing', () => {
  const model = buildProfileModel(candidate(), {}, fmt)
  assert.equal(model.designation, '')
  assert.deepEqual(model.details.map((r) => r.label), ['Name', 'Phone'])
  assert.deepEqual(model.job, [])
  assert.deepEqual(model.additional, [])
  assert.equal(model.summary, '')
  assert.deepEqual(model.contacts, [{ kind: 'phone', value: '9876543210' }])
  assert.deepEqual([model.jobs, model.skills, model.education, model.certifications, model.projects, model.passport], [[], [], [], [], [], []])
  assert.deepEqual(model.meta, ['Email', 'Added on 2026-10-01'])
})

test('the Veris reading fills what the record lacks, tidies dates, and the record wins when it has a value', () => {
  const model = buildProfileModel(candidate({ nationality: 'Indian (saved)', dob: '1976-05-24', currentCity: 'Madurai' }), veris, fmt)
  const row = (rows, label) => rows.find((r) => r.label === label)?.value
  assert.equal(row(model.additional, 'Date of birth'), '24 May 1976')
  assert.equal(row(model.additional, 'Nationality'), 'Indian (saved)')
  assert.equal(row(model.additional, 'Gender'), 'Female')
  assert.equal(row(model.additional, 'Marital status'), 'Married')
  assert.equal(row(model.details, 'Languages'), 'English, Tamil')
  assert.equal(row(model.details, 'Email'), 'asha@example.test')
  assert.equal(row(model.details, 'Address'), '12 Lake Road, Chennai')
  assert.equal(row(model.details, 'Current city'), 'Madurai')
  assert.deepEqual(model.details.find((r) => r.label === 'Websites').items, ['a.example.test', 'b.example.test'])
  assert.equal(model.designation, 'Site Engineer')
  // Veris dashboard order: identity and role first, contact after.
  assert.deepEqual(model.details.slice(0, 6).map((r) => r.label), ['Name', 'Job title', 'Industry', 'Highest qualification', 'Total experience', 'Languages'])
  assert.equal(row(model.details, 'Name'), 'Asha Rao')
  assert.equal(row(model.details, 'Industry'), 'Construction')
  assert.equal(row(model.details, 'Highest qualification'), 'B.E. Civil')
  assert.equal(row(model.details, 'Total experience'), '5 years 2 months')
  assert.equal(row(model.additional, 'Overseas experience'), '2 years')
  assert.equal(row(model.additional, 'Experience in India'), undefined)
  // The saved figure replaces the reading's.
  const saved = buildProfileModel(candidate({ totalExperience: 7.5, gulfExperience: 3, expectedSalary: 4500, designations: ['Foreman'] }), veris, fmt)
  assert.equal(row(saved.details, 'Total experience'), '7.5 years')
  assert.equal(row(saved.additional, 'Overseas experience'), '3 years')
  assert.equal(row(saved.job, 'Expected salary'), 'AED 4500')
  assert.equal(saved.designation, 'Foreman')
  assert.deepEqual(model.passport.map((r) => [r.label, r.value]), [
    ['Passport number', 'P1234567'], ['Place of issue', 'Chennai'], ['Issue date', '1 Jun 2020'], ['Expiry date', '31 May 2030'],
  ])
  const savedPassport = buildProfileModel(candidate({ passportNo: 'Z999', passportExpiry: '2031-01-02' }), veris, fmt)
  assert.equal(row(savedPassport.passport, 'Passport number'), 'Z999')
  assert.equal(row(savedPassport.passport, 'Expiry date'), '2 Jan 2031')
})

test('identity header: source file, channel, nationality and contact chips come only from data', () => {
  const model = buildProfileModel(candidate({ currentCity: 'Madurai', whatsapp: '98765 43210' }), veris, fmt)
  assert.equal(model.sourceFileName, 'asha-cv.pdf')
  assert.deepEqual(model.meta, ['asha-cv.pdf', 'Email', 'Added on 2026-10-01'])
  // WhatsApp equal to the phone number is not repeated.
  assert.deepEqual(model.contacts.map((c) => c.kind), ['email', 'phone', 'location'])
  assert.equal(model.contacts[2].value, 'Madurai')
  const other = buildProfileModel(candidate({ whatsapp: '9000000000' }), veris, fmt)
  assert.deepEqual(other.contacts.map((c) => c.kind), ['email', 'phone', 'whatsapp', 'location'])
  assert.equal(other.contacts[3].value, '12 Lake Road, Chennai')
})

test('experience is newest first with the current job leading; blank entries and duplicates are dropped', () => {
  const model = buildProfileModel(candidate(), veris, fmt)
  assert.deepEqual(model.jobs.map((j) => j.title), ['Senior Engineer', 'Engineer', 'Junior Engineer'])
  assert.deepEqual(model.jobs[0], { title: 'Senior Engineer', company: 'Gamma', location: 'Dubai', overseas: true, period: 'Jan 2021 – Present', duration: '2 years', description: 'Led crews.' })
  assert.equal(model.jobs[1].location, 'India')
  assert.equal(model.jobs[2].period, 'Jan 2015 – Dec 2018')
  assert.equal(model.experienceMeta, '5 years 2 months in total · 2 years overseas')
  assert.deepEqual(model.skills, ['AutoCAD', 'Surveying'])
  assert.deepEqual(model.education, [{ degree: 'B.E. Civil', institution: 'Anna University', period: '2011 – 2015', grade: '' }])
  assert.deepEqual(model.certifications, ['PMP', 'OSHA'])
  assert.deepEqual(model.achievements, ['Safety award'])
  assert.deepEqual(model.projects, [{ name: 'Metro viaduct', description: 'Pier works' }])
})

test('the saved skills list replaces the reading and the legacy storage shape gives the same profile', () => {
  assert.deepEqual(buildProfileModel(candidate({ skills: ['Welding'] }), veris, fmt).skills, ['Welding'])
  const legacy = { resume: { ...veris.resume, source_file: { file_name: 'asha-cv.pdf' } } }
  const model = buildProfileModel(candidate(), legacy, fmt)
  assert.equal(model.sourceFileName, 'asha-cv.pdf')
  assert.equal(model.jobs.length, 3)
  assert.equal(model.designation, 'Site Engineer')
})

test('resume dates sort and read sensibly', () => {
  assert.ok(dateKey('Present') > dateKey('2024-01'))
  assert.ok(dateKey('Mar 2020') > dateKey('2019-12'))
  assert.equal(dateKey('06/2019'), dateKey('2019-06'))
  assert.equal(dateKey('2018'), 2018 * 12)
  assert.equal(dateKey('soon'), -1)
  assert.equal(monthLabel('2019-06'), 'Jun 2019')
  assert.equal(monthLabel('Present'), 'Present')
  assert.equal(periodLabel('2019-06', '2019-06'), 'Jun 2019')
  assert.equal(periodLabel('2019-06', ''), 'From Jun 2019')
  assert.equal(periodLabel('', '2020'), 'Until 2020')
})

test('summary and education grade come from the reading when it has them', () => {
  const reading = { resume: { ...veris.resume, summary: ' Seeking a site role. ', education: [{ degree: '8 Standard', institution: 'Islamia school', grade: 'A - Grade' }] } }
  const model = buildProfileModel(candidate(), reading, fmt)
  assert.equal(model.summary, 'Seeking a site role.')
  assert.deepEqual(model.education, [{ degree: '8 Standard', institution: 'Islamia school', period: '', grade: 'A - Grade' }])
})
