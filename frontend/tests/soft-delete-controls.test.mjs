import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import test from 'node:test'

const read = (path) => readFileSync(new URL(`../src/${path}`, import.meta.url), 'utf8')
const af = (path) => read(`features/application-form/${path}`)

test('Remove for a document and for the photo asks first with the site Dialog, never window.confirm', () => {
  const documents = af('components/documents-section.tsx')
  const photo = af('fill/fill-photo.tsx')
  const notes = af('components/feedback-panel.tsx')
  const dialog = af('components/confirm-remove.tsx')
  for (const [name, source] of [['documents', documents], ['photo', photo], ['notes', notes], ['dialog', dialog]]) assert.ok(!/window\.confirm/.test(source), `${name} must not use window.confirm`)
  assert.match(dialog, /from '@\/shared\/ui\/overlay'/)
  assert.match(documents, /ConfirmRemoveDialog/)
  assert.match(documents, /Remove \$\{file\.fileName\}/)   // the button is labelled with the file it removes
  assert.match(documents, /canUpload && onRemove/)         // only for people who may work the candidate
  assert.match(photo, /Remove photo/)
  assert.match(photo, /ConfirmRemoveDialog/)
  assert.match(notes, /ConfirmRemoveDialog/)
})

test('the form API removes a file by its id and edits a note, both answering the whole form', () => {
  const api = af('form-api.ts')
  assert.match(api, /export async function removeFormDocument/)
  assert.match(api, /api\.del<unknown>\(formPath\(candidateId, target, `\/documents\/\$\{encodeURIComponent\(attachmentId\)\}`\)\)/)
  assert.match(api, /export async function editFeedback/)
  assert.match(api, /api\.put<unknown>\(`\/candidates\/\$\{encodeURIComponent\(candidateId\)\}\/application-form\/feedback\/\$\{encodeURIComponent\(feedbackId\)\}`/)
})

test('a note has an inline Edit (textarea, Save and Cancel) and an edited mark with its time', () => {
  const notes = af('components/feedback-panel.tsx')
  assert.match(notes, /Edit this note/)
  assert.match(notes, /aria-label="Edit note"/)
  assert.match(notes, />Cancel<\/Button>/)
  assert.match(notes, /'Save'/)
  assert.match(notes, /item\.editedAt && /)
  assert.match(notes, /edited \{ago\(item\.editedAt\)\}/)
  const hook = af('use-application-form.ts')
  assert.match(hook, /const changeFeedback = useCallback/)
  assert.match(hook, /const removeDocument = useCallback/)
  const screen = read('screens/candidates/application-form.tsx')
  assert.match(screen, /onEdit: state\.changeFeedback/)
  assert.match(screen, /onRemove=\{removeDocument\}/)
})
