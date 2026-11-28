import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'
import ts from 'typescript'

const moduleUrl = (source) => {
  const { outputText } = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } })
  return `data:text/javascript;base64,${Buffer.from(outputText).toString('base64')}`
}
const read = (path) => readFile(new URL(path, import.meta.url), 'utf8')
const code = async (path) => (await read(path)).replace(/\/\*[\s\S]*?\*\//g, '').replace(/^\s*\/\/.*$/gm, '')
const m = await import(moduleUrl(await read('../src/features/workflow/reopen-application.ts')))

test('the reason must have at least three characters once trimmed, like the server asks', () => {
  assert.equal(m.MIN_REOPEN_REASON, 3)
  for (const bad of ['', '  ', 'ok', ' a ']) assert.equal(m.reopenReasonOk(bad), false, JSON.stringify(bad))
  for (const good of ['yes', ' Candidate is interested again ']) assert.equal(m.reopenReasonOk(good), true, good)
})

test('the button is offered only for a closed placement and only to people who may work the candidate', () => {
  assert.equal(m.canReopenApplication('closed', true), true)
  assert.equal(m.canReopenApplication('closed', false), false)
  for (const status of ['active', 'deployed', '', null, undefined]) assert.equal(m.canReopenApplication(status, true), false, String(status))
})

test('the dialog names the stage the candidate goes back to and the Tasks Current list', () => {
  const text = m.reopenExplanation('Interview result')
  assert.match(text, /goes back to Interview result on the Tasks Current list/)
  assert.match(text, /stay on record/)
  assert.match(m.reopenExplanation('Prescreening'), /Prescreening/)
})

test('the line under an empty stage matches what is true: open, deployed, closed with the button, closed without it', () => {
  assert.equal(m.emptyStageHint('active', 4, true), 'Complete stage 4 to continue in order.')
  assert.match(m.emptyStageHint('deployed', 13, true), /deployed/)
  assert.match(m.emptyStageHint('closed', 1, true), /Choose Reopen application/)
  const noButton = m.emptyStageHint('closed', 1, false)
  assert.doesNotMatch(noButton, /Choose Reopen application/)
  assert.match(noButton, /recruiter, an admin or the MD can reopen it/)
  // the old dead end (no control existed) is gone
  for (const hint of [m.emptyStageHint('closed', 1, true), noButton]) assert.doesNotMatch(hint, /Reopen the application to continue working/)
})

test('the application form footnote tells how to continue only when the application is closed', () => {
  assert.match(m.closedFootnote('closed'), /Open a stage and choose Reopen application/)
  for (const status of ['active', 'deployed', '', null, undefined]) assert.equal(m.closedFootnote(status), null)
})

test('a server refusal is shown as it is, an empty one gets a plain sentence', () => {
  assert.equal(m.reopenFailure('This candidate is on another job now.'), 'This candidate is on another job now.')
  for (const empty of ['', '   ', null, undefined]) assert.match(m.reopenFailure(empty), /could not reopen the application/)
})

test('the component posts the reason and the version the screen holds to the endpoint the backend serves, then tells the Tasks page at once', async () => {
  const component = await code('../src/features/workflow/components/reopen-application.tsx')
  assert.match(component, /api\.post\(`\/applications\/\$\{app\.id\}\/reopen-application`, \{ reason: reason\.trim\(\), lock_version: app\.lockVersion \}\)/)
  assert.ok(component.indexOf('onRecorded?.()') > component.indexOf('api.post('), 'told after the server confirmed')
  assert.match(component, /store\.refresh\(\{ keepCurrent: true \}\)/)
  assert.match(component, /disabled=\{busy \|\| disabled \|\| !reopenReasonOk\(reason\)\}/, 'no submit without a valid reason')
  assert.match(component, /role="alert"/, 'a refusal is announced')
  const backend = await read('../../backend/app/api/routers/application_reopen.py')
  assert.match(backend, /@router\.post\("\/\{application_id\}\/reopen-application"/)
  assert.match(backend, /reason: str = Field\(min_length=3, max_length=500\)/)
})

test('a closed application shows the button in the stage panel and the closed row, and the dead-end hint is gone', async () => {
  const body = await code('../src/features/workflow/components/stage-modal-body.tsx')
  assert.match(body, /import \{ ReopenApplication \} from '\.\/reopen-application'/)
  assert.match(body, /function ClosedRow\(\{ app, canReopen = false/)
  assert.match(body, /\{canReopen && <div className="mt-3"><ReopenApplication app=\{app\}/)
  const uses = body.match(/<ClosedRow app=\{app\} canReopen=\{canReopenApplication\(app\.status, canWork\)\}/g) ?? []
  assert.match(body, /stage\.no === app\.currentStage && closureTab\(app\.closureReason\) === 'Declined' && canReopenApplication\(app\.status, canWork\) &&\s*<ReopenApplication/)
  assert.equal(uses.length, 1, 'the embedded form has one closed-stage summary')
  assert.match(body, /onRecorded=\{onStageRecorded\} onDone=\{onStageChanged\}/, 'the Tasks page re-reads at once, the host screen reloads')
  assert.doesNotMatch(body, /Reopen the application to continue working on its stages\./)
  assert.doesNotMatch(body, /emptyStageHint\(app\.status, app\.currentStage, canReopenApplication\(app\.status, canWork\)\)/)
  // Reopening a finished STAGE stays as it was (admin only)
  assert.match(body, /Reopen stage \{stage\}/)
})

test('the Tasks row menu opens the monitor without a reopen action', async () => {
  const table = await code('../src/screens/tasks/task-table.tsx')
  assert.doesNotMatch(table, /\.\.\.\(row\.applicationId && closed && work \? \[\{ label: 'Reopen application', icon: RotateCcw, onSelect: \(\) => onOpen\(row\) \}\] : \[\]\)/)
})

test('the stage picker has no explanatory footnote', async () => {
  const stages = await code('../src/features/application-form/components/recruitment-stages.tsx')
  assert.doesNotMatch(stages, /af-stage-footnote/)
})
