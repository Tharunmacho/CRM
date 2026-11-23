import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { readFile } from 'node:fs/promises'
import test from 'node:test'
import ts from 'typescript'

const read = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const load = async (path) => {
  const js = ts.transpileModule(await read(path), { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } }).outputText
  return import(`data:text/javascript;base64,${Buffer.from(js).toString('base64')}`)
}

const { cardSpeech } = await load('features/dashboard/card-text.ts')
const dashboard = await read('screens/dashboard/dashboard.tsx')
const workspace = await read('shared/ui/workspace.tsx')
const css = await read('styles/dashboard-simple.css')

test('a card says what it used to write underneath only in its tooltip and spoken name', () => {
  assert.equal(cardSpeech('Tasks', '87', '84 overdue'), 'Tasks: 87, 84 overdue')
  assert.equal(cardSpeech('Clients', '34', 'clients created'), 'Clients: 34, clients created')
  assert.equal(cardSpeech('Inbox', '4', 'candidates from email', '3 need review · 1 account'), 'Inbox: 4, candidates from email, 3 need review · 1 account')
  assert.equal(cardSpeech('Travelled', '–', '', undefined, false), 'Travelled: –')
})

test('the dashboard shows the nine cards through the same SummaryCards as the Travelled totals, with no note or detail text on the card', () => {
  assert.match(dashboard, /<div className="ds-summary"><SummaryCards items=\{items\} \/><\/div>/)
  for (const gone of ['ds-card', 'ds-note', 'ds-detail', 'ds-label', 'ds-value', 'ds-grid', 'ArrowUpRight']) assert.ok(!dashboard.includes(gone), `${gone} is gone`)
  const keys = [...dashboard.matchAll(/\{ key: '([a-z]+)', label: '([^']+)'/g)].map((m) => m[1])
  assert.deepEqual(keys, ['inbox', 'requirements', 'clients', 'jobs', 'candidates', 'leads', 'databank', 'tasks', 'travelled'])
  // every card has a route, a tooltip and a spoken name, and none of them passes a `detail` line to SummaryCards
  assert.equal([...dashboard.matchAll(/, to: '\/[a-z-]+' \}/g)].length, 9)
  assert.match(dashboard, /href: card\.to, onOpen: \(\) => navigate\(card\.to\), title: text, ariaLabel: text/)
  assert.doesNotMatch(dashboard, /detail: card\.detail|detail: text/)
  // overdue work keeps its red ink and tone
  assert.match(dashboard, /tone: overdue > 0 \? 'red' : 'blue'/)
  assert.match(dashboard, /tone: card\.danger \? 'red' : card\.tone, danger: card\.danger/)
})

test('SummaryCards can be a real link and the other pages keep rendering sections as before', () => {
  assert.match(workspace, /<a className="summary-card is-link" key=\{label\} href=\{href\} title=\{title\} aria-label=\{ariaLabel \?\? label\} onClick=\{open\}>/)
  assert.match(workspace, /event\.button !== 0 \|\| event\.metaKey \|\| event\.ctrlKey \|\| event\.shiftKey \|\| event\.altKey\) return/, 'Ctrl/Cmd-click still opens a new tab')
  assert.match(workspace, /<section\s+className=\{cn\('summary-card', onSelect && 'is-action', active && 'is-active'\)\}\s+key=\{label\}\s+aria-label=\{ariaLabel \?\? label\}/)
  assert.match(workspace, /summary-value tnum', danger && 'is-danger'/)
})

// ---- the redesigned tiles and Overview (Task O): a professional, compact KPI look; the contract is pinned below ----
const rule = (selector) => {
  const start = css.indexOf(selector)
  assert.ok(start >= 0, `${selector} exists`)
  return css.slice(start, css.indexOf('}', start))
}
const theme = await read('styles/theme.css')
const token = (block, name) => new RegExp(`${name}:\\s*(#[0-9a-fA-F]{6})`).exec(block)?.[1]
const lightBlock = theme.slice(theme.indexOf(':root {'), theme.indexOf('[data-theme="dark"] {'))
const darkBlock = theme.slice(theme.indexOf('[data-theme="dark"] {'), theme.indexOf('[data-theme="dark"] {') + 4000)
const luminance = (hex) => {
  const channel = (offset) => { const v = parseInt(hex.slice(offset, offset + 2), 16) / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4 }
  return 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5)
}
const contrast = (a, b) => { const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x); return (hi + 0.05) / (lo + 0.05) }

