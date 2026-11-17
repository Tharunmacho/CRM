/**
 * Regression guard: a candidate's name is never struck through on the Tasks screens. A line through a name reads as deleted,
 * rejected or declined, which is the wrong signal for finished work (the owner reported this on "Done today"). A finished row is
 * shown by its green left edge, the green "Done 11:35" tick and a slightly muted colour, never by a line through the text.
 */
import assert from 'node:assert/strict'
import { readdir, readFile } from 'node:fs/promises'
import { test } from 'node:test'

const root = new URL('../src/', import.meta.url)
const read = (path) => readFile(new URL(path, root), 'utf8')
const stripCss = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '')
const stripCode = (text) => text.replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')

/** Every plain rule as { selector, body }: nested blocks (@container, @media) are matched at their innermost rules. */
function rules(css) {
  return [...stripCss(css).matchAll(/([^{}]+)\{([^{}]*)\}/g)].map((match) => ({ selector: match[1].trim(), body: match[2] }))
}

const cssFiles = (await readdir(new URL('styles/', root))).filter((name) => name.endsWith('.css'))
const css = Object.fromEntries(await Promise.all(cssFiles.map(async (name) => [name, await read(`styles/${name}`)])))
const TASK_CSS = ['daily-work.css', 'daily-work-board.css', 'tasks-page.css']
/** Selectors of the Tasks / daily-work / follow-up screens, and of the shared name element used in their rows. */
const tasksSelector = /\.(dw|tk)-|\.dw\b|\.tk\b|\.record-name|\.record-identity|\.cid-main/
const doneSelector = /is-done|is-complete|\.done|completed|finished/

test('the Tasks stylesheets exist', () => {
  for (const name of TASK_CSS) assert.ok(cssFiles.includes(name), `${name} is missing`)
})

test('no rule of the Tasks stylesheets draws a line through text', () => {
  for (const name of TASK_CSS) {
    for (const { selector, body } of rules(css[name])) {
      assert.doesNotMatch(body, /line-through/i, `${name}: "${selector}" strikes text through; never strike through names, it reads as rejected`)
    }
  }
})

test('no rule anywhere that styles Tasks rows or candidate names draws a line through them', () => {
  for (const name of cssFiles) {
    for (const { selector, body } of rules(css[name])) {
      if (!/line-through/i.test(body)) continue
      assert.doesNotMatch(selector, tasksSelector, `${name}: "${selector}" strikes through a Tasks row or a candidate name`)
    }
  }
})

test('no done / completed row rule in any stylesheet that styles candidate rows or names strikes through', () => {
  for (const name of cssFiles) {
    for (const { selector, body } of rules(css[name])) {
      if (!/line-through/i.test(body)) continue
      if (/record-|candidate|\.dw|\.tk/.test(selector)) assert.doesNotMatch(selector, doneSelector, `${name}: "${selector}"`)
    }
  }
})

test('the done-row rule keeps its green edge and its "never strike through" note', async () => {
  const sheet = css['daily-work.css']
  assert.match(sheet, /never strike through names: it reads as rejected|NEVER strike through names: it reads as rejected/i)
  const done = rules(sheet).filter(({ selector }) => /\.dw-row\.is-done/.test(selector))
  assert.ok(done.length >= 2, 'the done-row rules are missing')
  assert.ok(done.some(({ body }) => /inset 3px 0 0/.test(body) && /success-ink/.test(body)), 'the green left edge of a done row is missing')
  assert.ok(done.every(({ body }) => !/line-through|underline|overline/i.test(body)), 'a done row must not draw a text line')
})

test('the row components never add a strike-through class, element or inline style', async () => {
  const files = ['screens/tasks/daily-table.tsx', 'screens/tasks/follow-up-table.tsx', 'screens/tasks/daily-work.tsx', 'screens/tasks/daily-staff-board.tsx',
    'screens/tasks/task-identity.tsx', 'screens/tasks/task-parts.tsx', 'features/daily-work/daily-work-model.ts', 'shared/ui/workspace.tsx']
  for (const file of files) {
    const source = stripCode(await read(file))
    assert.doesNotMatch(source, /line-through|lineThrough|strike|textDecoration|text-decoration|<s>|<del[\s>]/i, `${file} adds a strike-through`)
  }
})

test('the done row is marked only by is-done, which the stylesheet colours but never strikes', async () => {
  const table = stripCode(await read('screens/tasks/daily-table.tsx'))
  assert.match(table, /cn\('dw-row', done && 'is-done'/)
  assert.doesNotMatch(table, /className=\{[^}]*(strike|through|cancel|void|deleted)/i)
})
