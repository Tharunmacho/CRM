// Frontend request/render measurement through headless Chrome (Chrome DevTools Protocol, no extra dependencies).
//
//   node scripts/measure-requests.mjs --base http://localhost:5202 --email admin@f9b.test --password 'Passw0rd!x1' \
//        --tag after --out result.json [--idle 30] [--spa] [--pages dashboard,data-bank] [--port 9461] [--candidate c1] [--order o1]
//
// Run it against a SCRATCH stack only (scratch Postgres + API, `vite preview` of a production build or `vite` dev server).
// For every page it does a hard navigation (browser cache warm, like a returning visitor) and reports:
//   api          number of /api requests until the page settled
//   duplicates   /api requests with the same method + URL started within 2 s of an earlier one
//   transfer     bytes on the wire for everything (encodedDataLength), api part separately
//   scriptMs     CDP Performance ScriptDuration; longTasks = count and total of >50 ms main-thread tasks
//   shellMs      time until the app shell (main.workspace or the login form) exists
//   readyMs      time until the shell exists and no skeleton / aria-busy placeholder is left
//   settleMs     time of the last API response
// --idle N additionally watches the dashboard for N seconds and reports API calls per endpoint (polling).
// --hover moves the mouse over the Reports and Add candidate sidebar links and reports what was downloaded before any click.
// --live fires 6 create+delete to-do pairs (live events that change nothing on screen) on dashboard, data-bank and jobs and
//        reports the main-thread work they cost (script, layout, style), the refetches and the DOM nodes touched.
// --noevents blocks the live stream (/api/events) during the idle watch, to see the polling fallback.
// --spa also walks the sidebar by clicking links (no reload) and reports requests per navigation.
import { spawn } from 'node:child_process'
import { writeFile, mkdir, rm } from 'node:fs/promises'
import os from 'node:os'
import path from 'node:path'

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, item, i, all) => {
  if (item.startsWith('--')) acc.push([item.slice(2), all[i + 1] && !all[i + 1].startsWith('--') ? all[i + 1] : true])
  return acc
}, []))
const BASE = args.base ?? 'http://localhost:5173'
const PORT = Number(args.port ?? 9461)
const EMAIL = args.email
const PASSWORD = args.password
const CANDIDATE = args.candidate ?? 'c1'
const ORDER = args.order ?? 'o1'
const OUT = args.out
const chromePath = args.chrome ?? process.env.CHROME_PATH ?? 'C:/Program Files/Google/Chrome/Application/chrome.exe'
if (!EMAIL || !PASSWORD) { console.error('--email and --password are required'); process.exit(2) }

