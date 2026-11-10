import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import vm from 'node:vm'
import ts from 'typescript'

const html = await readFile(new URL('../index.html', import.meta.url), 'utf8')
const htmlTag = html.match(/<html\b[^>]*>/)[0]
const attribute = (name) => htmlTag.match(new RegExp(`\\b${name}=["']([^"']*)["']`))?.[1]
const bootstrap = [...html.matchAll(/<script(?:\s[^>]*)?>([\s\S]*?)<\/script>/g)]
  .map((match) => match[1]).find((script) => script.includes('crm-theme'))
assert.ok(bootstrap, 'test the real early theme script')
const source = await readFile(new URL('../src/shared/lib/theme.ts', import.meta.url), 'utf8')
const { outputText } = ts.transpileModule(source, {
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
})

function browser({ saved = null, osDark = true, storageFailure, items = new Map() } = {}) {
  if (saved !== null) items.set('crm-theme', saved)
  const writes = []
  const themesApplied = []
  const localStorage = {
    getItem(key) {
      if (storageFailure === 'read') throw new Error('Storage blocked')
      return items.get(key) ?? null
    },
    setItem(key, value) {
      if (storageFailure === 'write') throw new Error('Storage full')
      writes.push([key, value])
      items.set(key, value)
    },
  }
  const dataset = new Proxy({ theme: attribute('data-theme'), palette: attribute('data-palette') }, {
    set(target, key, value) {
      if (key === 'theme') themesApplied.push(value)
      target[key] = value
      return true
    },
  })
  const document = { documentElement: { dataset } }
  const window = { document, matchMedia: () => ({ matches: osDark }) }
  const readStorage = () => {
    if (storageFailure === 'access') throw new Error('Storage inaccessible')
    return localStorage
  }
  Object.defineProperty(window, 'localStorage', { get: readStorage })
  const module = { exports: {} }
  const sandbox = {
    window, document, module, exports: module.exports,
    // The production module is compiled unchanged; only React's rendering hook is stubbed.
    require(name) {
      assert.equal(name, 'react')
      return { useSyncExternalStore: (_subscribe, getSnapshot) => getSnapshot() }
    },
  }
  Object.defineProperty(sandbox, 'localStorage', { get: readStorage })
  const context = vm.createContext(sandbox)
  vm.runInContext(outputText, context, { filename: 'theme.ts' })
  const runtime = module.exports
  function start(expected) {
    vm.runInContext(bootstrap, context, { filename: 'index.html theme bootstrap' })
    assert.equal(dataset.theme, expected, 'the early HTML theme must match the preference')
    assert.equal(dataset.palette, 'white')
    runtime.initTheme()
    assert.equal(dataset.theme, expected, 'runtime initialization must not switch the early theme')
    assert.equal(runtime.useTheme().theme, expected)
    assert.equal(dataset.palette, 'white')
  }
  return { start, runtime, dataset, items, writes, themesApplied }
}

test('HTML supplies the light white-panel fallback before JavaScript runs', () => {
  assert.equal(attribute('data-theme'), 'light')
  assert.equal(attribute('data-palette'), 'white')
})

test('missing or invalid preferences stay light even when the operating system is dark', () => {
  for (const saved of [null, '', 'system', 'DARK']) {
    const page = browser({ saved, osDark: true })
    page.start('light')
    assert.ok(page.themesApplied.every((theme) => theme === 'light'), 'startup must never apply dark')
    assert.deepEqual(page.writes, [], 'a default must not overwrite an explicit future choice')
  }
})

test('saved light and dark choices are honored by both startup paths regardless of OS preference', () => {
  for (const saved of ['light', 'dark']) {
    for (const osDark of [false, true]) {
      const page = browser({ saved, osDark })
      page.start(saved)
      assert.deepEqual(page.writes, [])
    }
  }
})

test('blocked storage still applies light in the early script and runtime', () => {
  for (const storageFailure of ['read', 'access']) {
    const page = browser({ osDark: true, storageFailure })
    page.start('light')
    assert.ok(page.themesApplied.every((theme) => theme === 'light'))
  }
})

test('explicit theme changes update the page and survive a reload', () => {
  let page = browser()
  page.start('light')
  for (const selected of ['dark', 'light']) {
    page.runtime.setTheme(selected)
    assert.equal(page.dataset.theme, selected)
    assert.equal(page.runtime.useTheme().theme, selected)
    assert.equal(page.items.get('crm-theme'), selected)
    page = browser({ items: page.items, osDark: selected === 'light' })
    page.start(selected)
  }
})

test('an explicit choice still works for the current tab when storage writes fail', () => {
  const page = browser({ storageFailure: 'write' })
  page.start('light')
  page.runtime.setTheme('dark')
  assert.equal(page.dataset.theme, 'dark')
  assert.equal(page.runtime.useTheme().theme, 'dark')
  assert.equal(page.items.has('crm-theme'), false)
})
