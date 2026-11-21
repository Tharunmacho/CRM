import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

/**
 * The look of the application form, read from the stylesheet and the components (the screen needs React and a browser to run).
 * These are the measurable rules of the redesign: one grid, 40px controls, no clipped identity text, one active tab state,
 * a sticky action bar for unsaved edits, and the three layouts (rail, summary bar, single column).
 */
const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '')

test('every input, select, date, phone and tag control is 40px high with one radius, border and focus ring (44px on a phone)', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-main :is\(\.ui-input:not\(textarea\), \.custom-select-trigger, \.date-picker-trigger\) \{[^}]*height: 40px; min-height: 40px/)
  assert.match(css, /\.af-main :is\(\.ui-input, \.custom-select-trigger, \.date-picker-trigger, \.ac-tags\) \{[^}]*border: 1px solid var\(--border-control\); border-radius: 8px/)
  assert.match(css, /\.ac-tags \{[^}]*min-height: 40px/, 'tags grow with their chips but never under 40px')
  assert.match(css, /\.ac-tag \{[^}]*height: 28px/, 'chips are 28px')
  assert.match(css, /box-shadow: 0 0 0 3px rgba\(var\(--primary-rgb\), \.14\)/, 'one focus ring')
  const phone = css.slice(css.indexOf('@media (max-width: 768px)'))
  assert.match(phone, /height: 44px; min-height: 44px; font-size: 16px/)
})

test('labels are 13px/600 above the control on a 20px row; the provenance badge is a quiet icon at the end of that row', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.aff-lab, \.af \.aff \.ac-lab \{ height: 20px/)
  assert.match(css, /font-size: 13px; font-weight: 600; line-height: 20px; color: var\(--text-muted\)/)
  assert.match(css, /\.aff \.aff-from \{ width: 20px; height: 20px/)
  const controls = await src('features/application-form/fill/fill-controls.tsx')
  assert.match(controls, /className="aff-from" role="img" aria-label=\{text\} title=\{text\}/, 'icon only, spoken and shown as a tooltip')
  assert.doesNotMatch(controls, /aff-from-text/, 'no wide text chip beside the label')
  assert.match(controls, /'aria-required': true/, 'a required field is announced')
  assert.match(controls, /'aria-describedby': noteId/, 'the control points at its error text')
  assert.match(controls, /<AlertCircle aria-hidden="true" \/>/, 'the error sentence carries an icon')
})

test('names, client, job and address are never cut with an ellipsis', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  const rule = (selector) => css.split('\n').filter((line) => line.includes(selector)).join('\n')
  for (const selector of ['.af .af-bar-name {', '.af .af-ctx dd {', '.af .af-bar-title small {']) {
    const text = rule(selector)
    assert.ok(text, selector)
    assert.doesNotMatch(text, /text-overflow: ellipsis|white-space: nowrap/, selector)
    assert.match(text, /white-space: normal/, selector)
  }
  const strip_ = await src('features/application-form/components/context-strip.tsx')
  assert.doesNotMatch(strip_, /title=\{row\.value\}/, 'the value is shown whole, not as a tooltip for a cut text')
})

test('the Personal card gives the name and the father’s name half a row each and the photo a column of its own', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /@container affc \(min-width: 720px\)/)
  assert.match(css, /grid-template-columns: 112px repeat\(12, minmax\(0, 1fr\)\)/)
  assert.match(css, /\[data-field='personal\.candidateName'\], \[data-field='personal\.fatherName'\]\) \{ grid-column: span 6; \}/)
  assert.match(css, /\.aff-photo-frame \{[^}]*width: 112px; height: 112px/, 'a 112px avatar')
  const photo = await src('features/application-form/fill/fill-photo.tsx')
  assert.match(photo, /className="aff-photo-cam"/, 'a camera button on the avatar')
  assert.match(photo, /'Replace photo' : 'Upload photo'/, 'a clear text action under it')
})

test('there is one active tab state: Personal and Passport are separate tabs and no tab carries a second underline', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.doesNotMatch(css, /is-shown/)
  assert.equal([...css.matchAll(/\.af-tab\[aria-selected='true'\]::after \{ background/g)].length, 1)
  const bar = await src('features/application-form/components/form-tabs.tsx')
  assert.doesNotMatch(bar, /alsoShown/)
  assert.match(bar, /kind === 'done'/, 'a complete tab shows a check')
  assert.match(bar, /af-tab-count/, 'a tab with fields to add shows a count')
  assert.match(bar, /onKeyDown=\{onKeyDown\}/, 'the arrow keys move between tabs')
})

test('the header is one sticky row: Back, name, ID and client and job on the left, status then actions on the right', async () => {
  const screen = await src('screens/candidates/application-form.tsx')
  const header = screen.slice(screen.indexOf('<header className="af-bar">'), screen.indexOf('</header>'))
  const at = (text) => header.indexOf(text)
  assert.ok(at('af-bar-back') >= 0 && at('af-bar-back') < at('af-bar-title') && at('af-bar-title') < at('af-bar-status') && at('af-bar-status') < at('af-bar-actions'))
  assert.match(header, /Preview form/)
  assert.match(header, /Print or save as PDF/)
  assert.doesNotMatch(header, /Save changes|Discard/, 'saving lives in the sticky action bar')
  assert.doesNotMatch(screen, /<StageClockChip/, 'no floating stage chip: the stage and its time limit sit in the context strip')
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-bar-status \{[^}]*margin-left: auto/)
  assert.match(css, /\.af-bar-actions \{[^}]*border-left: 1px solid var\(--af-line\)/, 'status and actions are separated by a hairline')
})

