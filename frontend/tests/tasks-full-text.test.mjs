import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const rules = (css, selector) => [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)].filter((m) => m[1].split(',').some((part) => part.trim().endsWith(selector))).map((m) => m[2])

test('NOTES: the latest note is shown in full: no height cap, no inner scroll box, no keyboard stop for a scroller, text wraps', async () => {
  const css = (await read('styles/tasks-table.css')).replace(/\/\*[\s\S]*?\*\//g, '')
  for (const body of rules(css, '.tw-note-list')) assert.doesNotMatch(body, /max-height|(^|;)\s*height:|overflow-y|overflow:\s*(auto|scroll|hidden)|overscroll/, 'the notes list neither caps its height nor scrolls')
  for (const body of rules(css, '.tw-notes-cell')) assert.doesNotMatch(body, /max-height|(^|;)\s*height:/, 'the notes cell grows with its notes')
  assert.match(rules(css, '.tw-note-body').join(' '), /overflow-wrap: anywhere/, 'long words and links wrap at the cell width')
  const table = await read('screens/tasks/task-table.tsx')
  assert.doesNotMatch(table, /className="tw-note-list"[^\n]*tabIndex/, 'there is no scroller to focus any more')
  assert.match(table, /aria-label=\{`Latest note for \$\{row\.candidate\}`\}/, 'still a labelled list, newest first')
  assert.match(table, /Delete note|DELETE_NOTE_/, 'each note keeps its delete button')
})

test('REMARKS: the chosen remark is shown in full: the trigger grows and wraps, with no ellipsis and no line clamp, in the table and in the cards', async () => {
  const css = (await read('styles/tasks-table.css')).replace(/\/\*[\s\S]*?\*\//g, '')
  const trigger = rules(css, '.dw-remark .custom-select-trigger')
  assert.ok(trigger.length >= 2, 'a rule for the table and one for the cards')
  for (const body of trigger) {
    assert.match(body, /height: auto !important/, 'never a fixed height')
    assert.doesNotMatch(body, /(^|;)\s*height: \d/)
  }
  const value = [...rules(css, '.dw-remark .custom-select-trigger > span:first-child'), ...rules(css, '.dw-remark .custom-select-trigger[data-placeholder] > span:first-child')]
  assert.ok(value.length >= 2)
  for (const body of value) {
    assert.doesNotMatch(body, /line-clamp|-webkit-box|text-overflow:\s*ellipsis|white-space:\s*nowrap/, 'no clamp, no ellipsis, no single line')
    assert.match(body, /white-space: normal/)
    assert.match(body, /overflow: visible/)
  }
})

test('the dropdown lists every option in full (options wrap, they are never truncated)', async () => {
  const controls = (await read('styles/controls.css')).replace(/\/\*[\s\S]*?\*\//g, '')
  const option = rules(controls, '.custom-select-option > span').join(' ')
  assert.match(option, /overflow-wrap: anywhere/)
  assert.doesNotMatch(option, /text-overflow|white-space:\s*nowrap|line-clamp/)
  assert.doesNotMatch(rules(controls, '.custom-select-option').join(' '), /text-overflow|white-space:\s*nowrap|line-clamp/)
})
