import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

async function load(relative) {
  const source = await readFile(new URL(relative, import.meta.url), 'utf8')
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return import(`data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`)
}

const { checkWebsite, websiteHref, websiteLabel, WEBSITE_MAX } = await load('../src/shared/lib/website.ts')

test('blank is allowed, a bare domain gets https://', () => {
  assert.deepEqual(checkWebsite(''), { value: '', error: '' })
  assert.deepEqual(checkWebsite('   '), { value: '', error: '' })
  assert.deepEqual(checkWebsite(undefined), { value: '', error: '' })
  assert.equal(checkWebsite('www.GulfBuild.com').value, 'https://www.gulfbuild.com')
  assert.equal(checkWebsite('  gulfbuild.ae/careers  ').value, 'https://gulfbuild.ae/careers')
  assert.equal(checkWebsite('example.com:8080').value, 'https://example.com:8080')
  assert.equal(checkWebsite('http://example.com/').value, 'http://example.com')
})

test('spaces, script schemes, credentials and hosts without a dot are refused', () => {
  for (const bad of ['gulf build.com', 'javascript:alert(1)', 'JavaScript://x.com', 'data:text/html,hi', 'mailto:a@b.com', 'ftp://example.com',
    'https://user:pass@example.com', 'localhost', 'https://', 'http://exa mple.com', '.com', 'a..com']) {
    assert.notEqual(checkWebsite(bad).error, '', bad)
  }
  assert.notEqual(checkWebsite('a'.repeat(WEBSITE_MAX) + '.com').error, '')
})

test('only plain http(s) values become links', () => {
  assert.equal(websiteHref('https://example.com'), 'https://example.com')
  assert.equal(websiteHref('example.com'), 'https://example.com')
  assert.equal(websiteHref('javascript:alert(1)'), null)
  assert.equal(websiteHref(''), null)
  assert.equal(websiteHref(null), null)
})

test('the label drops the scheme and a lone trailing slash', () => {
  assert.equal(websiteLabel('https://www.example.com'), 'www.example.com')
  assert.equal(websiteLabel('http://example.com/'), 'example.com')
  assert.equal(websiteLabel('https://example.com/careers'), 'example.com/careers')
  assert.equal(websiteLabel(undefined), '')
})
