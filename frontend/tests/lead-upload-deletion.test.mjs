import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { afterEach, test } from 'node:test'
import ts from 'typescript'

const cache = new Map()
async function loadSource(path) {
  const url = new URL(path, import.meta.url)
  if (cache.has(url.href)) return cache.get(url.href)
  let source = await readFile(url, 'utf8')
  for (const spec of [...source.matchAll(/from '([^']+)'/g)].map(match => match[1])) {
    if (spec === '@/shared/api/client' || spec === './lead-model') {
      const child = await loadSource(spec === './lead-model' ? '../src/features/leads/lead-model.ts' : '../src/shared/api/client.ts')
      source = source.replaceAll(`from '${spec}'`, `from '${child.__url}'`)
    }
  }
  const js = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  const moduleUrl = `data:text/javascript;base64,${Buffer.from(js).toString('base64')}`
  const module = { ...(await import(moduleUrl)), __url: moduleUrl }
  cache.set(url.href, module)
  return module
}
const { leadApi } = await loadSource('../src/features/leads/lead-api.ts')
const { api, ApiError, invalidateReads } = await loadSource('../src/shared/api/client.ts')
const realFetch = globalThis.fetch
afterEach(() => { globalThis.fetch = realFetch; invalidateReads() })
const preview = { batchId: 'batch/synthetic', fileName: 'Synthetic.xlsx', leadCount: 30, protectedCount: 0,
  duplicateRows: 2, updatedExistingRows: 1, preservedExistingLeadCount: 1, canDelete: true, reasons: [], previewToken: 'reviewed-snapshot' }
const success = { deleted: true, batchId: preview.batchId, deletedLeads: 30, deletedActivities: 0,
  deletedFollowUps: 0, deletedAttachments: 0, deletedExtractionJobs: 0, duplicateRows: 2, preservedExistingLeadCount: 1 }

test('permanent upload deletion sends the exact reviewed count, token and literal confirmation', async () => {
  const requests = []
  globalThis.fetch = async (url, init) => {
    requests.push({ url, ...init })
    return Response.json(init.method === 'GET' ? preview : success)
  }
  const checked = await leadApi.deletionPreview(preview.batchId)
  assert.deepEqual(checked, preview)
  assert.deepEqual(await leadApi.permanentlyDeleteBatch(checked, 'DELETE'), success)
  assert.equal(requests[0].url, '/api/leads/batches/batch%2Fsynthetic/deletion-preview')
  assert.equal(requests[0].method, 'GET')
  assert.equal(requests[1].url, '/api/leads/batches/batch%2Fsynthetic/permanent')
  assert.equal(requests[1].method, 'DELETE')
  assert.deepEqual(JSON.parse(requests[1].body), { previewToken: 'reviewed-snapshot', expectedLeadCount: 30, confirm: 'DELETE' })
  assert.equal(requests[1].credentials, 'include')
  assert.equal(requests[1].headers['Content-Type'], 'application/json')
  assert.equal(preview.leadCount, 30)
})

test('an uncertain response can retry the same request; missing upload is never returned as success', async () => {
  const bodies = []
  globalThis.fetch = async (_url, init) => {
    bodies.push(init.body)
    if (bodies.length === 1) return Response.json({ detail: 'Result unavailable' }, { status: 503 })
    return Response.json(success)
  }
  await assert.rejects(leadApi.permanentlyDeleteBatch(preview, 'DELETE'), error => error instanceof ApiError && error.status === 503)
  assert.deepEqual(await leadApi.permanentlyDeleteBatch(preview, 'DELETE'), success)
  assert.equal(bodies[0], bodies[1])
  globalThis.fetch = async () => Response.json({ detail: 'Upload not found' }, { status: 404 })
  await assert.rejects(leadApi.permanentlyDeleteBatch(preview, 'DELETE'), error => error instanceof ApiError && error.status === 404)
})

test('a stale preview remains a conflict and bodyless DELETE callers remain unchanged', async () => {
  globalThis.fetch = async () => Response.json({ detail: { code: 'preview_changed', message: 'Refresh the deletion preview.' } }, { status: 409 })
  await assert.rejects(leadApi.permanentlyDeleteBatch(preview, 'DELETE'), error => error instanceof ApiError && error.status === 409 && error.detail.code === 'preview_changed')
  let options
  globalThis.fetch = async (_url, init) => { options = init; return new Response(null, { status: 204 }) }
  await api.del('/synthetic/legacy')
  assert.equal(options.method, 'DELETE')
  assert.equal(options.body, undefined)
})

test('DELETE with a confirmation body invalidates cached lead reads while retaining unrelated copies', async () => {
  const reads = []
  globalThis.fetch = async (url, init) => {
    if (init.method === 'GET') { reads.push(url); return Response.json({ count: reads.length }) }
    return Response.json(success)
  }
  const cache = { ttlMs: 60_000 }
  await api.get('/leads/summary', { cache })
  await api.get('/clients', { cache })
  await api.get('/leads/summary', { cache })
  assert.equal(reads.length, 2)
  await leadApi.permanentlyDeleteBatch(preview, 'DELETE')
  await api.get('/leads/summary', { cache })
  await api.get('/clients', { cache })
  assert.deepEqual(reads, ['/api/leads/summary', '/api/clients', '/api/leads/summary'])
})
