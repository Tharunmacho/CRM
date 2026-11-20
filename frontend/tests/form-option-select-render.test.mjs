import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Render the real FormOptionSelect (outside the store, so nobody is an Admin or MD): the managed list, the clearing row, no "+ Add an option".
const root = fileURLToPath(new URL('../', import.meta.url))
const bundle = await build({
  entryPoints: [path.join(root, 'src/features/application-form/components/form-option-select.tsx')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  alias: { '@': path.join(root, 'src') },
  external: ['react', 'react-dom', 'react/jsx-runtime', 'lucide-react'],
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const { FormOptionSelect } = compiled.exports
const render = (props) => renderToStaticMarkup(React.createElement(FormOptionSelect, { onChange: () => {}, ...props }))

test('Source shows the managed list with a first row that clears it', () => {
  const html = render({ category: 'candidate_source', value: 'referral', allowClear: true, clearLabel: 'Select a source', allowAdd: true, label: 'Source' })
  assert.match(html, /<option value="">Select a source<\/option>/)
  assert.match(html, /<option value="walk_in">Walk-in<\/option>/)
  assert.match(html, /<option value="referral" selected="">Referral \/ agency<\/option>/)
  assert.ok(html.indexOf('Select a source') < html.indexOf('Walk-in'), 'the clearing row comes first')
})

test('nobody but an Admin or MD is offered "+ Add an option"', () => {
  assert.doesNotMatch(render({ category: 'job_duration_preference', value: '', allowAdd: true }), /Add an option/)
  assert.doesNotMatch(render({ category: 'candidate_source', value: 'other', allowAdd: true }), /Add an option/)
})

test('a saved value that is no longer offered stays visible and is marked', () => {
  const html = render({ category: 'job_duration_preference', value: '7 years', allowClear: true })
  assert.match(html, /<option value="7 years" selected="">7 years \(no longer offered\)<\/option>/)
  assert.doesNotMatch(render({ category: 'job_duration_preference', value: '' }), /<option value="">/, 'no clearing row unless asked for')
})
