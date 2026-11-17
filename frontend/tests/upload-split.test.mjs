import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

/** A scan that held several documents is filed in their own fields: the form upload hands the server's sentence to the screen. */
globalThis.__splitAnswer = {}
const stubs = {
  '@/shared/api/client': "export const api = { form: () => Promise.resolve(globalThis.__splitAnswer), get: () => Promise.resolve({}), put: () => Promise.resolve({}), post: () => Promise.resolve({}), del: () => Promise.resolve({}) }",
  './form-model': 'export const normalizeForm = (form) => ({ normalized: form })',
}
let source = await readFile(new URL('../src/features/application-form/form-api.ts', import.meta.url), 'utf8')
source = source.replace("import { api, type SplitReport } from '@/shared/api/client'", "import { api } from '@/shared/api/client'")
for (const [spec, code] of Object.entries(stubs)) {
  assert.ok(source.includes(`from '${spec}'`), `form-api.ts no longer imports ${spec}`)
  source = source.replaceAll(`from '${spec}'`, `from 'data:text/javascript;base64,${Buffer.from(code).toString('base64')}'`)
}
source = source.replace(/^import type .*$/m, '')
const js = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
const { uploadFormDocument } = await import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)

test('the form upload returns the form and what the server filed where', async () => {
  const split = { message: 'This file held several documents: Passport (page 2) and Resume (page 1) were filed in their own fields.', pieces: [] }
  globalThis.__splitAnswer = { form: { a: 1 }, split }
  const answered = await uploadFormDocument('c1', 'passport', new File(['x'], 'scan.pdf'))
  assert.deepEqual(answered.form, { normalized: { a: 1 } })
  assert.equal(answered.split.message, split.message)
})

test('a single-document upload has no split to report', async () => {
  globalThis.__splitAnswer = { form: { a: 1 } }
  assert.equal((await uploadFormDocument('c1', 'medical', new File(['x'], 'm.pdf'))).split, null)
  globalThis.__splitAnswer = {}
  assert.deepEqual(await uploadFormDocument('c1', 'medical', new File(['x'], 'm.pdf')), { form: null, split: null })
})

test('the screens say which document went where, and keep their old wording for a plain upload', async () => {
  const profile = await readFile(new URL('../src/screens/candidates/candidate-profile.tsx', import.meta.url), 'utf8')
  const form = await readFile(new URL('../src/screens/candidates/application-form.tsx', import.meta.url), 'utf8')
  const add = await readFile(new URL('../src/screens/candidates/add-candidate.tsx', import.meta.url), 'utf8')
  assert.match(profile, /stored\.split\?\.message/)
  assert.match(form, /split\?\.message \|\| `\$\{entry\?\.label \?\? 'Document'\} uploaded`/)
  assert.match(add, /intake\.attach\(candidateId, \(message\) => store\.notify\(message\)\)/)
})
