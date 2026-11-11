/** Run Python workspace tasks with the project's environment, on Windows or Unix. */
import { spawn, spawnSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import path from 'node:path'

const backend = fileURLToPath(new URL('../backend/', import.meta.url))
const virtualPython = path.join(
  backend,
  '.venv',
  process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python',
)
const python = process.env.CRM_PYTHON || (existsSync(virtualPython) ? virtualPython : 'python')
const commands = {
  dev: ['-m', 'app.runtime', '--reload', '--host', '127.0.0.1', '--port', '8000'],
  start: ['-m', 'app.runtime', '--host', '127.0.0.1', '--port', '8000'],
  test: ['-m', 'pytest'],
  migrate: ['-m', 'alembic', '-c', 'alembic.ini', 'upgrade', 'head'],
  'db:check': ['-m', 'app.db_check'],
  'db:tunnel': [fileURLToPath(new URL('./db-tunnel.py', import.meta.url))],
  worker: ['-m', 'app.email_worker'],
}
const [task, ...extra] = process.argv.slice(2)
if (!Object.hasOwn(commands, task)) {
  console.error('Choose a backend task: dev, start, worker, test, migrate, db:check, or db:tunnel.')
  process.exit(1)
}

// A private local config opts development into SSH access. Environment overrides
// belong to tests/other installations and must never start this machine's tunnel.
const overridesDatabase = Object.keys(process.env).some((key) =>
  ['CRM_ENV_FILE', 'DATABASE_URL'].includes(key.toUpperCase()),
)
if (['dev', 'start', 'db:check'].includes(task)
    && !overridesDatabase && existsSync(path.join(backend, '.db-tunnel.json'))) {
  const tunnel = spawnSync(python, [fileURLToPath(new URL('./db-tunnel.py', import.meta.url))], {
    cwd: backend,
    stdio: 'inherit',
    windowsHide: true,
    env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' },
  })
  if (tunnel.error || tunnel.status !== 0) {
    console.error('[crm] Could not prepare the database tunnel; the backend has not started.')
    process.exit(tunnel.status || 1)
  }
}

console.log(`[crm] Starting backend ${task}...`)
const child = spawn(python, [...commands[task], ...extra], {
  cwd: backend,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' },
})
child.once('error', (error) => {
  console.error(`Could not start Python: ${error.message}`)
  console.error('Create backend/.venv and install backend/requirements-dev.txt; see README.md.')
  process.exitCode = 1
})
child.once('exit', (code, signal) => {
  process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1)
})
