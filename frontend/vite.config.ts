import { defineConfig, loadEnv, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

/** First screen of each top-level URL (the file Vite turns into that route's chunk). */
const SCREENS: Record<string, string> = {
  '/': 'src/screens/dashboard/dashboard.tsx',
  '/inbox': 'src/screens/inbox/inbox.tsx',
  '/clients': 'src/screens/clients/clients.tsx',
  '/jobs': 'src/screens/jobs/jobs.tsx',
  '/add-candidate': 'src/screens/candidates/add-candidate.tsx',
  '/data-bank': 'src/screens/candidates/data-bank.tsx',
  '/leads': 'src/screens/leads/leads.tsx',
  '/leads/import': 'src/screens/leads/leads.tsx',
  '/tasks': 'src/screens/tasks/tasks.tsx',
}
const SHELL = 'src/app/layout/app-shell.tsx'

/**
 * Lets index.html fetch the app shell and the first screen's code alongside the entry bundle.
 *
 * Both are lazy chunks, so the browser would otherwise discover them only after the entry bundle ran, the
 * session answered and the shell rendered: a chain of downloads before the first screen. The list below is
 * built from the real bundle (chunk names are hashed), so it can never point at a stale file.
 */
function earlyChunks(apiBase = ''): Plugin {
  let base = '/'
  return {
    name: 'crm-early-chunks',
    configResolved(config) {
      base = config.base.endsWith('/') ? config.base : `${config.base}/`
    },
    transformIndexHtml: {
      order: 'post',
      handler(html, ctx) {
        const bundle = ctx.bundle
        const withBase = (text: string) => text.replace('__CRM_API_BASE__', JSON.stringify(apiBase).slice(1, -1))
        if (!bundle) return withBase(html.replace('__CRM_CHUNK_TABLE__', '{}'))
        const chunks = Object.values(bundle).filter((item) => item.type === 'chunk')
        const byName = new Map(chunks.map((chunk) => [chunk.fileName, chunk]))
        const closure = (start: string, seen = new Set<string>()) => {
          const pending = [start]
          while (pending.length) {
            const name = pending.pop() as string
            if (seen.has(name)) continue
            seen.add(name)
            for (const next of byName.get(name)?.imports ?? []) pending.push(next)
          }
          return seen
        }
        const entry = chunks.find((chunk) => chunk.isEntry)
        const inEntry = entry ? closure(entry.fileName) : new Set<string>()
        const find = (source: string) => chunks.find((chunk) => chunk.facadeModuleId?.replace(/\\/g, '/').endsWith(source))
        const files = (...sources: string[]) => {
          const names = new Set<string>()
          for (const source of sources) {
            const chunk = find(source)
            if (chunk) closure(chunk.fileName, names)
          }
          const urls: string[] = []
          for (const name of names) {
            if (inEntry.has(name)) continue
            urls.push(`${base}${name}`)
            for (const css of byName.get(name)?.viteMetadata?.importedCss ?? []) urls.push(`${base}${css}`)
          }
          return urls
        }
        const table: Record<string, string[]> = {}
        for (const [route, source] of Object.entries(SCREENS)) table[route] = files(SHELL, source)
        return withBase(html.replace('__CRM_CHUNK_TABLE__', JSON.stringify(table)))
      },
    },
  }
}

type ModuleLookup = (id: string) => { importedIds: readonly string[] } | null

/** Every module the entry bundle imports statically (directly or through others), found once per build from `src/main.tsx`. */
let entryGraph: Set<string> | null = null
function inEntryGraph(id: string, getModuleInfo: ModuleLookup): boolean {
  if (!entryGraph) {
    const seen = new Set<string>()
    const pending = [path.resolve(__dirname, 'src/main.tsx').split(path.sep).join('/')]
    while (pending.length) {
      const next = pending.pop() as string
      if (seen.has(next)) continue
      seen.add(next)
      pending.push(...(getModuleInfo(next)?.importedIds ?? []))
    }
    entryGraph = seen
  }
  return entryGraph.has(id)
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, __dirname, 'API_')
  const apiBase = loadEnv(mode, __dirname, 'VITE_').VITE_API_URL ?? ''

  return {
    plugins: [react(), earlyChunks(apiBase)],
    resolve: { alias: { '@': path.resolve(__dirname, './src') } },
    build: {
      rollupOptions: {
        output: {
          // Libraries change far less often than the app, so keeping them in their own files means a deploy
          // does not make every returning visitor download React again; charts stay out of every screen
          // except Reports.
          manualChunks(id, { getModuleInfo }) {
            if (!id.includes('node_modules')) return undefined
            // clsx and tailwind-merge are used by every screen. Left unassigned, clsx was pulled into the charts chunk
            // (its other importer), so the entry bundle statically imported 375 KB of recharts on every page load.
            if (/node_modules\/(react|react-dom|scheduler|clsx|tailwind-merge)\//.test(id)) return 'vendor-react'
            if (/node_modules\/(react-router|react-router-dom|@remix-run)\//.test(id)) return 'vendor-router'
            // lucide-react is one module per icon; left alone Rollup made a tiny chunk per icon (about 60), and every screen
            // modulepreloaded the ones it uses. The icons the screens use go into one shared chunk (one request, cached across
            // screens). Anything the entry bundle already imports stays with the entry, so first load does not download icons
            // that only lazy screens use.
            if (/node_modules\/lucide-react\//.test(id)) return inEntryGraph(id, getModuleInfo) ? undefined : 'vendor-icons'
            if (/node_modules\/(recharts|d3-[^/]+|victory-vendor|recharts-scale|internmap|decimal\.js-light)\//.test(id)) return 'vendor-charts'
            return undefined
          },
        },
      },
    },
    server: {
      port: 5173,
      // Transform the shell and Candidate form before first navigation as well as
      // login. Only these entry graphs are warmed; optional dialogs remain lazy.
      warmup: {
        clientFiles: ['./index.html', './src/main.tsx', './src/screens/auth/login.tsx',
          './src/app/layout/app-shell.tsx', './src/screens/candidates/add-candidate.tsx'],
      },
      proxy: {
        '/api': {
          target: env.API_PROXY_TARGET || 'http://127.0.0.1:8000',
          changeOrigin: true,
        },
      },
    },
  }
})
