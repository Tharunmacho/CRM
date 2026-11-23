import assert from 'node:assert/strict'
import { readFile } from 'node:fs/promises'
import test from 'node:test'

const src = (path) => readFile(new URL(`../src/${path}`, import.meta.url), 'utf8')

test('the Job requirements sheet follows other people\'s changes live, keeps loaded rows and never wipes an unsaved edit', async () => {
  const hook = await src('features/job-requirements/use-job-requirements.ts')
  assert.match(hook, /import \{ liveInterval \} from '@\/shared\/state\/live'/)
  assert.match(hook, /liveInterval\(\(\) => \{[\s\S]*?\}, 30_000, \['job_requirements'\]/, 'a topic subscription with a 30 s poll fallback')
  assert.match(hook, /readBlocked\(inFlight\.current, Object\.keys\(unsaved\.current\)\.length\)\) \{ rerun\.current = true; return \}/, 'nothing is re-read while a typed change is unsaved or being sent; the skipped read runs afterwards')
  assert.match(hook, /Math\.max\(PAGE_LIMIT, itemsRef\.current\.length\)/, 'a live refresh asks for at least the rows already loaded')
})

test('the workspace (Clients, Jobs, Candidates, Users, Settings) has a timer fallback, not only live events and route changes', async () => {
  const store = await src('shared/state/crm-store.tsx')
  assert.match(store, /const WORKSPACE_POLL_MS = 30_000/)
  assert.match(store, /liveInterval\(reload, WORKSPACE_POLL_MS, WORKSPACE_TOPICS/)
  assert.doesNotMatch(store, /live\.subscribe\(WORKSPACE_TOPICS, reload/, 'the subscription alone left every workspace screen stale when the stream is down')
})

test('the numbers-to-ignore card and the dashboard Inbox card follow live changes', async () => {
  const card = await src('features/whatsapp/ignored-numbers.tsx')
  assert.match(card, /liveInterval\(\(\) => \{ if \(document\.visibilityState !== 'hidden'\) return load\(\) \}, 60_000, \['whatsapp'\]/)
  const dash = await src('screens/dashboard/dashboard.tsx')
  assert.match(dash, /'\/inbox\?limit=1', store\.me\.id, '', \['mail', 'documents'\]/)
})

test('lists that other people change are re-read within a minute even if a live event is delayed (the 2-minute default is not left to them)', async () => {
  assert.match(await src('screens/candidates/data-bank.tsx'), /safetyMs: LIVE_RELOAD_POLL_MS/)
  assert.match(await src('features/leads/use-leads.ts'), /safetyMs: 60_000/)
  assert.match(await src('features/job-requirements/use-job-requirements.ts'), /safetyMs: 30_000/)
  assert.match(await src('shared/state/crm-store.tsx'), /safetyMs: 60_000/)
})
