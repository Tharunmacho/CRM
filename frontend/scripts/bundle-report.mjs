// Bundle report: node scripts/bundle-report.mjs [distDir]
// Prints the first-load JS/CSS (entry + its static imports, gzip and raw) and the biggest lazy chunks.
// First-load per route is the entry closure plus the shell and the screen chunk, as index.html preloads them.
import { readFileSync, readdirSync } from 'node:fs'
import { gzipSync } from 'node:zlib'
import path from 'node:path'

const dist = path.resolve(process.argv[2] ?? 'dist')
const assets = path.join(dist, 'assets')
const files = readdirSync(assets)
const read = (name) => readFileSync(path.join(assets, name))
const size = (name) => ({ raw: read(name).length, gz: gzipSync(read(name)).length })
const kb = (n) => (n / 1024).toFixed(1)

/** Static imports only: `import ... from"./x.js"` and `import"./x.js"`; dynamic import("./x.js") is not followed. */
function staticImports(name) {
  if (!name.endsWith('.js')) return []
  const text = read(name).toString('utf8')
  const found = new Set()
  for (const match of text.matchAll(/(?:from|import)\s*["']\.\/([^"']+\.js)["']/g)) found.add(match[1])
  return [...found]
}
function closure(start) {
  const seen = new Set()
  const stack = [start]
  while (stack.length) {
    const next = stack.pop()
    if (seen.has(next) || !files.includes(next)) continue
    seen.add(next)
    stack.push(...staticImports(next))
  }
  return seen
}
function cssOf(names) {
  const css = new Set()
  for (const name of names) {
    const text = read(name).toString('utf8')
    for (const m of text.matchAll(/["']([^"']*\.css)["']/g)) { const base = path.basename(m[1]); if (files.includes(base)) css.add(base) }
  }
  return css
}
const html = readFileSync(path.join(dist, 'index.html'), 'utf8')
const entry = path.basename(html.match(/src="[^"]*\/(index-[^"]+\.js)"/)[1])
const entryCss = [...html.matchAll(/href="[^"]*\/([^"/]+\.css)"/g)].map((m) => m[1])
const sum = (names) => names.reduce((a, n) => { const s = size(n); return { raw: a.raw + s.raw, gz: a.gz + s.gz } }, { raw: 0, gz: 0 })

const base = closure(entry)
const baseFiles = [...base, ...entryCss]
const total = sum(baseFiles)
console.log(`ENTRY ${entry}: ${base.size} js files + ${entryCss.length} css -> first-load ${kb(total.raw)} KB raw / ${kb(total.gz)} KB gzip`)
for (const n of baseFiles) { const s = size(n); console.log(`   ${n.padEnd(46)} ${kb(s.raw).padStart(8)} KB  ${kb(s.gz).padStart(7)} gz`) }

const screens = {
  '/': 'dashboard-', '/inbox': 'inbox-', '/clients': 'clients-', '/jobs': 'jobs-', '/add-candidate': 'add-candidate-',
  '/data-bank': 'data-bank-', '/tasks': 'tasks-', '/reports': 'reports-', '/candidates/:id': 'candidate-profile-', '/application-form': 'application-form-',
}
const shell = files.find((f) => /^app-shell-.*\.js$/.test(f))
console.log('\nROUTE first-load (entry + shell + screen chunk and their static imports + css):')
for (const [route, prefix] of Object.entries(screens)) {
  const screen = files.filter((f) => f.startsWith(prefix) && f.endsWith('.js'))
  const names = new Set(base)
  for (const start of [shell, ...screen]) for (const n of closure(start)) names.add(n)
  const css = new Set([...entryCss, ...cssOf([...names])])
  const all = [...names, ...css]
  const s = sum(all)
  console.log(`   ${route.padEnd(20)} ${String(all.length).padStart(3)} files  ${kb(s.raw).padStart(8)} KB raw  ${kb(s.gz).padStart(7)} KB gzip`)
}
const lazy = files.filter((f) => f.endsWith('.js') && !base.has(f)).map((f) => ({ f, ...size(f) })).sort((a, b) => b.raw - a.raw).slice(0, 12)
console.log('\nLARGEST LAZY CHUNKS:')
for (const c of lazy) console.log(`   ${c.f.padEnd(46)} ${kb(c.raw).padStart(8)} KB  ${kb(c.gz).padStart(7)} gz`)
const all = files.filter((f) => f.endsWith('.js')).map(size)
console.log(`\nTOTAL JS ${files.filter((f) => f.endsWith('.js')).length} files: ${kb(all.reduce((a, s) => a + s.raw, 0))} KB raw`)