const ALL_PAGES = {
  dashboard: '/',
  'data-bank': '/data-bank',
  'data-bank-resumes': '/data-bank', // resumes list mode (the default shows positions)
  tasks: '/tasks',
  clients: '/clients',
  jobs: '/jobs',
  inbox: '/inbox',
  'candidate-profile': `/candidates/${CANDIDATE}`,
  'application-form': `/candidates/${CANDIDATE}/application?order=${ORDER}`,
  'add-candidate': '/add-candidate',
  reports: '/reports',
}
const wanted = typeof args.pages === 'string' ? args.pages.split(',') : Object.keys(ALL_PAGES)

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
const profile = path.join(os.tmpdir(), `crm-measure-${process.pid}`)
await rm(profile, { recursive: true, force: true }).catch(() => {})
await mkdir(profile, { recursive: true })
const chrome = spawn(chromePath, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${profile}`, '--window-size=1440,900',
  '--no-first-run', '--disable-gpu', '--disable-extensions', 'about:blank',
], { stdio: 'ignore' })
const cleanup = async () => { try { chrome.kill() } catch { /* gone */ } await sleep(300); await rm(profile, { recursive: true, force: true }).catch(() => {}) }
process.on('SIGINT', async () => { await cleanup(); process.exit(130) })

let targets
for (let i = 0; i < 60; i++) {
  try { targets = await (await fetch(`http://127.0.0.1:${PORT}/json`)).json(); if (targets.find((t) => t.type === 'page')) break } catch { /* starting */ }
  await sleep(500)
}
const page = targets?.find((t) => t.type === 'page')
if (!page) { console.error('Chrome did not start'); await cleanup(); process.exit(1) }
const ws = new WebSocket(page.webSocketDebuggerUrl)
await new Promise((resolve) => ws.addEventListener('open', resolve))
let nextId = 0
const pending = new Map()
const listeners = []
ws.addEventListener('message', (event) => {
  const message = JSON.parse(event.data)
  if (message.id && pending.has(message.id)) { pending.get(message.id)(message); pending.delete(message.id) }
  else if (message.method) for (const listener of listeners) listener(message)
})
const send = (method, params = {}) => new Promise((resolve) => { const id = ++nextId; pending.set(id, resolve); ws.send(JSON.stringify({ id, method, params })) })
const evaluate = async (expression) => {
  const reply = await send('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true })
  if (reply.result?.exceptionDetails) throw new Error(JSON.stringify(reply.result.exceptionDetails).slice(0, 400))
  return reply.result?.result?.value
}

// Page-side probes, installed before any page script runs.
await send('Page.enable')
await send('Network.enable')
await send('Performance.enable')
await send('Page.addScriptToEvaluateOnNewDocument', {
  source: `(() => {
    const m = window.__m = { longTasks: [], shell: null, ready: null, fcp: null }
    try { new PerformanceObserver((list) => { for (const e of list.getEntries()) m.longTasks.push(e.duration) }).observe({ type: 'longtask', buffered: true }) } catch {}
    try { new PerformanceObserver((list) => { for (const e of list.getEntries()) if (e.name === 'first-contentful-paint') m.fcp = e.startTime }).observe({ type: 'paint', buffered: true }) } catch {}
    const check = () => {
      const now = performance.now()
      const shell = document.querySelector('main.workspace, form[autocomplete], input[type=password]')
      if (shell && m.shell === null) m.shell = now
      if (shell && m.ready === null && !document.querySelector('.sk-page, [aria-busy="true"], .workspace-loading, .pre-splash, .af-skeleton')) m.ready = now
    }
    new MutationObserver(check).observe(document, { childList: true, subtree: true, attributes: true })
    document.addEventListener('DOMContentLoaded', check)
  })()`,
})

let requests = new Map()
const start = { t: 0 }
listeners.push((message) => {
  const { method, params } = message
  if (method === 'Network.requestWillBeSent') {
    requests.set(params.requestId, { url: params.request.url, method: params.request.method, type: params.type, at: params.timestamp * 1000, bytes: 0, status: 0, finishedAt: null, fromCache: false })
  } else if (method === 'Network.responseReceived') {
    const r = requests.get(params.requestId); if (r) { r.status = params.response.status; r.fromCache = Boolean(params.response.fromDiskCache || params.response.fromServiceWorker) }
  } else if (method === 'Network.loadingFinished') {
    const r = requests.get(params.requestId); if (r) { r.bytes = params.encodedDataLength; r.finishedAt = params.timestamp * 1000 }
  } else if (method === 'Network.loadingFailed') {
    const r = requests.get(params.requestId); if (r) { r.failed = true; r.finishedAt = params.timestamp * 1000 }
  }
})

const isApi = (r) => new URL(r.url).pathname.startsWith('/api/')
const apiKey = (r) => `${r.method} ${new URL(r.url).pathname}${new URL(r.url).search}`
async function settle(maxMs = 25_000, quietMs = 1_500) {
  const begin = Date.now()
  let lastActivity = Date.now()
  let seen = 0
  while (Date.now() - begin < maxMs) {
    await sleep(150)
    const open = [...requests.values()].filter((r) => r.finishedAt === null).length
    if (requests.size !== seen) { seen = requests.size; lastActivity = Date.now() }
    // An open SSE stream never finishes; it does not count as activity.
    const busy = [...requests.values()].filter((r) => r.finishedAt === null && !r.url.includes('/api/events')).length
    if (open >= 0 && busy === 0 && Date.now() - lastActivity > quietMs) return
    if (busy > 0) lastActivity = Date.now()
  }
}
function summarise(origin) {
  const all = [...requests.values()].sort((a, b) => a.at - b.at)
  const api = all.filter(isApi)
  const lastSeen = new Map()
  const dups = []
  for (const r of api) {
    const key = apiKey(r)
    const prev = lastSeen.get(key)
    if (prev !== undefined && r.at - prev < 2000 && r.method === 'GET') dups.push(key) // writes are never duplicates of each other
    lastSeen.set(key, r.at)
  }
  const byKey = {}
  for (const r of api) byKey[apiKey(r)] = (byKey[apiKey(r)] ?? 0) + 1
  const finished = api.filter((r) => r.finishedAt !== null && !r.url.includes('/api/events'))
  return {
    api: api.filter((r) => !r.url.includes('/api/events')).length,
    duplicates: dups.length,
    duplicateKeys: [...new Set(dups)],
    byEndpoint: byKey,
    requestsTotal: all.length,
    transferKB: +(all.reduce((s, r) => s + (r.bytes || 0), 0) / 1024).toFixed(1),
    apiKB: +(api.reduce((s, r) => s + (r.bytes || 0), 0) / 1024).toFixed(1),
    jsKB: +(all.filter((r) => r.type === 'Script').reduce((s, r) => s + (r.bytes || 0), 0) / 1024).toFixed(1),
    settleMs: finished.length ? Math.round(Math.max(...finished.map((r) => r.finishedAt)) - origin) : null,
  }
}
const metricValue = (list, name) => list.find((m) => m.name === name)?.value ?? 0

// Sign in through the API (cookie stays in this profile), then measure with a warm browser cache.
await send('Page.navigate', { url: `${BASE}/login` })
await sleep(2500)
const login = await evaluate(`fetch('/api/auth/login', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ email: ${JSON.stringify(EMAIL)}, password: ${JSON.stringify(PASSWORD)} }) }).then(r => r.status)`)
if (login !== 200 && login !== 204) { console.error('login failed', login); await cleanup(); process.exit(1) }

const results = {}
async function visit(name, url, { settleMs = 25_000 } = {}) {
  requests = new Map()
  await send('Page.navigate', { url: 'about:blank' })
  await sleep(300)
  requests = new Map()
  const before = await send('Performance.getMetrics')
  const origin = performance.timeOrigin + performance.now()
  void origin
  const nav = await send('Page.navigate', { url: `${BASE}${url}` })
  const navStarted = Date.now()
  void nav
  await sleep(400)
  const first = [...requests.values()].sort((a, b) => a.at - b.at)[0]
  const originSeconds = first ? first.at : Date.now()
  await settle(settleMs)
  const metrics = (await send('Performance.getMetrics')).result.metrics
  const probe = JSON.parse(await evaluate(`JSON.stringify({ m: window.__m, path: location.pathname, fonts: [...document.fonts].filter((f) => f.status === 'loaded').map((f) => f.family.replace(/"/g, '') + ' ' + f.weight + (f.style === 'italic' ? 'i' : '')) })`))
  const long = probe.m?.longTasks ?? []
  const base = summarise(originSeconds)
  const entry = {
    url,
    landedOn: probe.path,
    ...base,
    shellMs: probe.m?.shell === null ? null : Math.round(probe.m.shell),
    readyMs: probe.m?.ready === null ? null : Math.round(probe.m.ready),
    fcpMs: probe.m?.fcp === null ? null : Math.round(probe.m.fcp),
    scriptMs: Math.round(metricValue(metrics, 'ScriptDuration') * 1000),
    taskMs: Math.round(metricValue(metrics, 'TaskDuration') * 1000),
    fonts: probe.fonts,
    longTasks: long.length,
    longTaskMs: Math.round(long.reduce((s, d) => s + d, 0)),
    heapMB: +(metricValue(metrics, 'JSHeapUsedSize') / 1048576).toFixed(1),
    wall: Date.now() - navStarted,
  }
  void before
  results[name] = entry
  console.log(`${name.padEnd(18)} api=${String(entry.api).padStart(2)} dup=${String(entry.duplicates).padStart(2)} ready=${String(entry.readyMs ?? '-').padStart(5)}ms shell=${String(entry.shellMs ?? '-').padStart(5)}ms settle=${String(entry.settleMs ?? '-').padStart(5)}ms script=${String(entry.scriptMs).padStart(4)}ms long=${entry.longTasks}/${entry.longTaskMs}ms xfer=${entry.transferKB}KB (js ${entry.jsKB}, api ${entry.apiKB})${entry.landedOn !== url.split('?')[0] ? '  LANDED ' + entry.landedOn : ''}`)
  if (name === 'dashboard-warm' || name === 'data-bank') console.log('    fonts:', entry.fonts.join(', '))
  if (entry.duplicates) console.log('    duplicates:', entry.duplicateKeys.join(' | '))
  return entry
}

// First visit of the dashboard with the browser cache disabled, then everything else warm.
await send('Network.setCacheDisabled', { cacheDisabled: true })
await visit('dashboard-cold', '/')
await send('Network.setCacheDisabled', { cacheDisabled: false })
for (const name of wanted) {
  // The Data Bank remembers its list mode in localStorage; set it while a page of this origin is loaded.
  await evaluate(`localStorage.${name === 'data-bank-resumes' ? "setItem('crm-db-mode:data-bank', 'resumes')" : "removeItem('crm-db-mode:data-bank')"}`)
  await visit(name === 'dashboard' ? 'dashboard-warm' : name, ALL_PAGES[name])
}

if (args.idle) {
  const seconds = Number(args.idle)
  if (args.noevents) await send('Network.setBlockedURLs', { urls: ['*/api/events*'] })
  requests = new Map()
  await send('Page.navigate', { url: `${BASE}/` })
  await settle(25_000)
  requests = new Map()
  console.log(`watching the dashboard idle for ${seconds}s ...`)
  await sleep(seconds * 1000)
  const idle = summarise(0)
  results.idle = { seconds, api: idle.api, byEndpoint: idle.byEndpoint }
  console.log('idle api calls:', idle.api, JSON.stringify(idle.byEndpoint))
}

if (args.live) {
  results.live = {}
  const cost = (list) => ({ script: metricValue(list, 'ScriptDuration'), task: metricValue(list, 'TaskDuration'), layout: metricValue(list, 'LayoutDuration'), style: metricValue(list, 'RecalcStyleDuration') })
  for (const name of ['dashboard', 'data-bank', 'jobs']) {
    requests = new Map()
    await send('Page.navigate', { url: `${BASE}${ALL_PAGES[name]}` })
    await settle(25_000)
    await sleep(2000)
    await evaluate(`(() => { window.__mut = 0; new MutationObserver((l) => { window.__mut += l.length }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true }) })()`)
    requests = new Map()
    const first = cost((await send('Performance.getMetrics')).result.metrics)
    for (let i = 0; i < 6; i++) {
      await evaluate(`(async () => { const r = await fetch('/api/todos', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ title: 'perf probe', dueAt: new Date(Date.now() + 86400000).toISOString(), priority: 'low' }) }); if (r.ok) { const t = await r.json(); await fetch('/api/todos/' + t.id, { method: 'DELETE' }) } return r.status })()`)
      await sleep(900)
    }
    await sleep(3000)
    const last = cost((await send('Performance.getMetrics')).result.metrics)
    const mutations = await evaluate('window.__mut')
    const s = summarise(0)
    const ms = (key) => Math.round((last[key] - first[key]) * 1000)
    results.live[name] = { scriptMs: ms('script'), taskMs: ms('task'), layoutMs: ms('layout'), styleMs: ms('style'), domMutations: mutations, api: s.api, byEndpoint: s.byEndpoint }
    console.log(`live ${name.padEnd(10)} script=${ms('script')}ms task=${ms('task')}ms layout=${ms('layout')}ms style=${ms('style')}ms domMutations=${mutations} api=${s.api} ${JSON.stringify(s.byEndpoint)}`)
  }
}

if (args.hover) {
  await send('Network.setCacheDisabled', { cacheDisabled: true })
  await send('Page.navigate', { url: `${BASE}/` })
  await settle(25_000)
  await sleep(1500)
  results.hover = {}
  for (const href of ['/reports', '/add-candidate']) {
    requests = new Map()
    const box = await evaluate(`(() => { const a = document.querySelector('nav a[href="${href}"]'); if (!a) return null; const r = a.getBoundingClientRect(); return JSON.stringify({ x: r.left + r.width / 2, y: r.top + r.height / 2 }) })()`)
    if (!box) { console.log(`hover ${href}: no such sidebar link for this account`); continue }
    const { x, y } = JSON.parse(box)
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x: x - 40, y, button: 'none' })
    await send('Input.dispatchMouseEvent', { type: 'mouseMoved', x, y, button: 'none' })
    await sleep(2500)
    const got = [...requests.values()].map((r) => `${r.type}:${new URL(r.url).pathname.split('/').pop()}`)
    results.hover[href] = got
    console.log(`hover ${href}: ${got.length} requests before any click`, got.join(' '))
  }
  await send('Network.setCacheDisabled', { cacheDisabled: false })
}

if (args.spa) {
  await send('Page.navigate', { url: `${BASE}/` })
  await settle(25_000)
  const hrefs = JSON.parse(await evaluate(`JSON.stringify([...document.querySelectorAll('nav a[href]')].map(a => a.getAttribute('href')).filter(h => h.startsWith('/')))`))
  results.spa = {}
  for (const href of hrefs.slice(0, 12)) {
    requests = new Map()
    const clicked = await evaluate(`(() => { const a = document.querySelector('nav a[href="${href}"]'); if (!a) return false; a.click(); return true })()`)
    if (!clicked) continue
    await sleep(300)
    await settle(15_000)
    const s = summarise(0)
    results.spa[href] = { api: s.api, duplicates: s.duplicates, byEndpoint: s.byEndpoint }
    console.log(`spa ${href.padEnd(20)} api=${s.api} dup=${s.duplicates}${s.duplicates ? '  ' + s.duplicateKeys.join(' | ') : ''}`)
  }
}

if (OUT) await writeFile(OUT, JSON.stringify(results, null, 2))
ws.close()
await cleanup()
process.exit(0)