test('the context strip is a five-item descriptions grid with the stage clock folded in and a text Change button', async () => {
  const strip_ = await src('features/application-form/components/context-strip.tsx')
  for (const key of ['data-ctx="stage"', 'data-ctx="doc"', 'af-ctx-clock', 'af-context-change']) assert.ok(strip_.includes(key), key)
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-context-cells \{[^}]*grid-auto-flow: column; grid-auto-columns: minmax\(0, 1fr\)/)
  assert.match(css, /\.af-ctx \{[^}]*border-left: 1px solid var\(--af-line\)/, 'a divider between items')
  assert.match(css, /\.af-context-change \{[^}]*background: transparent/, 'Change is a text button')
})

test('unsaved edits show one sticky bar over the bottom edge with Discard, Save changes and what is still missing', async () => {
  const screen = await src('screens/candidates/application-form.tsx')
  assert.match(screen, /\{dirty && form && \(\s*<div className="af-actionbar"/)
  assert.match(screen, /Save changes/)
  assert.match(screen, /Still missing \{missing\.length\}/)
  assert.match(screen, /'af', dirty && 'has-actionbar'/)
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-actionbar \{ position: absolute; left: 0; right: 0; bottom: 0/, 'it overlays, so nothing above it moves')
  assert.match(css, /\.af\.has-actionbar \.af-layout \{ padding-bottom: 96px; \}/, 'the last card is never hidden behind it')
  const phone = css.slice(css.indexOf('@media (max-width: 768px)'))
  assert.match(phone, /\.af-actionbar-buttons \.ui-button \{ flex: 1 1 0; height: 44px/, 'full width, 44px targets on a phone')
  const print = await src('styles/application-form.css')
  assert.match(print, /\.af-actionbar, \.af-notes \{ display: none !important/, 'it never prints')
})

test('three layouts: the rail from 1280px, a summary bar above the tabs from 768px, one column and a segmented tab control below', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-layout \{[^}]*grid-template-columns: 280px minmax\(0, 1100px\); justify-content: center/, 'rail plus a content column of at most 1100px, centred')
  assert.match(css, /@media \(max-width: 1279px\) \{\s*\.af \.af-layout \{ grid-template-columns: minmax\(0, 1fr\)/)
  assert.match(css, /@media \(min-width: 1280px\) \{ \.af \.af-missing-toggle \{ display: none; \} \}/)
  const phone = css.slice(css.indexOf('@media (max-width: 768px)'))
  assert.match(phone, /\.af-tab\[aria-selected='true'\] \{ background: var\(--bg-card\)/, 'the chosen segment is a raised pill')
  assert.match(phone, /\.af-tab::after \{ display: none; \}/)
  assert.match(css, /\.af-tablist-wrap\[data-fade-end='true'\]::after/, 'a fade at the edge that scrolls')
  assert.match(css, /@container affc \(max-width: 559px\)[^}]*\{[^}]*grid-column: 1 \/ -1/, 'fields are one column in a narrow card')
})

test('cards use 24px padding, the site tokens and no colour of their own; the page ends 32px below the last card', async () => {
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-pane\.af-work \{ padding: 24px; \}/)
  assert.match(css, /padding: 24px 24px 32px/, 'page padding bottom 32px')
  assert.match(css, /\.aff \.aff-tile \{ width: 32px; height: 32px/)
  assert.doesNotMatch(css, /#[0-9a-fA-F]{3,8}\b/)
})

test('a locked placement shows Client and Job as locked values with ONE helper line and the existing Unmap flow', async () => {
  const bar = await src('features/application-form/components/assignment-bar.tsx')
  assert.match(bar, /const locked = editJob && pickersLocked/)
  assert.match(bar, /className="af-assign-locked"/)
  assert.equal([...bar.matchAll(/className="af-assign-lock"/g)].length, 1, 'one calm line under the pair')
  assert.match(bar, /PICKERS_LOCKED_TEXT/)
  assert.match(bar, /Unmap candidate/)
  assert.match(bar, /errors\.job/, 'the server error stays as the fallback for other refusals')
  const screen = await src('screens/candidates/application-form.tsx')
  assert.match(screen, /pickersLocked=\{pickersLocked\(application\?\.currentStage, application\?\.status\)\}/)
  assert.match(screen, /store\.unmap\(application\.id\)/, 'the same unmap call the job and candidate pages use')
  assert.match(screen, /store\.can\('map_candidates'\)/, 'only people who may map candidates are offered it')
  assert.match(screen, /UnmapCandidateDialog/, 'with the same confirmation')
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-assign-locked \{[^}]*min-height: 40px/, 'the same 40px control look')
})

test('Live and Reconnecting are a small status dot and a word, not a chip, with a tooltip that says edits are safe', async () => {
  const screen = await src('screens/candidates/application-form.tsx')
  assert.match(screen, /Live updates reconnecting; your edits are safe/)
  const css = strip(await src('styles/application-form-tabs.css'))
  assert.match(css, /\.af-live \{[^}]*background: transparent/)
  assert.match(css, /\.af-live\.is-retry i \{[^}]*var\(--primary\)/, 'reconnecting is not an error colour')
})
