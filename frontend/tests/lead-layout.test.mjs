import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

const cache = new Map()
async function load(path) {
  if (cache.has(path)) return cache.get(path)
  const url = new URL(path, import.meta.url)
  let source = await readFile(url, 'utf8')
  for (const spec of [...source.matchAll(/from '(\.[^']+)'/g)].map(match => match[1])) {
    const loaded = await load(new URL(`${spec}.ts`, url).href)
    source = source.replaceAll(`from '${spec}'`, `from '${loaded.__url}'`)
  }
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  const dataUrl = `data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`
  const loaded = { ...(await import(dataUrl)), __url: dataUrl }
  cache.set(path, loaded)
  return loaded
}
const model = await load('../src/features/leads/lead-model.ts')
const fields = await load('../src/features/leads/lead-fields.ts')
const fit = await load('../src/features/candidates/components/column-fit.ts')

// Exercise the actual column factory with inert React elements; no browser or API is needed.
const source = await readFile(new URL('../src/features/leads/components/lead-column-definitions.tsx', import.meta.url), 'utf8')
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.React },
  transformers: { before: [() => file => ts.factory.updateSourceFile(file, file.statements.filter(node => !ts.isImportDeclaration(node)))] },
}).outputText
const componentNames = ['CampaignCell', 'ContactCell', 'CreatedCell', 'UploadedCell', 'EducationCell', 'ExperienceCell', 'ExtraAnswerCell', 'FieldCell', 'LeadActions', 'LocationCell', 'NameCell', 'PositionCountryCell', 'ProfileCell', 'ResumeCell', 'StateCell', 'StatusOwnerCell']
const setup = `const React={createElement:(type,props)=>({type,props})};const workBlock=()=>null;
  const LEAD_HEADINGS=${JSON.stringify(model.LEAD_HEADINGS)},EXTRA_PREFIX=${JSON.stringify(model.EXTRA_PREFIX)},FIELD_PREFIX=${JSON.stringify(fields.FIELD_PREFIX)};
  const DEFAULT_COLUMN_FIELDS=new Set(${JSON.stringify([...fields.DEFAULT_COLUMN_FIELDS])});const LEAD_FIELD_DEFS=${JSON.stringify(fields.LEAD_FIELD_DEFS)};
  ${componentNames.map(name => `const ${name}=${JSON.stringify(name)};`).join('\n')}`
const columnsModule = await import(`data:text/javascript;base64,${Buffer.from(setup + compiled).toString('base64')}`)
const columns = columnsModule.leadColumns({ userById: () => null })
const requested = ['name', 'position', 'education', 'experienceIndia', 'experienceAbroad', 'experience', 'location']

test('lead table restores Actions, Created and Uploaded before the requested identity and experience breakdown', () => {
  const visible = columns.filter(column => !column.off)
  assert.deepEqual(visible.slice(0, 3).map(column => column.key), ['actions', 'created', 'uploaded'])
  assert.deepEqual(visible.slice(0, 4).map(column => column.sticky), ['start', 'start', 'start', 'start'])
  assert.deepEqual(visible.slice(3, 10).map(column => column.key), requested)
  assert.deepEqual(visible.slice(3, 10).map(column => column.header), ['Name', 'Job applied', 'Education', 'India exp.', 'Abroad exp.', 'Total exp.', 'Location'])
  assert.deepEqual(visible.slice(10).map(column => column.key), ['campaign', 'status', 'resume'])
  // the shortened headings keep the full name as the header tooltip, so the Columns menu and hover still say what they mean
  assert.deepEqual(visible.slice(6, 9).map(column => column.headerTitle), ['India experience', 'Abroad experience', 'Total experience'])
  // 'Name' is short so the heading fits one line; the full 'Name / Age / Contact' is its tooltip
  assert.equal(visible[3].headerTitle, 'Name / Age / Contact')
  assert.ok(visible.filter(column => !['name', 'experienceIndia', 'experienceAbroad', 'experience'].includes(column.key)).every(column => column.headerTitle === undefined))
  assert.equal(new Set(columns.map(column => column.key)).size, columns.length)
})

