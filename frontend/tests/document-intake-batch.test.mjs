import assert from 'node:assert/strict'
import { test } from 'node:test'
import { build } from 'esbuild'
import { createRequire } from 'node:module'

const bundle = await build({
  entryPoints: ['src/features/candidates/use-document-intake.ts'], bundle: true, write: false, platform: 'node', format: 'cjs',
  plugins: [{ name: 'intake-boundaries', setup(plugin) {
    plugin.onResolve({ filter: /^react$/ }, () => ({ path: 'react', namespace: 'fixture' }))
    plugin.onResolve({ filter: /^@\/shared\/api\/client$/ }, () => ({ path: 'api', namespace: 'fixture' }))
    plugin.onLoad({ filter: /.*/, namespace: 'fixture' }, ({ path }) => ({ contents: path === 'react'
      ? 'export const useEffect=()=>{}; export const useMemo=f=>f(); export const useRef=v=>({current:v}); export const useState=v=>[typeof v==="function"?v():v,()=>{}];'
      : 'export const api={post:(...a)=>globalThis.__intakeApi.post(...a),get:(...a)=>globalThis.__intakeApi.get(...a),put:async()=>({})}; export const fileHref=p=>p; export class ApiError extends Error {constructor(status,message){super(message);this.status=status}};' }))
  } }],
})
const compiled = { exports: {} }
new Function('require', 'module', 'exports', bundle.outputFiles[0].text)(createRequire(import.meta.url), compiled, compiled.exports)
const { useDocumentIntake } = compiled.exports

test('uploads wait for explicit extraction; later files are excluded from the running batch and a retry is scoped', async () => {
  const jobs = []
  const started = []
  const originalFetch = globalThis.fetch
  let duringRead
  globalThis.fetch = async (_url, init) => {
    const id = `file-${jobs.length + 1}`
    const job = { id, groupId: 'group', kind: init.body.get('kind'), filename: init.body.get('file').name,
      sizeKb: 1, status: 'uploaded', createdAt: '2026-11-12T00:00:00Z', updatedAt: '2026-11-12T00:00:00Z' }
    jobs.push(job)
    return { ok: true, json: async () => structuredClone(job) }
  }
  globalThis.__intakeApi = {
    post: async path => {
      const id = path.split('/').at(-2)
      started.push(id)
      const job = jobs.find(row => row.id === id)
      job.status = 'pending'
      return structuredClone(job)
    },
    get: async () => {
      if (duringRead) { const action = duringRead; duringRead = null; await action() }
      for (const job of jobs) if (job.status === 'pending') job.status = 'completed'
      return structuredClone(jobs)
    },
  }
  const upload = (intake, name) => intake.upload('resume', new File(['synthetic'], name, { type: 'application/pdf' }), { many: true })
  try {
    const intake = useDocumentIntake('synthetic-owner')
    await upload(intake, 'passport-front.pdf')
    await upload(intake, 'passport-back.pdf')
    assert.deepEqual(started, [], 'adding files does not start the reader')
    assert.equal(intake.hasUnqueued(), true)
    duringRead = () => upload(intake, 'extra-page.pdf')
    const first = await intake.read(new AbortController().signal)
    assert.deepEqual(started, ['file-1', 'file-2'])
    assert.equal(first.find(job => job.id === 'file-3').status, 'uploaded', 'a later upload waits for the next explicit start')
    await intake.read(new AbortController().signal)
    assert.deepEqual(started, ['file-1', 'file-2', 'file-3'], 'completed files are not resubmitted')
    await upload(intake, 'another-document.pdf')
    await intake.retry('file-1')
    await intake.read(new AbortController().signal, { retryFailed: false, jobIds: ['file-1'] })
    assert.equal(started.includes('file-4'), false, 'retrying one file does not queue unrelated saved files')
    assert.equal(intake.hasUnqueued(), true)
  } finally {
    globalThis.fetch = originalFetch
    delete globalThis.__intakeApi
  }
})
