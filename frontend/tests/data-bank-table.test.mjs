import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(path) {
  const source = await readFile(new URL(path, import.meta.url), 'utf8')
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const table = await load('../src/features/candidates/data-bank-table.ts')

test('the candidate table has seven columns, Actions last and right-aligned, the rest left-aligned', () => {
  assert.deepEqual(table.BANK_TABLE_COLUMNS.map((column) => column.header), ['Candidate', 'Contact', 'Source', 'Agency', 'Recruiter', 'Status', 'Actions'])
  assert.equal(table.BANK_TABLE_COLUMNS.some((column) => /country/i.test(column.header)), false)
  assert.deepEqual(table.BANK_TABLE_COLUMNS.map((column) => column.align), ['left', 'left', 'left', 'left', 'left', 'left', 'right'])
})

test('column shares add up to 100 percent and every width follows its share', () => {
  assert.equal(table.bankSharesTotal(), 100)
  assert.equal(table.BANK_TABLE_MIN_WIDTH, table.BANK_TABLE_COLUMNS.reduce((sum, column) => sum + table.bankColumnWidth(column), 0))
  assert.ok(table.BANK_TABLE_MIN_WIDTH <= 880, 'the table must fit without sideways scrolling at the card breakpoint')
  assert.ok(table.BANK_TABLE_COLUMNS.every((column) => column.share >= 8))
})

test('job-group rows use the same shares as the table', () => {
  const template = table.bankGridTemplate().split(' minmax').length
  assert.equal(template, table.BANK_TABLE_COLUMNS.length)
  assert.ok(table.bankGridTemplate().startsWith('minmax(0, 24fr)'))
})

test('every sortable column sorts on a key the server knows', async () => {
  const server = await readFile(new URL('../../backend/app/services/candidate_search.py', import.meta.url), 'utf8')
  for (const column of table.BANK_TABLE_COLUMNS.filter((item) => item.sortKey)) assert.match(server, new RegExp(`"${column.sortKey}":`), `server cannot sort on "${column.sortKey}"`)
})

test('the agent never shows an internal pointer', () => {
  assert.equal(table.agentLabel('email_message:3f9c2a'), '')
  assert.equal(table.agentLabel('whatsapp_message:abc-123'), '')
  assert.equal(table.agentLabel('person_review:77'), '')
  assert.equal(table.agentLabel('lead_sheet:77', 'other'), '')
  assert.equal(table.agentLabel('  Al Noor Agency  '), 'Al Noor Agency')
  assert.equal(table.agentLabel('Ref: Hassan'), 'Ref: Hassan')
  assert.equal(table.agentLabel(null), '')
  assert.equal(table.agentLabel(undefined), '')
})

test('import provenance is not an agent, while real referring names keep their intake channel', () => {
  assert.equal(table.agentLabel('  Bulk  import  ', 'other'), '')
  assert.equal(table.agentLabel('Bulk import', 'email'), '')
  assert.equal(table.agentLabel('Bulk import', 'referral'), 'Bulk import')
  assert.equal(table.agentLabel('Al Noor Agency', 'email'), 'Al Noor Agency')
  assert.equal(table.agentLabel('Al Noor Agency', 'referral'), 'Al Noor Agency')
})

test('Other intake shows human provenance without record identifiers or agency names', () => {
  assert.equal(table.sourceProvenance('other', 'lead_sheet:private-record-id'), 'Lead Sheet')
  assert.equal(table.sourceProvenance('other', '  Bulk  import  '), 'Bulk import')
  assert.equal(table.sourceProvenance('other', 'Al Noor Agency'), '')
  assert.equal(table.sourceProvenance('other', 'person_review:private-record-id'), '')
  assert.equal(table.sourceProvenance('email', 'email_message:private-record-id'), '')
  assert.equal(table.sourceProvenance('referral', 'Bulk import'), '')
  assert.equal(table.sourceProvenance('other', null), '')
})

test('the unread-name placeholder and blank names are flagged, real names are not', () => {
  assert.equal(table.isPlaceholderName('Name needs review'), true)
  assert.equal(table.isPlaceholderName('  name NEEDS review '), true)
  assert.equal(table.isPlaceholderName(''), true)
  assert.equal(table.isPlaceholderName(undefined), true)
  assert.equal(table.isPlaceholderName('Test Candidate'), false)
})

test('the muted line under the name joins the reference and the date of birth', () => {
  assert.equal(table.candidateSubline('C-1001', '05 Jan 1990'), 'C-1001 · 05 Jan 1990')
  assert.equal(table.candidateSubline('C-1001', null), 'C-1001')
  assert.equal(table.candidateSubline('', '05 Jan 1990'), '05 Jan 1990')
  assert.equal(table.candidateSubline('0123456789abcdef', null), '0123456789ab…')
})

test('job groups read job title bold and client muted; the group with no job says so', () => {
  assert.equal(table.NOT_MATCHED_JOB_LABEL, 'Not matched to a job')
  assert.equal(table.groupHeading({ orderId: null, title: null }), 'Not matched to a job')
  assert.equal(table.groupHeading({ orderId: 'o1', title: ' Site Engineer ' }), 'Site Engineer')
  assert.equal(table.groupHeading({ orderId: 'o1', title: '' }), 'Job')
  assert.equal(table.groupClientLine({ orderId: 'o1', company: 'Acme', client: 'Acme', country: 'Qatar' }), 'Acme · Qatar')
  assert.equal(table.groupClientLine({ orderId: 'o1', company: 'Acme', client: 'Beta', country: null }), 'Acme · Beta')
  assert.equal(table.groupClientLine({ orderId: null, company: 'Acme' }), '')
})