test('India, abroad and total render distinct supplied categories with no restaurant fallback', () => {
  const byKey = Object.fromEntries(columns.map(column => [column.key, column]))
  const lead = { id:'synthetic', experienceRestaurantText:'8 years', experienceTotalText:'No', experienceTotalYears:0 }
  for (const [key, kind] of [['experienceIndia','india'], ['experienceAbroad','abroad'], ['experience','total']]) {
    const element = byKey[key].render(lead)
    assert.equal(element.type, 'ExperienceCell')
    assert.equal(element.props.kind, kind)
    assert.equal(element.props.lead, lead)
  }
  assert.equal(model.experienceAnswer(lead, 'india').value, null)
  assert.equal(model.experienceAnswer(lead, 'abroad').value, null)
  assert.equal(model.experienceAnswer(lead, 'total').value, 'No')
  assert.equal(byKey.name.render(lead).type, 'NameCell')
})

test('essential supplied facts stay at desktop widths; the least important columns hide first instead of being squeezed', () => {
  const essential = ['name', 'position', 'experienceIndia', 'experienceAbroad']
  for (const width of [900, 1100, 1450, 1700]) {
    const result = fit.fitColumns(columns, width)
    for (const key of essential) assert.ok(result.keys.includes(key), `${key} omitted at ${width}px`)
    assert.deepEqual(result.keys.slice(0, 2), ['actions', 'created'])
    assert.ok(!result.keys.includes('uploaded') || result.keys.indexOf('uploaded') === 2, 'Uploaded sits right after Created')
    for (const key of ['contact', 'state', 'profile']) assert.equal(result.keys.includes(key), false, `${key} should begin off`)
    // nothing is ever narrower than its stated width: the shown columns add up to no more than the box
    const total = result.keys.reduce((sum, key) => sum + columns.find(column => column.key === key).width, 0)
    assert.ok(total <= width - fit.FIT_GUTTER, `the shown columns fit at ${width}px`)
  }
  for (const key of requested) assert.ok(fit.fitColumns(columns, 1450).keys.includes(key) || key === 'education', `${key} is shown at 1450px`)
  assert.ok(fit.fitColumns(columns, 1700).keys.includes('campaign'), 'the original campaign column is shown when it fits')
  const explicit = fit.fitColumns(columns, 1700, { campaign:true })
  assert.ok(explicit.keys.includes('campaign'), 'the full campaign column remains available by choice')
  assert.ok(fit.fitColumns(columns, 1700, { state:true }).keys.includes('state'), 'State remains available as a separate optional column')
})

test('Uploaded is a default column that goes first when the table is tight, never costing another column its place', () => {
  const uploaded = columns.find(column => column.key === 'uploaded')
  assert.equal(uploaded.header, 'Uploaded')
  assert.equal(uploaded.sortKey, 'uploaded')
  assert.ok(!uploaded.off, 'visible by default')
  const maxOptional = Math.max(...columns.filter(column => !column.off && column.optional).map(column => column.optional))
  assert.equal(uploaded.optional, maxOptional, 'the first default column to be hidden')
  assert.ok(fit.fitColumns(columns, 1700).keys.includes('uploaded'), 'shown when everything fits')
  for (const width of [900, 1100]) {
    const result = fit.fitColumns(columns, width)
    const withoutUploaded = fit.fitColumns(columns.filter(column => column.key !== 'uploaded'), width)
    assert.deepEqual(result.keys.filter(key => key !== 'uploaded'), withoutUploaded.keys, `no other column is hidden for it at ${width}px`)
  }
  assert.ok(fit.fitColumns(columns, 700, { uploaded: false }).keys.every(key => key !== 'uploaded'), 'the Columns picker can switch it off')
  assert.equal(fields.DEFAULT_COLUMN_FIELDS.has('uploaded'), true)
  assert.ok(fields.LEAD_FIELD_DEFS.some(field => field.id === 'uploaded'), 'the expanded row lists it with every other field')
})

test('Location and Job applied are wide enough for a 15-letter word at 13px bold, so a place is never broken mid-word', () => {
  const need = 15 * 8 + 16 + 1 // 15 capitals at about 8px, the cell's 8px padding either side, the 1px column line
  const byKey = Object.fromEntries(columns.map(column => [column.key, column]))
  assert.equal(columnsModule.WORD_COLUMN_MIN, need)
  for (const key of ['location', 'position']) assert.ok(byKey[key].width >= need, `${key} is ${byKey[key].width}px, needs ${need}px`)
  assert.ok(byKey.state.width >= 12 * 8 + 17, 'State holds Chhattisgarh whole')
  assert.ok(byKey.location.optional, 'Location is still hidden by the fit before anything is squeezed')
})