test('the nine tiles fill two rows with no empty slot: the grid spans add up to whole rows, equal gaps, flush right edges', () => {
  const grid = rule('html:root:root:root .dashsimple .ds-summary .workspace-summary {')
  const columns = Number(/grid-template-columns: repeat\((\d+), minmax\(0, 1fr\)\)/.exec(grid)?.[1])
  assert.equal(columns, 20)
  assert.match(grid, /gap: 16px/)
  const first = Number(/\.summary-card \{ grid-column: span (\d+); \}/.exec(css)?.[1])
  const second = Number(/\.summary-card:nth-child\(n \+ 6\) \{ grid-column: span (\d+); \}/.exec(css)?.[1])
  assert.equal(first * 5, columns, 'row one: five tiles use every column')
  assert.equal(second * 4, columns, 'row two: four tiles use every column (no empty fifth slot)')
  // narrower: three across divides nine exactly; two across leaves one tile, which takes the full row
  assert.match(css, /@container dash \(max-width: 929px\) \{[\s\S]*?repeat\(3, minmax\(0, 1fr\)\)/)
  assert.equal(9 % 3, 0)
  assert.match(css, /@container dash \(max-width: 560px\) \{[\s\S]*?repeat\(2, minmax\(0, 1fr\)\)[\s\S]*?\.summary-card:last-child \{ grid-column: 1 \/ -1; \}/)
  assert.match(css, /summary-card, html:root:root:root \.dashsimple \.ds-summary \.workspace-summary > \.summary-card:nth-child\(n \+ 6\) \{ grid-column: auto; \}/, 'the spans are reset where the columns change, so no tile is left stretched')
})

test('a tile is compact and calm: 128-150 px tall, a white surface with a hairline and a light shadow, no coloured wash', () => {
  const tile = rule('html:root:root:root .dashsimple .ds-summary .summary-card {')
  const [low, , high] = /min-height: clamp\((\d+)px, ([\d.]+)cqw, (\d+)px\)/.exec(tile).slice(1).map((value) => Number(value))
  assert.ok(low >= 112 && low <= 140, `lower height bound ${low}px`)
  assert.ok(high >= 130 && high <= 160, `upper height bound ${high}px (the old tile was 225 px)`)
  assert.match(tile, /border: 1px solid var\(--border-main\); border-radius: 12px; background: var\(--bg-card\); box-shadow: var\(--shadow-card\)/)
  assert.doesNotMatch(tile, /gradient\(/, 'the global coloured wash is switched off for these tiles')
  assert.match(tile, /padding: 16px 12px/)
  assert.match(css, /summary-card::before \{ content: none; display: none; \}/, 'no accent bar')
  // type scale: label 13 px / 500 muted, figure 32 px / 650 with tabular figures and tight tracking, icon chip 36 px
  assert.match(css, /summary-card-top \{[^}]*font-size: 13px; font-weight: 500;[^}]*color: var\(--text-muted\)/)
  const value = rule('html:root:root:root .dashsimple .ds-summary .summary-value {')
  const size = Number(/font-size: (\d+)px/.exec(value)[1])
  assert.ok(size >= 30 && size <= 36, `figure ${size}px`)
  assert.match(value, /font-weight: 650/)
  assert.match(value, /font-variant-numeric: tabular-nums/)
  assert.match(value, /letter-spacing: -\.03em/)
  assert.match(css, /summary-icon \{ width: 36px; height: 36px; border-radius: 10px; box-shadow: none;/)
  // restrained interaction: a tinted hairline, a soft shadow and a 1 px lift; a ring for the keyboard
  assert.match(css, /summary-card\.is-link:hover \{ border-color: color-mix\(in srgb, var\(--tone\) 42%, var\(--border-main\)\); box-shadow: 0 4px 14px color-mix\(in srgb, var\(--tone\) 12%, transparent\); transform: translateY\(-1px\); \}/)
  assert.match(css, /summary-card\.is-link:focus-visible \{ outline: 2px solid var\(--tone\); outline-offset: 2px; \}/)
  assert.doesNotMatch(css, /aspect-ratios*:/)
})

test('every tile is one centred column on its own centre line: icon, then label, then figure; words wrap at spaces and are never cut', () => {
  const tile = rule('html:root:root:root .dashsimple .ds-summary .summary-card {')
  assert.match(tile, /display: flex; flex-direction: column; align-items: center; justify-content: center;/)
  assert.match(tile, /text-align: center/)
  assert.match(css, /summary-card-top \{ flex-direction: column-reverse; align-items: center;/)
  assert.match(css, /summary-value \{[^}]*text-align: center;/)
  assert.match(css, /overflow-wrap: normal; word-break: normal; hyphens: none; text-wrap: balance;/)
  assert.doesNotMatch(css, /\.summary-card[^{]*\{[^}]*text-align: left/)
})

test('the label, the figure and the overdue red keep a readable contrast against the tile in the light and the dark theme (WCAG AA, 4.5:1)', () => {
  for (const [name, block] of [['light', lightBlock], ['dark', darkBlock]]) {
    const surface = token(block, '--bg-card')
    assert.ok(surface, `${name} surface token`)
    assert.ok(contrast(token(block, '--text-muted'), surface) >= 4.5, `${name}: label on the tile`)
    assert.ok(contrast(token(block, '--text-main'), surface) >= 7, `${name}: figure on the tile`)
    assert.ok(contrast(token(block, '--error-ink'), surface) >= 4.5, `${name}: overdue red on the tile`)
  }
})

test('Overview speaks the same language as the tiles: 12 px curve, hairline, one footer, chart beside its legend on a wide card', () => {
  assert.match(css, /\.sg-card \{[^}]*padding: 20px; border: 1px solid var\(--border-main\); border-radius: 12px; background: var\(--bg-card\); box-shadow: var\(--shadow-card\)/)
  assert.doesNotMatch(css, /\.sg-card \{[^}]*gradient/, 'no wash on the Overview cards')
  assert.match(css, /\.sg-seg button\.is-active \{ background: var\(--primary\); color: var\(--on-primary\);/)
  assert.match(css, /\.sg-chip \{[^}]*width: 36px; height: 36px; border-radius: 10px;/, 'same icon chip as the tiles')
  assert.match(css, /@container dash \(min-width: 700px\) \{\s*\.sg-body \{ grid-template-columns: 192px minmax\(0, 1fr\); gap: 28px; \}/)
  assert.match(css, /\.sg-foot \{[^}]*border-top: 1px solid var\(--border-faint\)/)
  assert.match(css, /@container dash \(max-width: 929px\) \{[\s\S]*?\.sg-grid \{ grid-template-columns: minmax\(0, 1fr\); \}/)
  const graph = readFileSync(new URL('../src/features/dashboard/components/source-graph.tsx', import.meta.url), 'utf8')
  assert.match(graph, /<div className="sg-body">/)
  assert.match(graph, /<footer className="sg-foot">/)
  assert.match(graph, /STROKE = 20/, 'a thinner ring')
})
