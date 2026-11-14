import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import { test } from 'node:test'

const read = (relative) => readFile(new URL(relative, import.meta.url), 'utf8')
const page = await read('../src/screens/clients/clients.tsx')

test('the client list offers Delete in the table row and on the card, only to roles that may delete records', () => {
  assert.match(page, /const canDelete = store\.can\('delete_records'\)/)
  assert.match(page, /onDelete=\{canDelete \? setDeletingClient : undefined\}/g)
  // Table: the same 32px icon button the agency rows use, beside Edit (hidden without permission).
  assert.match(page, /\{onDelete && <button type="button" className="cs-icon-act is-danger"[^\n]*aria-label=\{`Delete \$\{row\.agencyName \|\| row\.client\.name\}`\}/)
  // Card: the same equal-width destructive button the agency card uses.
  assert.match(page, /\{onDelete && <button type="button" className="cl-open cs-wide is-danger"[^\n]*aria-label=\{`Delete \$\{name\}`\}/)
})

test('deleting a client goes through the shared confirm dialog that names the record and shows the server refusal', async () => {
  assert.match(page, /<DeleteRecordDialog kind="client" id=\{deletingClient\.id\}/)
  const dialog = await read('../src/shared/components/delete-dialog.tsx')
  assert.match(dialog, /Delete \$\{name \?\? `this \$\{word\}`\}\?/)
  assert.match(dialog, /role="alert"/)
  assert.match(dialog, /Change status instead/)
})

test('a row click on the table is not triggered by the Delete button', () => {
  assert.match(page, /onClick=\{\(event\) => \{ event\.stopPropagation\(\); onDelete\(row\.client\) \}\}/)
})
