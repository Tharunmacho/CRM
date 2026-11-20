import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

function moduleUrl(source) {
  const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
}
async function load(path, imports = {}) {
  let source = await readFile(new URL(path, import.meta.url), 'utf8')
  for (const [from, url] of Object.entries(imports)) source = source.replaceAll(`'${from}'`, `'${url}'`)
  return import(moduleUrl(source))
}

const { expandDesk } = await load('../src/shared/state/desk-wire.ts')
const clientUrl = moduleUrl(`export class ApiError extends Error { constructor(status, message, detail) { super(message); this.status = status; this.detail = detail } }
export const fileHref = (url) => 'https://api.test' + url`)
const { readDesk } = await load('../src/shared/api/desk-read.ts', { './client': clientUrl })

const wire = () => ({
  me: { id: 'u1' },
  revision: 'abc123',
  documentLabels: { passport: 'Passport', resume: 'Resume' },
  rowDefaults: {
    candidates: { travellerId: null, languages: [], totalExperience: 0, resumeText: '', documentCounts: {}, needsReview: false },
    applications: { draft: {}, stages: [], closureReason: null },
    stages: { ownerId: null, outcome: null },
    tasks: { completedAt: null, escalated: false },
  },
  candidates: [
    { id: 'c1', name: 'A', phone: '1', documentCounts: { passport: 2, resume: 1 } },
    { id: 'c2', name: 'B', phone: '2', languages: ['Tamil'], totalExperience: 4 },
  ],
  applications: [
    { id: 'a1', candidateId: 'c1', stages: [{ id: 's1', stage: 1, completedBy: 'u1' }] },
    { id: 'a2', candidateId: 'c2' },
  ],
  tasks: [{ id: 't1', title: 'x' }],
  orders: [{ id: 'o1' }],
})

test('expandDesk puts every trimmed key back, as the server would have sent it whole', () => {
  const { desk, revision } = expandDesk(wire())
  assert.equal(revision, 'abc123')
  assert.deepEqual(desk.candidates[1], {
    id: 'c2', name: 'B', phone: '2', languages: ['Tamil'], totalExperience: 4, travellerId: null, resumeText: '', documentCounts: {}, needsReview: false,
    documents: [], documentsSummary: true,
  })
  assert.deepEqual(desk.applications[1], { id: 'a2', candidateId: 'c2', draft: {}, stages: [], closureReason: null })
  assert.deepEqual(desk.applications[0].stages[0], { id: 's1', stage: 1, completedBy: 'u1', ownerId: null, outcome: null })
  assert.deepEqual(desk.tasks[0], { id: 't1', title: 'x', completedAt: null, escalated: false })
  assert.deepEqual(desk.orders, [{ id: 'o1' }]) // no defaults named for orders: untouched
  assert.equal('rowDefaults' in desk, false)
  assert.equal('documentLabels' in desk, false)
  assert.equal('revision' in desk, false)
})

test('a candidate lists the slots that hold a file, named by the server, without versions', () => {
  const { desk } = expandDesk(wire())
  assert.deepEqual(desk.candidates[0].documents, [
    { key: 'passport', label: 'Passport', versions: [], fileUploaded: true },
    { key: 'resume', label: 'Resume', versions: [], fileUploaded: true },
  ])
  assert.deepEqual(desk.candidates[0].documentCounts, { passport: 2, resume: 1 })
})

test('rows do not share their default lists or objects', () => {
  const { desk } = expandDesk(wire())
  desk.candidates[0].languages.push('Hindi')
  desk.applications[1].draft.x = 1
  assert.deepEqual(desk.candidates[1].languages, ['Tamil'])
  assert.deepEqual(desk.applications[0].draft, {})
  const second = expandDesk(wire()).desk
  assert.deepEqual(second.candidates[0].languages, [])
})

test('a snapshot without rowDefaults (an older server) is returned as it is', () => {
  const old = { me: { id: 'u1' }, candidates: [{ id: 'c1', documents: [{ key: 'passport', versions: [{ id: 'f' }] }] }] }
  const { desk, revision } = expandDesk(old)
  assert.equal(desk, old)
  assert.equal(revision, undefined)
})

const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch })

test('readDesk sends the revision back and treats 304 as unchanged', async () => {
  let seen
  globalThis.fetch = async (url, init) => { seen = { url, init }; return new Response(null, { status: 304 }) }
  const answer = await readDesk('/bootstrap?compact=true&slim=true', { revision: 'abc123' })
  assert.deepEqual(answer, { same: true })
  assert.equal(seen.url, 'https://api.test/api/bootstrap?compact=true&slim=true')
  assert.equal(seen.init.headers['If-None-Match'], '"abc123"')
  assert.equal(seen.init.credentials, 'include')
})

test('readDesk sends no condition without a revision and returns the changed snapshot', async () => {
  let init
  globalThis.fetch = async (_url, options) => { init = options; return Response.json({ me: { id: 'u1' }, revision: 'r2' }) }
  const answer = await readDesk('/bootstrap?compact=true&slim=true')
  assert.equal(init.headers, undefined)
  assert.deepEqual(answer, { same: false, payload: { me: { id: 'u1' }, revision: 'r2' } })
})

test('readDesk reports a refusal with its status, and an unreachable server as status 0', async () => {
  globalThis.fetch = async () => Response.json({ detail: 'Session expired' }, { status: 401 })
  await assert.rejects(readDesk('/bootstrap?compact=true&slim=true'), (error) => error.status === 401 && error.message === 'Session expired')
  globalThis.fetch = async () => { throw new TypeError('network') }
  await assert.rejects(readDesk('/bootstrap?compact=true&slim=true'), (error) => error.status === 0)
})

test('readDesk passes a caller cancellation through untouched', async () => {
  const controller = new AbortController()
  globalThis.fetch = (_url, init) => new Promise((_resolve, reject) => init.signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))))
  const pending = readDesk('/bootstrap?compact=true&slim=true', { signal: controller.signal })
  controller.abort()
  await assert.rejects(pending, (error) => error.name === 'AbortError')
})
