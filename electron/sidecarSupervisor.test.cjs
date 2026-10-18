const test = require('node:test')
const assert = require('node:assert/strict')
const { isHealthyPayload, waitForHealthy, restartDecision, terminateProcessTree, parseBoundedJson } = require('./sidecarSupervisor.cjs')

test('health validation requires the expected local engine payload', () => {
  assert.equal(isHealthyPayload({ status: 'ok', service: 'flujo-local-engine-room' }), true)
  assert.equal(isHealthyPayload({ status: 'ok' }), false)
  assert.equal(isHealthyPayload({ status: 'error', service: 'flujo-local-engine-room' }), false)
})

test('health wait returns ready only after a valid response and reports malformed responses', async () => {
  let probes = 0
  let clock = 0
  const result = await waitForHealthy(async () => {
    probes += 1
    if (probes < 3) return { ok: true, payload: { status: 'ok' } }
    return { ok: true, payload: { status: 'ok', service: 'flujo-local-engine-room' } }
  }, { timeoutMs: 100, intervalMs: 10, now: () => clock, sleep: async ms => { clock += ms } })
  assert.deepEqual(result, { ready: true, reason: 'healthy' })
  assert.equal(probes, 3)

  clock = 0
  const malformed = await waitForHealthy(async () => ({ ok: true, payload: '<html>bad gateway</html>', reason: 'malformed_health_response' }), { timeoutMs: 25, intervalMs: 10, now: () => clock, sleep: async ms => { clock += ms } })
  assert.equal(malformed.ready, false)
  assert.equal(malformed.reason, 'malformed_health_response')
})

test('restart policy uses bounded exponential delay and exhausts deterministically', () => {
  assert.deepEqual(restartDecision(0, 3), { restart: true, state: 'starting', delayMs: 500 })
  assert.deepEqual(restartDecision(1, 3), { restart: true, state: 'starting', delayMs: 1000 })
  assert.deepEqual(restartDecision(3, 3), { restart: false, state: 'restart-exhausted' })
  assert.deepEqual(restartDecision(-1, 3), { restart: false, state: 'restart-exhausted' })
})

test('Windows process-tree cleanup uses taskkill with descendant cleanup', () => {
  const calls = []
  const result = terminateProcessTree({ pid: 4242, platform: 'win32', spawnSync: (...args) => { calls.push(args); return { status: 0 } } })
  assert.equal(result.terminated, true)
  assert.deepEqual(calls[0][0], 'taskkill')
  assert.deepEqual(calls[0][1], ['/pid', '4242', '/t', '/f'])
  assert.equal(terminateProcessTree({ pid: 0, platform: 'win32' }).reason, 'invalid_pid')
})

test('bounded JSON parsing rejects oversized and malformed sidecar responses', () => {
  assert.deepEqual(parseBoundedJson('{"status":"ok"}'), { ok: true, payload: { status: 'ok' } })
  assert.deepEqual(parseBoundedJson('{not-json'), { ok: false, reason: 'malformed_json' })
  assert.deepEqual(parseBoundedJson('x'.repeat(8 * 1024 * 1024 + 1)), { ok: false, reason: 'response_too_large' })
})
