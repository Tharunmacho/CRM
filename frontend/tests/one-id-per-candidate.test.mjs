import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// One person, ONE visible ID per screen: the Data Bank ID everywhere except Travelled, which shows only the Traveller ID.
const root = fileURLToPath(new URL('../', import.meta.url))
const bundle = await build({
  stdin: { contents: `
    export { CandidateCell } from './src/features/candidates/components/data-bank-columns'
    export { CandidateIds } from './src/screens/tasks/daily-table'
    export { TravellerIdChip } from './src/features/candidates/components/candidate-id-chip'
  `, resolveDir: root, loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  alias: { '@': path.join(root, 'src') },
  external: ['react', 'react-dom', 'react/jsx-runtime', 'react-router-dom', 'lucide-react'],
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const { CandidateCell, CandidateIds, TravellerIdChip } = compiled.exports
const render = (type, props) => renderToStaticMarkup(React.createElement(type, props))
const read = (file) => readFile(new URL(file, import.meta.url), 'utf8')

const person = { id: 'c1', name: 'Asha Rao', ref: 'CND-01096', careId: 'CI-078', travellerId: 'CI-T-094', dob: '1990-01-05', phone: '+910000000001', passportNo: 'P1234567' }

test('the Data Bank row shows only the Data Bank ID: no CND reference, no Traveller ID, in the text or the tooltip', () => {
  const html = render(CandidateCell, { candidate: person })
  assert.match(html, /<b class="cid-main">CI-078<\/b>/)
  assert.doesNotMatch(html, /CND-|CI-T-|Traveller/)
  assert.match(html, /title="[^"]*CI-078/)
})

test('the Data Bank row of a very old candidate without a Data Bank ID shows the reference instead, never both', () => {
  const html = render(CandidateCell, { candidate: { ...person, careId: null } })
  assert.match(html, /CND-01096/)
  assert.doesNotMatch(html, /CI-078|CI-T-/)
})

test('the Tasks row (Today\'s candidates and Follow-up) shows only the Data Bank ID', () => {
  const html = render(CandidateIds, { item: { candidateCareId: 'CI-078', candidateRef: 'CND-01096' } })
  assert.match(html, /CI-078/)
  assert.doesNotMatch(html, /CND-|·/)
  const old = render(CandidateIds, { item: { candidateCareId: null, candidateRef: 'CND-01096' } })
  assert.match(old, /CND-01096/)
  assert.equal(render(CandidateIds, { item: { candidateCareId: null, candidateRef: '' } }).includes('cid-main'), false)
})

test('the Travelled row shows only the Traveller ID plus the passport number as plain text', async () => {
  const chip = render(TravellerIdChip, { candidate: person })
  assert.match(chip, /CI-T-094/)
  assert.doesNotMatch(chip, /CI-078|CND-/)
  assert.equal(render(TravellerIdChip, { candidate: { ...person, travellerId: null } }), '')

  const source = await read('../src/screens/travelled/travelled.tsx')
  const row = source.slice(source.indexOf('function TravelTable'))
  assert.match(row, /<TravellerIdChip candidate=\{row\} \/>/)
  assert.doesNotMatch(row, /passportNumber|row\.airline|row\.pnr|Passport |PNR |Flight/)   // the table shows no passport or flight
  assert.doesNotMatch(row, /primaryId|careIdOf|row\.careId|row\.ref\b|Care India ID/)
})

test('the profile and the application form header carry the Data Bank ID only', async () => {
  const profile = await read('../src/screens/candidates/candidate-profile.tsx')
  assert.match(profile, /<span className="cid-main">\{primaryId\(cand\)\}<\/span><\/p>/)
  assert.doesNotMatch(profile, /secondaryId|TravellerIdChip|cid-ref/)
  const form = await read('../src/screens/candidates/application-form.tsx')
  assert.match(form, /candidateSubtitle\(careIdOf\(ids\) \? '' : ids\.ref, form\?\.assignment\)/)
  assert.doesNotMatch(form, /TravellerIdChip|travellerId/)
})
