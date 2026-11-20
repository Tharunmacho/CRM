import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { afterEach, test } from 'node:test'
import { build } from 'esbuild'
import ts from 'typescript'
import { withRoleLabels } from './fixtures/role-labels-url.mjs'

const here = (path) => fileURLToPath(new URL(path, import.meta.url))
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
async function load(path) {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`)
}
/** Like `load`, for a model that imports its neighbours with relative paths (each is loaded first and its data: URL swapped in). */
const deep = new Map()
async function loadDeep(path) {
  if (deep.has(path)) return deep.get(path)
  const url = new URL(path, import.meta.url)
  let source = await readFile(url, 'utf8')
  for (const spec of [...source.matchAll(/from '(\.[^']+)'/g)].map((match) => match[1])) {
    const loaded = await loadDeep(new URL(`${spec}.ts`, url).href)
    source = source.replaceAll(`from '${spec}'`, `from '${loaded.__url}'`)
  }
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  const dataUrl = `data:text/javascript;base64,${Buffer.from(withRoleLabels(js)).toString('base64')}`
  const loaded = { ...(await import(dataUrl)), __url: dataUrl }
  deep.set(path, loaded)
  return loaded
}
/** A source file without its comments, so a rule about rendered words is not tripped by a comment that explains it. */
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

const model = await load('../src/features/leads/lead-model.ts')
const status = await load('../src/features/leads/lead-status-model.ts')

const row = (name, tone = 'neutral', extra = {}) => ({ id: `id-${name}`, name, tone, active: true, builtin: false, sortOrder: 100, ...extra })
const SEEDED = [row('Ringing', 'warning', { builtin: true, sortOrder: 10 }), row('No incoming calls', 'warning', { builtin: true, sortOrder: 20 }), row('Not eligible', 'negative', { builtin: true, sortOrder: 30 })]

/* ------------------------------------------------------------------ the model */

test('the three new statuses and the templates sit after the standard list: built-in first, the rest A to Z', () => {
  const options = model.statusOptions([row('Warm lead'), ...SEEDED, row('busy later'), row('Visa ready', 'positive')])
  const standard = model.SETTABLE_STATUSES.map((s) => s.value)
  assert.deepEqual(options.map((o) => o.value), [...standard, 'Ringing', 'No incoming calls', 'Not eligible', 'busy later', 'Visa ready', 'Warm lead'])
  assert.deepEqual(options.slice(0, standard.length).map((o) => o.kind), standard.map(() => 'builtin'))
  assert.ok(options.slice(standard.length).every((o) => o.kind === 'template'))
  assert.ok(!options.some((o) => o.value === 'converted'), 'converted is never chosen by hand')
  assert.equal(standard[0], 'new', 'the usual order is untouched')
})

test('a hidden template is not offered, but a lead that holds it keeps it in its own select', () => {
  const list = [...SEEDED, row('Busy later', 'neutral', { active: false })]
  assert.ok(!model.statusOptions(list).some((o) => o.value === 'Busy later'))
  const own = model.statusOptions(list, { status: 'contacted', statusLabel: 'Busy later' })
  assert.equal(own.at(-1).value, 'Busy later')
  assert.equal(own.filter((o) => o.value === 'Busy later').length, 1)
  const deleted = model.statusOptions(SEEDED, { status: 'contacted', statusLabel: 'Gone status' })
  assert.deepEqual([deleted.at(-1).value, deleted.at(-1).kind], ['Gone status', 'template'])
  assert.equal(model.statusOptions(SEEDED, { status: 'contacted', statusLabel: 'ringing' }).filter((o) => o.value.toLowerCase() === 'ringing').length, 1, 'no duplicate for another letter case')
})

test('a lead shows its template name; the chip tone is the template\'s, else the tone of the code it counts as', () => {
  assert.deepEqual(model.leadStatus('contacted', null), model.leadStatus('contacted'))
  assert.deepEqual(model.leadStatus('contacted', '  '), model.leadStatus('contacted'))
  model.registerStatusTemplates([row('Ringing', 'warning'), row('Visa ready', 'positive'), row('Not eligible', 'negative'), row('Busy later', 'neutral')])
  assert.deepEqual(model.leadStatus('contacted', 'Ringing'), { value: 'Ringing', label: 'Ringing', tone: 'warn' })
  assert.equal(model.leadStatus('interested', 'visa ready').tone, 'ok')
  assert.equal(model.leadStatus('not_interested', 'Not eligible').tone, 'bad')
  assert.equal(model.leadStatus('contacted', 'Busy later').tone, 'info')
  assert.equal(model.leadStatus('contacted', 'Visa ready').label, 'Visa ready', 'the spelling of the template is kept, never re-capitalised')
  model.registerStatusTemplates([])
  assert.equal(model.leadStatus('not_interested', 'Old custom').tone, 'quiet', 'template gone: the tone of the built-in code')
  assert.equal(model.leadStatus('interested', 'old custom').label, 'old custom')
  assert.equal(model.templateTone('warning'), 'warn')
  assert.equal(model.templateTone('nonsense'), 'info')
})

test('the select value of a lead is its template name, else its code, and is spoken as words', () => {
  assert.equal(model.statusPick({ status: 'contacted', statusLabel: 'Ringing' }), 'Ringing')
  assert.equal(model.statusPick({ status: 'contacted', statusLabel: null }), 'contacted')
  assert.equal(model.statusPick({ status: 'contacted' }), 'contacted')
  assert.equal(model.pickLabel('wrong_number'), 'Wrong number')
  assert.equal(model.pickLabel('visa ready'), 'visa ready')
  model.registerStatusTemplates([row('Ringing', 'warning'), row('Busy later', 'neutral')])
  assert.equal(model.isAttemptLabel('ringing'), true)
  assert.equal(model.isAttemptLabel('Busy later'), false)
  assert.equal(model.isAttemptLabel(null), false)
  model.registerStatusTemplates([])
})

test('the filter list comes from the facets with counts, and keeps the current value', () => {
  assert.deepEqual(model.statusChoices('').map(([value]) => value), model.LEAD_STATUSES.map((s) => s.value), 'without facets the list is the standard one')
  const statuses = [
    { value: 'new', label: 'Not contacted', count: 3, kind: 'builtin' }, { value: 'contacted', label: 'Contacted', count: 1500, kind: 'builtin' },
    { value: 'resume_received', label: 'Resume received', count: 0, kind: 'builtin' },
    { value: 'Ringing', label: 'Ringing', count: 2, kind: 'template', tone: 'warning', builtin: true }, { value: 'Visa ready', label: 'Visa ready', count: 0, kind: 'template', tone: 'positive' },
  ]
  const choices = model.statusChoices('', { statuses })
  assert.deepEqual(choices, [['new', 'Not contacted (3)'], ['contacted', 'Contacted (1,500)'], ['resume_received', 'Interested (resume in) (0)'], ['Ringing', 'Ringing (2)'], ['Visa ready', 'Visa ready (0)']])
  assert.equal(model.statusChoices('Ringing', { statuses }).length, 5)
  const pair = model.statusChoices('interested,resume_received', { statuses })
  assert.equal(pair.length, 6)
  assert.equal(pair.at(-1)[1], 'Interested or Interested (resume in)')
  assert.deepEqual(model.statusChoices('', { statuses: [] }).map(([v]) => v), model.LEAD_STATUSES.map((s) => s.value), 'an empty list falls back to the standard one')
})

test('names: trimmed, 40 characters, no comma, never a standard status, unique ignoring case', () => {
  const standard = [...model.LEAD_STATUSES.flatMap((s) => [s.value, s.label]), 'Resume received']
  const check = (name, tone = 'neutral') => status.newStatusCheck(name, tone, [...SEEDED, row('Busy later')], standard)
  assert.deepEqual(check('  Call   tomorrow  '), { ok: true, name: 'Call tomorrow', tone: 'neutral' })
  assert.equal(check('Call tomorrow', 'loud').tone, 'neutral', 'an unknown tone becomes neutral')
  assert.equal(check('x', 'positive').tone, 'positive')
  assert.equal(check('x'.repeat(40)).ok, true)
  assert.match(check('x'.repeat(41)).message, /under 40 characters/)
  assert.match(check('   ').message, /Write the status name/)
  assert.match(check('Busy, later').message, /comma/)
  for (const taken of ['busy later', 'BUSY   LATER', 'ringing', 'Not Eligible']) assert.match(check(taken).message, /already in the list/, taken)
  for (const standardName of ['Contacted', 'wrong number', 'Interested (resume in)', 'not_interested', 'Resume received']) assert.match(check(standardName).message, /standard statuses/, standardName)
  assert.equal(status.NAME_MAX, 40)
  assert.deepEqual(status.STATUS_TONES.map((t) => t.value), ['neutral', 'positive', 'warning', 'negative'])
  assert.deepEqual(status.COUNTS_AS, { neutral: 'contacted', positive: 'interested', warning: 'contacted', negative: 'not_interested' })
})

test('the list from the server is cleaned and ordered; the optimistic row is shown, taken back or settled', () => {
  const list = status.normalizeStatusTemplates({ items: [
    { id: '3', name: ' Warm  lead ', tone: 'weird', active: true, builtin: false, sortOrder: 110 }, { id: '2', name: 'Not eligible', tone: 'negative', builtin: true, sortOrder: 30, countsAs: 'not_interested' },
    { id: '1', name: 'Ringing', tone: 'warning', builtin: true, sortOrder: 10 }, { id: '4', name: 'Busy later', tone: 'neutral', active: false }, { id: '', name: 'No id' }, null, { id: '9', name: '   ' },
  ] })
  assert.deepEqual(list.map((t) => t.name), ['Ringing', 'Not eligible', 'Busy later', 'Warm lead'])
  assert.equal(list.find((t) => t.name === 'Warm lead').tone, 'neutral')
  assert.equal(list.find((t) => t.name === 'Busy later').active, false)
  assert.deepEqual(status.normalizeStatusTemplates('nope'), [])
  assert.deepEqual(status.activeStatusTemplates(list).map((t) => t.name), ['Ringing', 'Not eligible', 'Warm lead'])

  const pending = { ...row('Alpha'), id: 'pending:1', sortOrder: 9e15 }
  const shown = status.withPendingStatus(list, pending)
  assert.deepEqual(shown.map((t) => t.name), ['Ringing', 'Not eligible', 'Alpha', 'Busy later', 'Warm lead'])
  assert.equal(shown.find((t) => t.name === 'Alpha').pending, true)
  assert.deepEqual(status.withoutStatus(shown, 'pending:1').map((t) => t.name), list.map((t) => t.name))
  const settled = status.settleStatus(shown, 'pending:1', row('Alpha', 'positive', { id: 'real-1' }))
  assert.deepEqual(settled.filter((t) => t.name === 'Alpha').map((t) => [t.id, t.pending]), [['real-1', undefined]])
  assert.equal(status.existingFromError({ code: 'status_exists', existing: { id: 'e', name: 'Busy later', tone: 'neutral', active: true } }).name, 'Busy later')
  assert.equal(status.existingFromError({ code: 'invalid_request' }), null)
  assert.equal(status.existingFromError('text'), null)
})

test('undo and the timeline speak in the template\'s name', async () => {
  const bulk = await load('../src/features/leads/lead-bulk-model.ts')
  assert.deepEqual(bulk.undoEntry({ id: 'l1', name: 'A', status: 'contacted', statusLabel: 'Ringing' }, 'interested', 1), { leadId: 'l1', name: 'A', from: 'Ringing', to: 'interested', at: 1 })
  assert.equal(bulk.undoEntry({ id: 'l1', status: 'contacted', statusLabel: 'Ringing' }, 'Ringing'), null, 'the same choice is not a change')
  assert.equal(bulk.undoEntry({ id: 'l1', status: 'contacted' }, 'Ringing').from, 'contacted')
  assert.deepEqual(bulk.undoBody(bulk.undoEntry({ id: 'l1', status: 'contacted', statusLabel: 'Ringing' }, 'interested')), { status: 'Ringing' })
  assert.equal(bulk.statusToast('Ringing', model.pickLabel), 'Status changed to Ringing.')
  const remarks = await loadDeep('../src/features/leads/lead-remarks-model.ts')
  const event = remarks.timelineEvent({ id: 'a', kind: 'status', summary: '', userName: 'Asha', createdAt: '2026-11-20T10:00:00Z', meta: { from: 'new', to: 'contacted', toLabel: 'Ringing', fromLabel: null } })
  assert.equal(event.title, 'Changed status from Not contacted to Ringing')
  const plain = remarks.timelineEvent({ id: 'b', kind: 'status', summary: '', userName: 'Asha', createdAt: '2026-11-20T10:00:00Z', meta: { from: 'contacted', to: 'interested' } })
  assert.equal(plain.title, 'Changed status from Contacted to Interested')
})

/* ------------------------------------------------------------------ the add flow, with a fake server */

const bundle = await build({
  stdin: {
    contents: "export * from '@/features/leads/lead-status-templates-api'; export { leadStatus } from '@/features/leads/lead-model'",
    resolveDir: here('../'), loader: 'ts',
  },
  bundle: true, write: false, platform: 'node', format: 'cjs', logLevel: 'silent', alias: { '@': here('../src') },
  plugins: [{ name: 'boundaries', setup(plugin) {
    plugin.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }))
    plugin.onResolve({ filter: /^@\/shared\/state\/live$/ }, () => ({ path: 'live', namespace: 'fixture' }))
    plugin.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ contents: path === 'react'
      ? 'export const useEffect=()=>{}; export const useSyncExternalStore=(subscribe, get)=>get();'
      : 'export const live={subscribe:()=>()=>{}};' }))
  } }],
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const api = compiled.exports

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

/** A fake server holding the templates; `calls` records every request. */
function fakeServer(initial = SEEDED, options = {}) {
  const items = initial.map((item) => ({ ...item }))
  const calls = []
  let release = null
  const respond = (status, body) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  globalThis.fetch = async (url, init = {}) => {
    const method = init.method ?? 'GET'
    const body = init.body ? JSON.parse(init.body) : undefined
    calls.push({ method, url, body })
    if (method === 'GET') return respond(200, { items: items.filter((i) => i.active || String(url).includes('all=1')) })
    if (method === 'POST') {
      if (options.hold) { options.hold = false; await new Promise((resolve) => { release = resolve }) }   // only the first add waits
      if (options.refuse) return respond(options.refuse.status, { detail: options.refuse.detail })
      const created = { id: `srv-${items.length + 1}`, name: body.name, tone: body.tone ?? 'neutral', active: true, builtin: false, sortOrder: 110, countsAs: 'contacted' }
      items.push(created)
      return respond(201, created)
    }
    const id = decodeURIComponent(String(url).split('/').pop())
    const found = items.find((i) => i.id === id)
    if (!found) return respond(404, { detail: { code: 'not_found', message: 'Status template not found.' } })
    if (method === 'DELETE') { items.splice(items.indexOf(found), 1); return respond(200, { ...found, deleted: true }) }
    Object.assign(found, body)
    return respond(200, found)
  }
  return { items, calls, release: () => release?.() }
}

test('adding a status: it is in the list at once, then replaced by the server\'s row, and everybody reads it afterwards', async () => {
  const server = fakeServer(SEEDED, { hold: true })
  await api.refreshLeadStatusTemplates()
  assert.deepEqual(api.currentLeadStatusTemplates().items.map((t) => t.name), ['Ringing', 'No incoming calls', 'Not eligible'])
  const adding = api.addLeadStatus('Busy later', 'neutral')
  await new Promise((resolve) => setTimeout(resolve, 10))
  const during = api.currentLeadStatusTemplates().items.find((t) => t.name === 'Busy later')
  assert.ok(during && during.pending === true, 'shown while the request is on its way')
  server.release()
  const saved = await adding
  assert.equal(saved.id, 'srv-4')
  const after = api.currentLeadStatusTemplates().items.filter((t) => t.name === 'Busy later')
  assert.deepEqual(after.map((t) => [t.id, t.pending]), [['srv-4', undefined]])
  assert.deepEqual(server.calls.filter((c) => c.method === 'POST').map((c) => [c.url, c.body]), [['/api/lead-status-templates', { name: 'Busy later', tone: 'neutral' }]])
  assert.equal(api.leadStatus('contacted', 'Busy later').tone, 'info', 'chips find the new status\' tone')
  await api.addLeadStatus('Visa ready', 'positive')
  assert.equal(api.leadStatus('interested', 'Visa ready').tone, 'ok')
  assert.deepEqual(api.currentLeadStatusTemplates().items.map((t) => t.name), ['Ringing', 'No incoming calls', 'Not eligible', 'Busy later', 'Visa ready'])
})

test('a refused add is taken out of the list again and the server\'s words are thrown', async () => {
  fakeServer(SEEDED, { refuse: { status: 422, detail: { code: 'invalid_request', message: 'A status name cannot contain a comma.' } } })
  await api.refreshLeadStatusTemplates()
  await assert.rejects(api.addLeadStatus('Nope', 'neutral'), (error) => { assert.equal(error.message, 'A status name cannot contain a comma.'); return true })
  assert.ok(!api.currentLeadStatusTemplates().items.some((t) => t.name === 'Nope'))
  assert.equal(api.currentLeadStatusTemplates().items.length, 3)
})

test('a name somebody else just added is not an error: that status is used; a hidden duplicate is refused', async () => {
  const existing = row('Busy later', 'neutral', { id: 'e1' })
  fakeServer(SEEDED, { refuse: { status: 409, detail: { code: 'status_exists', message: 'A status named "Busy later" already exists', existing } } })
  await api.refreshLeadStatusTemplates()
  const used = await api.addLeadStatus('busy later', 'neutral')
  assert.equal(used.id, 'e1')
  assert.deepEqual(api.currentLeadStatusTemplates().items.filter((t) => t.name.toLowerCase() === 'busy later').map((t) => t.id), ['e1'])
  fakeServer(SEEDED, { refuse: { status: 409, detail: { code: 'status_exists', message: 'A status named "Old one" already exists but is switched off. Ask an admin to switch it on.', existing: row('Old one', 'neutral', { active: false }) } } })
  await api.refreshLeadStatusTemplates()
  await assert.rejects(api.addLeadStatus('Old one', 'neutral'), /switched off/)
  assert.ok(!api.currentLeadStatusTemplates().items.some((t) => t.name === 'Old one'))
})

test('a network failure leaves the list as it was and says so', async () => {
  fakeServer()
  await api.refreshLeadStatusTemplates()
  globalThis.fetch = async () => { throw new TypeError('network down') }
  await assert.rejects(api.addLeadStatus('Busy later', 'neutral'), /Cannot reach the server/)
  assert.equal(api.currentLeadStatusTemplates().items.length, 3)
})

test('admin changes go to the right endpoints and the shared list is read again', async () => {
  const server = fakeServer([...SEEDED, row('Busy later', 'neutral', { id: 'b1' })])
  await api.refreshLeadStatusTemplates()
  assert.equal((await api.listAllLeadStatusTemplates()).length, 4)
  await api.renameLeadStatus('b1', 'Call tomorrow')
  await api.setLeadStatusActive('b1', false)
  assert.ok(!api.currentLeadStatusTemplates().items.some((t) => t.id === 'b1'), 'a hidden status leaves the shared list')
  await api.deleteLeadStatus('b1')
  const writes = server.calls.filter((c) => c.method !== 'GET').map((c) => [c.method, c.url, c.body])
  assert.deepEqual(writes, [['PATCH', '/api/lead-status-templates/b1', { name: 'Call tomorrow' }], ['PATCH', '/api/lead-status-templates/b1', { active: false }], ['DELETE', '/api/lead-status-templates/b1', undefined]])
  assert.ok(server.calls.some((c) => c.url.includes('all=1')))
})

/* ------------------------------------------------------------------ the screens use it */

test('every place a status is chosen offers the same list with "+ Add a status" last', async () => {
  const select = await code('../src/features/leads/components/status-select.tsx')
  assert.match(select, /useLeadStatusTemplates\(\)/)
  assert.match(select, /statusOptions\(templates\.items, lead\)/)
  assert.ok(select.lastIndexOf('ADD_STATUS_LABEL') > select.indexOf('options.map'), 'the add row comes after the list')
  assert.match(select, /allowAdd &&/)
  assert.match(select, /event\.key === 'Escape'/)
  assert.match(select, /role="group" aria-label="Add a status"/)
  assert.match(select, /maxLength=\{NAME_MAX\}/)
  assert.equal(status.ADD_STATUS_LABEL, '+ Add a status')
  for (const file of ['lead-edit-dialog.tsx', 'lead-drawer.tsx', 'bulk-dialogs.tsx']) {
    const source = await code(`../src/features/leads/components/${file}`)
    assert.match(source, /<StatusSelect /, file)
    assert.doesNotMatch(source, /SETTABLE_STATUSES/, `${file} no longer builds its own status list`)
  }
  assert.match(await code('../src/features/leads/components/lead-filters.tsx'), /statusChoices\(filters\.status, facets\)/)
  assert.match(await code('../src/features/leads/components/lead-columns.tsx'), /leadStatus\(lead\.status, lead\.statusLabel\)/)
  assert.match(await code('../src/features/leads/lead-fields.ts'), /leadStatus\(l\.status, l\.statusLabel\)/)
  assert.match(await code('../src/screens/leads/leads.tsx'), /StatusManageDialog/)
})

test('the manage list is for admin and MD and never offers rename or delete for a built-in status', async () => {
  const manage = await code('../src/features/leads/components/status-manage-dialog.tsx')
  assert.match(manage, /!item\.builtin && \(confirmDelete/)
  assert.match(manage, /!item\.builtin && <button type="button" disabled=\{Boolean\(busyId\)\} onClick=\{\(\) => setEditing/)
  assert.match(manage, /Delete for everyone/)
  assert.match(await code('../src/screens/leads/leads.tsx'), /canAssign && <StatusManageDialog/)
  assert.match(await code('../src/screens/leads/leads.tsx'), /canAssign && <Button variant="secondary" icon=\{<ListChecks/)
})

test('phone width: the add box becomes one column', async () => {
  const css = await read('../src/styles/lead-status.css')
  assert.match(css, /@media \(max-width: 760px\)[\s\S]*\.ld-addstatus \{ grid-template-columns: minmax\(0, 1fr\); \}/)
})
