import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { StaticRouter } from 'react-router-dom/server.js'

const root = fileURLToPath(new URL('../', import.meta.url))
const bundle = await build({
  stdin: { contents: `
    export { SourceLine, dataBankColumns, bankContext } from './src/features/candidates/components/data-bank-columns'
    export { DataBankCards } from './src/features/candidates/components/data-bank-cards'
  `, resolveDir: root, loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  alias: { '@': path.join(root, 'src') },
  external: ['react', 'react-dom', 'react/jsx-runtime', 'react-router-dom', 'lucide-react'],
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const { SourceLine, dataBankColumns, bankContext, DataBankCards } = compiled.exports
const store = { me: { id: 'admin', role: 'admin' }, can: () => false, user: () => undefined, applicationsFor: () => [] }
const handlers = { onOpen() {}, onReassign() {}, onEdit() {}, onOpenStage() {} }
const candidate = { id: 'candidate', name: 'Candidate', ref: 'REF-1', phone: '', source: 'other', sourceDetail: '', status: 'new', ownerId: null }
const renderSource = change => renderToStaticMarkup(React.createElement(SourceLine, { candidate: { ...candidate, ...change } }))

test('the default inventory columns show Source instead of Stage and keep allocation fields optional', () => {
  const columns = dataBankColumns({ store, ...handlers })
  assert.deepEqual(columns.filter(column => !column.off).map(column => column.header),
    ['Candidate', 'Contact', 'Source', 'Agent', 'Recruiter', 'Status', 'Actions'])
  const source = columns.find(column => column.key === 'source')
  assert.equal(source.optional, undefined)
  assert.equal(source.sortKey, 'source')
  for (const key of ['f:stage', 'f:order']) assert.equal(columns.find(column => column.key === key)?.off, true)
})

test('source cells show all intake channels and only human provenance for Other', () => {
  for (const [source, label] of Object.entries({ email: 'Email', whatsapp: 'WhatsApp', walk_in: 'Walk-in', referral: 'Referral', other: 'Other' })) {
    assert.match(renderSource({ source }), new RegExp(`>${label}<`))
  }
  const lead = renderSource({ sourceDetail: 'lead_sheet:private-record-id' })
  assert.match(lead, />Other</)
  assert.match(lead, />Lead Sheet</)
  assert.doesNotMatch(lead, /lead_sheet:|private-record-id/)
  assert.match(renderSource({ sourceDetail: 'Bulk import' }), />Bulk import</)
  assert.doesNotMatch(renderSource({ source: 'referral', sourceDetail: 'Al Noor Agency' }), /Al Noor Agency/)
  assert.equal(bankContext(store).sourceLabel('other', 'lead_sheet:private-record-id'), 'Other · Lead Sheet')
})

test('mobile cards show Source instead of Stage and keep the referring agency separate', () => {
  const renderCard = change => renderToStaticMarkup(React.createElement(StaticRouter, null,
    React.createElement(DataBankCards, { rows: [{ ...candidate, ...change }], store, ...handlers })))
  const lead = renderCard({ sourceDetail: 'lead_sheet:private-record-id' })
  assert.match(lead, /<dt>Source<\/dt>/)
  assert.match(lead, />Lead Sheet</)
  assert.doesNotMatch(lead, /<dt>Stage<\/dt>|<dt>Agent<\/dt>|private-record-id/)
  const referral = renderCard({ source: 'referral', sourceDetail: 'Al Noor Agency' })
  assert.match(referral, /<dt>Source<\/dt>/)
  assert.match(referral, />Referral</)
  assert.match(referral, /<dt>Agent<\/dt>/)
  assert.match(referral, />Al Noor Agency</)
})
