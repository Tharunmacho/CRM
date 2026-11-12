import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

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
  const dataUrl = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
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
const componentNames = ['CampaignCell', 'ContactCell', 'CreatedCell', 'EducationCell', 'ExperienceCell', 'ExtraAnswerCell', 'FieldCell', 'LeadActions', 'LocationCell', 'NameCell', 'PositionCountryCell', 'ProfileCell', 'ResumeCell', 'StateCell', 'StatusOwnerCell']
const setup = `const React={createElement:(type,props)=>({type,props})};const workBlock=()=>null;
  const LEAD_HEADINGS=${JSON.stringify(model.LEAD_HEADINGS)},EXTRA_PREFIX=${JSON.stringify(model.EXTRA_PREFIX)},FIELD_PREFIX=${JSON.stringify(fields.FIELD_PREFIX)};
  const DEFAULT_COLUMN_FIELDS=new Set(${JSON.stringify([...fields.DEFAULT_COLUMN_FIELDS])});const LEAD_FIELD_DEFS=${JSON.stringify(fields.LEAD_FIELD_DEFS)};
  ${componentNames.map(name => `const ${name}=${JSON.stringify(name)};`).join('\n')}`
const columnsModule = await import(`data:text/javascript;base64,${Buffer.from(setup + compiled).toString('base64')}`)
const columns = columnsModule.leadColumns({ userById: () => null })
const requested = ['name', 'position', 'education', 'experienceIndia', 'experienceAbroad', 'experience', 'location']

test('lead table restores Actions and Created before the requested identity and experience breakdown', () => {
  const visible = columns.filter(column => !column.off)
  assert.deepEqual(visible.slice(0, 2).map(column => column.key), ['actions', 'created'])
  assert.deepEqual(visible.slice(0, 3).map(column => column.sticky), ['start', 'start', 'start'])
  assert.deepEqual(visible.slice(2, 9).map(column => column.key), requested)
  assert.deepEqual(visible.slice(2, 9).map(column => column.header), ['Name / Age / Contact', 'Job applied', 'Education', 'India experience', 'Abroad experience', 'Total experience', 'Location'])
  assert.deepEqual(visible.slice(9).map(column => column.key), ['campaign', 'status', 'resume'])
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

test('essential supplied facts remain available at desktop widths while optional duplicates start off', () => {
  for (const width of [900, 1100, 1450, 1700]) {
    const result = fit.fitColumns(columns, width)
    for (const key of requested) assert.ok(result.keys.includes(key), `${key} omitted at ${width}px`)
    assert.deepEqual(result.keys.slice(0, 2), ['actions', 'created'])
    for (const key of ['contact', 'state', 'profile']) assert.equal(result.keys.includes(key), false, `${key} should begin off`)
  }
  assert.ok(fit.fitColumns(columns, 1700).keys.includes('campaign'), 'the original campaign column is shown when it fits')
  const explicit = fit.fitColumns(columns, 1700, { campaign:true })
  assert.ok(explicit.keys.includes('campaign'), 'the full campaign column remains available by choice')
  assert.ok(fit.fitColumns(columns, 1700, { state:true }).keys.includes('state'), 'State remains available as a separate optional column')
})
