import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const form = await load('../src/features/clients/client-form.ts')
const { cleanStaffRows, validateParty, buildAgencyPayload, fieldErrorsFromApi, firstName, staffRowsOf, emptyStaffRow, emptyParty, isEmptyStaffRow } = form

const party = { name: ' Khalid Rahman ', agencyName: ' Gulf Build ', phone: '+91 98765 43210', email: 'khalid@example.com', address: ' 12 Marina Road ', state: 'Kerala' }

test('completely empty staff rows are ignored and the rest are trimmed', () => {
  const rows = [
    emptyStaffRow(),
    { name: ' Priya Nair ', designation: ' HR ', phone: '', email: ' priya@example.com ' },
    { name: '  ', designation: '', phone: '  ', email: '' },
    { name: 'Ravi', designation: '', phone: '98765', email: '' },
  ]
  assert.equal(isEmptyStaffRow(rows[0]), true)
  assert.deepEqual(cleanStaffRows(rows), [
    { name: 'Priya Nair', designation: 'HR', email: 'priya@example.com' },
    { name: 'Ravi', phone: '98765' },
  ])
  assert.deepEqual(cleanStaffRows([]), [])
})

test('saved staff come back as editable rows', () => {
  assert.deepEqual(staffRowsOf([{ name: 'A', phone: '1' }]), [{ name: 'A', designation: '', phone: '1', email: '' }])
  assert.deepEqual(staffRowsOf(undefined), [])
})

test('the contact person and agency name are required, with plain messages', () => {
  const errors = validateParty(emptyParty(), [])
  assert.deepEqual(Object.keys(errors).sort(), ['agencyName', 'name'])
  assert.match(errors.name, /contact person/)
  assert.deepEqual(validateParty(party, []), {})
})

test('email and phone are checked only when filled in', () => {
  const errors = validateParty({ ...party, email: 'not-an-email', phone: 'call me' }, [])
  assert.match(errors.email, /valid email/)
  assert.match(errors.phone, /digits/)
  assert.match(validateParty({ ...party, phone: '123' }, []).phone, /5 and 15 digits/)
  assert.deepEqual(validateParty({ ...party, phone: '', email: '' }, []), {})
})

test('a staff row with details needs a name; blank rows never block the form', () => {
  const errors = validateParty(party, [emptyStaffRow(), { name: '', designation: 'HR', phone: '', email: 'bad' }])
  assert.match(errors['staff.1.name'], /name/)
  assert.match(errors['staff.1.email'], /valid email/)
  assert.equal(errors['staff.0.name'], undefined)
  assert.deepEqual(validateParty(party, [emptyStaffRow(), emptyStaffRow()]), {})
})

test('no more than 50 staff entries', () => {
  const many = Array.from({ length: 51 }, (_, i) => ({ name: `P${i}`, designation: '', phone: '', email: '' }))
  assert.match(validateParty(party, many).staff, /50/)
})

test('the agency body trims values, leaves blanks out and carries the cleaned staff', () => {
  const body = buildAgencyPayload({ ...party, phone: '', state: ' ' }, [{ name: 'Ravi', designation: '', phone: '', email: '' }, emptyStaffRow()])
  assert.deepEqual(body, {
    name: 'Khalid Rahman', agencyName: 'Gulf Build', phone: undefined, email: 'khalid@example.com', address: '12 Marina Road', state: undefined,
    staffContacts: [{ name: 'Ravi' }],
  })
})

test('server 422 errors land on the right field', () => {
  const detail = [
    { loc: ['body', 'agencyName'], msg: 'String should have at least 1 character' },
    { loc: ['body', 'staffContacts', 2, 'email'], msg: 'Invalid email.' },
    { loc: ['body', 'company', 'state'], msg: 'Too long' },
    { loc: ['body', 'staffIds'], msg: 'Every id must be an active staff member' },
    { loc: ['body', 'something_else'], msg: 'Odd' },
  ]
  const { fields, general } = fieldErrorsFromApi(detail)
  assert.equal(fields.agencyName, 'String should have at least 1 character.')
  assert.equal(fields['staff.2.email'], 'Invalid email.')
  assert.equal(fields.state, 'Too long.')
  assert.match(fields.staffIds, /active staff/)
  assert.match(general, /something_else: Odd/)
  assert.deepEqual(fieldErrorsFromApi('Not allowed'), { fields: {}, general: 'Not allowed' })
  assert.deepEqual(fieldErrorsFromApi(undefined), { fields: {}, general: '' })
})

test('first names for the small staff chips', () => {
  assert.equal(firstName('Anita Rao'), 'Anita')
  assert.equal(firstName('  Zoë  '), 'Zoë')
  assert.equal(firstName(''), '')
  assert.equal(firstName(undefined), '')
})

