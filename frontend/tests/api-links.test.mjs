import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

test('public webhook URLs follow the API deployment instead of the frontend host', async (t) => {
  const previousWindow = globalThis.window
  globalThis.window = { location: { origin: 'https://crm.example.test' } }
  t.after(() => {
    if (previousWindow === undefined) delete globalThis.window
    else globalThis.window = previousWindow
  })
  const source = await readFile(new URL('../src/shared/api/client.ts', import.meta.url), 'utf8')
  for (const [base, expected] of [
    ['', 'https://crm.example.test/api/whatsapp/webhook'],
    ['https://api.example.test', 'https://api.example.test/api/whatsapp/webhook'],
    ['/backend', 'https://crm.example.test/backend/api/whatsapp/webhook'],
  ]) {
    const { outputText } = ts.transpileModule(source.replace('import.meta.env?.VITE_API_URL', JSON.stringify(base)), {
      compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext },
    })
    const { absoluteApiHref } = await import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
    assert.equal(absoluteApiHref('/api/whatsapp/webhook'), expected)
  }
})
