import assert from 'node:assert/strict'
import test from 'node:test'
import { EventEmitter } from 'node:events'
import { monitorTunnel, startTunnelCheck, usesManagedTunnel } from '../db-tunnel-monitor.mjs'

test('only normal dev and start commands monitor the explicitly configured local tunnel', () => {
  for (const task of ['dev', 'start']) {
    assert.equal(usesManagedTunnel(task, {}, true), true)
    assert.equal(usesManagedTunnel(task, {}, false), false)
    for (const key of ['DATABASE_URL', 'database_url', 'CRM_ENV_FILE', 'crm_env_file']) {
      assert.equal(usesManagedTunnel(task, { [key]: 'test override' }, true), false)
    }
  }
  for (const task of ['test', 'migrate', 'db:check', 'db:tunnel', 'worker']) {
    assert.equal(usesManagedTunnel(task, {}, true), false)
  }
})

function fixture({ listening = true, succeeds = true } = {}) {
  let scheduled, attempts = 0, cancelled = 0
  const delays = [], logs = []
  const state = { listening, succeeds }
  const stop = monitorTunnel({ port: 55436,
    probe: async port => { assert.equal(port, 55436); return state.listening },
    run: () => { attempts++; return { promise: Promise.resolve(state.succeeds), cancel: () => { cancelled++ } } },
    log: value => logs.push(value),
    setTimer: (fn, delay) => { scheduled = fn; delays.push(delay); return fn },
    clearTimer: fn => { if (scheduled === fn) scheduled = undefined },
  })
  return { state, stop, delays, logs, attempts: () => attempts, cancelled: () => cancelled,
    pending: () => Boolean(scheduled), tick: async () => { const fn = scheduled; scheduled = undefined; await fn() } }
}

test('healthy listeners are left untouched and stopping cancels the next probe', async () => {
  const env = fixture()
  await env.tick(); await env.tick()
  assert.equal(env.attempts(), 0)
  assert.deepEqual(env.logs, [])
  env.stop()
  assert.equal(env.pending(), false)
})

test('a disconnected tunnel reconnects once and returns to normal polling', async () => {
  const env = fixture({ listening: false })
  await env.tick()
  assert.equal(env.attempts(), 1)
  assert.match(env.logs[0], /disconnected/)
  assert.match(env.logs[1], /restored/)
  env.state.listening = true
  await env.tick()
  assert.equal(env.attempts(), 1)
  assert.equal(env.delays.at(-1), 15_000)
  env.stop()
})

test('failed reconnects use capped backoff without repeated warning logs', async () => {
  const env = fixture({ listening: false, succeeds: false })
  for (let i = 0; i < 6; i++) await env.tick()
  assert.deepEqual(env.delays, [15_000, 30_000, 60_000, 120_000, 120_000, 120_000, 120_000])
  assert.equal(env.logs.length, 2)
  env.state.listening = true
  await env.tick()
  assert.match(env.logs.at(-1), /restored/)
  assert.equal(env.delays.at(-1), 15_000)
  env.stop()
})

test('stopping during a reconnect cancels its helper and never schedules another check', async () => {
  let check, resolve, cancelled = 0, timerCount = 0
  const stop = monitorTunnel({ port: 55436, probe: async () => false, log: () => {},
    run: () => ({ promise: new Promise(done => { resolve = done }), cancel: () => { cancelled++ } }),
    setTimer: fn => { timerCount++; check = fn; return fn }, clearTimer: () => {},
  })
  const running = check()
  await Promise.resolve()
  stop()
  resolve(false)
  await running
  assert.equal(cancelled, 1)
  assert.equal(timerCount, 1)
})

test('Windows cancellation hides and stops only the owned Python helper tree', async () => {
  const calls = []
  const child = Object.assign(new EventEmitter(), { pid: 123, exitCode: null, signalCode: null })
  const run = startTunnelCheck('python', 'db-tunnel.py', 'workspace', {
    platform: 'win32', spawnProcess: (...args) => { calls.push(args); return calls.length === 1 ? child : new EventEmitter() },
  })
  run.cancel(); run.cancel()
  assert.equal(calls.length, 2)
  assert.equal(calls[0][2].windowsHide, true)
  assert.deepEqual(calls[1], ['taskkill.exe', ['/PID', '123', '/T', '/F'], { windowsHide: true, stdio: 'ignore' }])
  child.exitCode = 1
  child.emit('exit', 1)
  assert.equal(await run.promise, false)
})
