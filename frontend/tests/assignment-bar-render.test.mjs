import assert from 'node:assert/strict'
import { createRequire } from 'node:module'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'
import { build } from 'esbuild'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'

// Render the actual AssignmentBar and Select together: a missing selected option
// previously made the visible trigger display the first eligible recruiter.
const root = fileURLToPath(new URL('../', import.meta.url))
const bundle = await build({
  entryPoints: [path.join(root, 'src/features/application-form/components/assignment-bar.tsx')],
  bundle: true, write: false, platform: 'node', format: 'cjs', jsx: 'automatic',
  alias: { '@': path.join(root, 'src') },
  external: ['react', 'react-dom', 'react/jsx-runtime', 'lucide-react'],
  loader: { '.css': 'empty' }, logLevel: 'silent',
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const { AssignmentBar } = compiled.exports
const options = [{ id: 'manager', name: 'Manager', role: 'manager' }]
function render(recruiter, recruiters = options) {
  return renderToStaticMarkup(React.createElement(AssignmentBar, {
    assignment: { client: null, job: null, source: { key: 'other', label: 'Other' }, agency: null, recruiter,
      can: { job: false, source: false, agency: false, recruiter: true }, options: { clients: [], agencies: [], recruiters } },
    saving: {}, errors: {}, onChange: () => { throw new Error('Rendering must not reassign a candidate') },
  }))
}
const triggerText = html => html.match(/<button[^>]*aria-label="Recruiter"[^>]*>([\s\S]*?)<\/button>/)?.[1].replace(/<[^>]+>/g, '')

test('saved owner absent from eligible recruiters remains visible as a disabled current option', () => {
  const html = render({ id: 'admin', name: 'Administrator' })
  assert.equal(triggerText(html), 'Administrator')
  assert.match(html, /<option value="admin" disabled="" selected="">Administrator<\/option>/)
  assert.match(html, /<option value="manager">Manager \(manager\)<\/option>/)
})

test('eligible saved recruiter keeps its normal option without a duplicate disabled fallback', () => {
  const html = render({ id: 'manager', name: 'Manager' })
  assert.equal(triggerText(html), 'Manager (manager)')
  assert.equal((html.match(/<option value="manager"/g) ?? []).length, 1)
  assert.match(html, /<option value="manager" selected="">Manager \(manager\)<\/option>/)
})

test('unassigned candidate remains explicitly unassigned while valid choices stay available', () => {
  const html = render(null)
  assert.equal(triggerText(html), 'Not assigned')
  assert.match(html, /<option value="" selected="">Not assigned<\/option>/)
  assert.match(html, /<option value="manager">Manager \(manager\)<\/option>/)
})
