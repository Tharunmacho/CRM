/** Run Python workspace tasks with the project's environment, on Windows or Unix. */
import { spawn } from 'node:child_process'
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
  worker: ['-m', 'app.email_worker'],
}
const [task, ...extra] = process.argv.slice(2)
if (!Object.hasOwn(commands, task)) {
  console.error('Choose a backend task: dev, start, worker, test, or migrate.')
  process.exit(1)
}

const child = spawn(python, [...commands[task], ...extra], {
  cwd: backend,
  stdio: 'inherit',
  env: { ...process.env, PYTHONUTF8: '1' },
})
child.once('error', (error) => {
  console.error(`Could not start Python: ${error.message}`)
  console.error('Create backend/.venv and install backend/requirements-dev.txt; see README.md.')
  process.exitCode = 1
})
child.once('exit', (code, signal) => {
  process.exitCode = code ?? (signal === 'SIGINT' ? 130 : 1)
})
