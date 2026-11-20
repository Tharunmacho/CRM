import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import path from 'node:path'
import { fileURLToPath } from 'node:url'
import test from 'node:test'

const root = fileURLToPath(new URL('../src/', import.meta.url))
const read = (relative) => readFile(path.join(root, relative), 'utf8')

const SCREENS = [
  ['screens/clients/clients.tsx', 'ops-clients'],
  ['screens/clients/clients.tsx', 'ops-agencies'],
  ['screens/jobs/jobs.tsx', 'ops-jobs'],
  ['screens/users/users.tsx', 'ops-users'],
  ['screens/users/currency-card.tsx', 'ops-currencies'],
  ['screens/inbox/whatsapp.tsx', 'ops-whatsapp'],
  ['features/clients/components/agency-import-dialog.tsx', 'ops-import'],
  ['screens/jobs/order-detail.tsx', 'ops-matched'],
  ['features/reports/fee-collections.tsx', 'ops-fees'],
]

test('every ops table sits under ops-fit and loads the one sheet', async () => {
  for (const [file, cls] of SCREENS) {
    const source = await read(file)
    assert.ok(source.includes(cls), `${file} carries ${cls}`)
    assert.match(source, /@\/styles\/ops-tables\.css/, `${file} imports ops-tables.css`)
  }
})

test('no ops table has an inner scroll box or a country flag in a cell', async () => {
  for (const file of ['screens/clients/clients.tsx', 'screens/jobs/jobs.tsx', 'screens/users/currency-card.tsx']) {
    assert.doesNotMatch(await read(file), /sticky maxHeight|<DataTable[^>]* sticky/, `${file}: the page scrolls`)
  }
  assert.doesNotMatch(await read('screens/clients/clients.tsx'), /<Flag/)
  assert.doesNotMatch(await read('screens/jobs/jobs.tsx'), /<Flag/)
})

test('the column shares of each list add up to 100 per cent, so the table fills its panel and never scrolls sideways', async () => {
  const sum = (source, from, to) => [...source.slice(source.indexOf(from), source.indexOf(to)).matchAll(/width: '(\d+)%'/g)].reduce((n, m) => n + Number(m[1]), 0)
  const actions = { clients: 21, agencies: 15, jobs: 17, users: 16 }
  const clients = await read('screens/clients/clients.tsx')
  assert.equal(sum(clients, 'function ClientTable(', 'function AgencyTable(') + actions.clients, 100)
  assert.equal(sum(clients, 'function AgencyTable(', 'function Skeleton(') + actions.agencies, 100)
  assert.equal(sum(await read('screens/jobs/jobs.tsx'), 'function JobTable(', 'function Skeleton(') + actions.jobs, 100)
  assert.equal(sum(await read('screens/users/users.tsx'), 'const columns: Column<User>[]', 'const toggleStatus') + actions.users, 100)
})

test('the sheet fits, wraps at spaces and uses the shared frame tokens', async () => {
  const css = await read('styles/ops-tables.css')
  assert.match(css, /\.ops-fit \.ui-data-table table \{ width: 100%; min-width: 0; table-layout: fixed; \}/)
  assert.match(css, /overflow-x: clip/)
  assert.doesNotMatch(css, /overflow-wrap: anywhere/, 'words are only broken at spaces')
  assert.match(css, /border-radius: var\(--table-frame-radius, 6px\)/)
  assert.match(css, /var\(--table-frame-width, 1px\)/)
  assert.match(css, /var\(--table-column-line/)
  assert.doesNotMatch(css, /border-radius: (10|12|16)px/)
})

test('fee collections: a fixed table whose two low-priority columns fold away below 1280px', async () => {
  const source = await read('features/reports/fee-collections.tsx')
  assert.match(source, /<colgroup>/)
  assert.equal([...source.matchAll(/fc-low/g)].length, 4)
  const css = await read('styles/ops-tables.css')
  const widths = [...css.matchAll(/col\.fc-\w+ \{ width: (\d+)%; \}/g)].reduce((n, m) => n + Number(m[1]), 0)
  assert.equal(widths, 100)
  assert.match(css, /@media \(max-width: 1279px\) \{ \.ops-fees :is\(\.fc-low/)
})

test('the client-detail candidate list is framed with the thin 6px frame inside the job card', async () => {
  const css = await read('styles/ops-tables.css')
  assert.match(css, /\.cd-order-list \.cd-cands \{[^}]*border: var\(--table-frame-width, 1px\) solid var\(--table-line-strong[^}]*border-radius: var\(--table-frame-radius, 6px\); overflow: hidden;/)
})
