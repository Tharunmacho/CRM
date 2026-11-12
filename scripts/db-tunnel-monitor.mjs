/** Keep an explicitly configured local-development SSH tunnel available while the backend runs. */
import net from 'node:net'
import { spawn } from 'node:child_process'

export function usesManagedTunnel(task, environment, hasConfig) {
  return hasConfig && ['dev', 'start'].includes(task)
    && !Object.keys(environment).some(key => ['CRM_ENV_FILE', 'DATABASE_URL'].includes(key.toUpperCase()))
}

export function tunnelListening(port) {
  return new Promise(resolve => {
    const socket = net.createConnection({ host: '127.0.0.1', port })
    let done = false
    const finish = value => { if (!done) { done = true; socket.destroy(); resolve(value) } }
    socket.setTimeout(2000)
    socket.once('connect', () => finish(true))
    socket.once('timeout', () => finish(false))
    socket.once('error', () => finish(false))
  })
}

export function startTunnelCheck(python, script, backend, { spawnProcess = spawn, platform = process.platform } = {}) {
  // Reuse the pinned SSH key, process-identity checks and loopback forwarding of the existing helper.
  const child = spawnProcess(python, [script], {
    cwd: backend, windowsHide: true, stdio: 'ignore',
    env: { ...process.env, PYTHONUTF8: '1', PYTHONUNBUFFERED: '1' },
  })
  let cancelled = false
  const cancel = () => {
    if (cancelled || !child.pid || child.exitCode !== null || child.signalCode != null) return
    cancelled = true
    if (platform === 'win32') {
      // The Windows venv launcher starts another Python process: stop this owned helper tree, not just its wrapper.
      const killer = spawnProcess('taskkill.exe', ['/PID', String(child.pid), '/T', '/F'], { windowsHide: true, stdio: 'ignore' })
      killer.once('error', () => child.kill())
    } else child.kill()
  }
  const promise = new Promise(resolve => {
    const timer = setTimeout(cancel, 65_000)
    const finish = ok => { clearTimeout(timer); resolve(ok) }
    child.once('error', () => finish(false))
    child.once('exit', code => finish(code === 0))
  })
  return { promise, cancel }
}

export function monitorTunnel({ port, run, probe = tunnelListening, log = console.log,
  setTimer = setTimeout, clearTimer = clearTimeout, interval = 15_000, maxBackoff = 120_000 }) {
  let stopped = false, timer, active, failures = 0, disconnected = false
  const schedule = delay => { if (!stopped) timer = setTimer(check, delay) }
  const check = async () => {
    timer = undefined
    try {
      const listening = await probe(port)
      if (stopped) return
      if (listening) {
        if (disconnected) log('[crm] Database tunnel connection restored.')
        disconnected = false; failures = 0
      } else {
        if (!disconnected) log('[crm] Database tunnel disconnected; reconnecting automatically...')
        disconnected = true
        active = run()
        const ok = await active.promise
        active = undefined
        if (stopped) return
        if (ok) {
          log('[crm] Database tunnel connection restored.')
          disconnected = false; failures = 0
        } else {
          failures += 1
          if (failures === 1) log('[crm] Database tunnel is unavailable. Automatic retries will continue; check server access if it persists.')
        }
      }
    } catch {
      active = undefined
      if (stopped) return
      failures += 1
      if (failures === 1) log('[crm] Database tunnel check failed. Automatic retries will continue.')
    }
    schedule(Math.min(maxBackoff, interval * (2 ** Math.min(failures, 4))))
  }
  schedule(interval)
  return () => {
    stopped = true
    if (timer !== undefined) clearTimer(timer)
    active?.cancel()
    active = undefined
  }
}
