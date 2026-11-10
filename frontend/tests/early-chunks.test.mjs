import assert from 'node:assert/strict'
import { fileURLToPath } from 'node:url'
import { test } from 'node:test'
import { build } from 'vite'

test('built HTML preloads the shell and Leads for both Leads URLs', async () => {
  // Exercise the production HTML transform against actual hashed chunks, without writing dist.
  // A source-text check would miss a route whose chunk cannot be found in the emitted bundle.
  const result = await build({
    root: fileURLToPath(new URL('..', import.meta.url)),
    logLevel: 'silent',
    build: { write: false, reportCompressedSize: false },
  })
  assert.ok(!Array.isArray(result) && 'output' in result)
  const output = result.output
  const html = output.find((item) => item.type === 'asset' && item.fileName === 'index.html')
  assert.ok(html, 'production HTML was emitted')
  const match = String(html.source).match(/var chunks = (\{[^\n]+\})/)
  assert.ok(match, 'production HTML contains its early chunk table')
  const table = JSON.parse(match[1])
  const chunks = output.filter((item) => item.type === 'chunk')
  const findChunk = (source) => chunks.find((chunk) => chunk.facadeModuleId?.replace(/\\/g, '/').endsWith(source))
  const shell = findChunk('/src/app/layout/app-shell.tsx')
  const leads = findChunk('/src/screens/leads/leads.tsx')
  assert.ok(shell, 'shell remains a lazy chunk')
  assert.ok(leads, 'Leads remains a lazy chunk')
  const emitted = new Set(output.map((item) => `/${item.fileName}`))

  for (const route of ['/leads', '/leads/import']) {
    const urls = table[route]
    assert.ok(Array.isArray(urls), `${route} has an early preload list`)
    assert.ok(urls.includes(`/${shell.fileName}`), `${route} preloads its shell`)
    assert.ok(urls.includes(`/${leads.fileName}`), `${route} preloads Leads before the entry executes`)
    for (const url of urls) assert.ok(emitted.has(url), `${route} refers to an emitted asset: ${url}`)
    assert.ok(!urls.some((url) => url.includes('vendor-charts')), `${route} does not download unrelated charts`)
    assert.ok(!chunks.filter((chunk) => chunk.isEntry).some((chunk) => urls.includes(`/${chunk.fileName}`)),
      `${route} does not redundantly preload the entry`)
  }
  assert.deepEqual(table['/leads/import'], table['/leads'])
})
