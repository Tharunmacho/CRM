import assert from 'node:assert/strict'
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

test('nine cards in exactly two rows on a desktop (a 5-column grid of equal widths: five, then four flush-left), three across below 930 px, two on a tablet, one on a phone', () => {
  assert.match(css, /\.dashsimple \.ds-summary \.workspace-summary \{[^}]*grid-template-columns: repeat\(5, minmax\(0, 1fr\)\); gap: 16px;[^}]*align-items: stretch;/)
  // a squarish tile, never shorter than a Travelled total card (150 px), the figure pushed to the bottom
  assert.match(css, /\.dashsimple \.ds-summary \.summary-card \{ min-height: clamp\(150px, 15cqw, 230px\); \}/)
  assert.doesNotMatch(css, /aspect-ratios*:/, 'an aspect-ratio plus a minimum height forbids the tile to shrink below 225 px wide and overflows the grid at 1280 px')
  // every tile is one centred column on the card's centre axis: icon on top, label under it, the figure below, in every tile and at every breakpoint
  assert.match(css, /:root:root:root \.dashsimple \.ds-summary \.summary-card \{ align-items: center; justify-content: center; gap: 10px; text-align: center; \}/)
  assert.match(css, /:root:root:root \.dashsimple \.ds-summary \.summary-card-top \{ flex-direction: column-reverse; align-items: center; justify-content: flex-end; gap: 8px; width: 100%; min-height: 84px; text-align: center; \}/, 'icon above the label (the markup has the label first), room for two label lines so the figures line up')
  assert.match(css, /:root:root:root \.dashsimple \.ds-summary \.summary-value \{ margin-top: 0; text-align: center; \}/)
  assert.doesNotMatch(css, /\.summary-card \{[^}]*text-align: left/, 'nothing left-aligns the tiles')
  assert.match(css, /@container dash \(max-width: 700px\) \{\s*\.dashsimple \.ds-summary \.summary-card \{ min-height: 150px; \}/, 'two across are wide: no tall slabs')
  assert.match(css, /@media \(max-width: 520px\) \{\s*\.dashsimple \.ds-summary \.summary-card \{ min-height: 150px; \}/)
  // below 1100 px of dashboard width the card padding, gap and icon tighten so a 1280 px window with the sidebar open still fits five without splitting a word
  assert.match(css, /@container dash \(max-width: 1100px\) \{\s*:root:root \.dashsimple \.ds-summary \.summary-card \{ padding: 14px 12px 14px; \}\s*:root:root \.dashsimple \.ds-summary \.summary-icon \{ width: 36px; height: 36px; \}/)
  assert.match(css, /@container dash \(max-width: 929px\) \{[^}]*padding: 18px 20px 20px;[\s\S]*?grid-template-columns: repeat\(3, minmax\(0, 1fr\)\);/)
  assert.match(css, /@container dash \(max-width: 700px\) \{[\s\S]*?\.dashsimple \.ds-summary \.workspace-summary \{ grid-template-columns: repeat\(2, minmax\(0, 1fr\)\); \}/)
  assert.match(css, /@media \(max-width: 520px\) \{[\s\S]*?\.dashsimple \.ds-summary \.workspace-summary \{ grid-template-columns: minmax\(0, 1fr\); gap: 10px; \}/)
  assert.match(css, /\.summary-card\.is-link:focus-visible \{ outline: 2px solid var\(--primary\)/, 'a visible focus ring')
  assert.match(css, /summary-card-top > span:first-child \{ min-width: 0; overflow-wrap: break-word; word-break: normal; hyphens: none; \}/, 'a word is never split in the middle')
})
