const { MAX_BACKEND_RESPONSE_BYTES } = require('./shellContracts.cjs')

const SIDECAR_STATES = Object.freeze(['starting', 'ready', 'timeout', 'crashed', 'port-conflict', 'stopped', 'restart-exhausted'])

function isHealthyPayload(payload) {
  return payload !== null && typeof payload === 'object' && payload.status === 'ok' && payload.service === 'flujo-local-engine-room'
}

async function waitForHealthy(probe, { timeoutMs = 30000, intervalMs = 250, sleep = delay, now = Date.now } = {}) {
  const deadline = now() + timeoutMs
  let lastReason = 'not_reachable'
  while (now() < deadline) {
    try {
      const result = await probe()
      if (result && result.ok && isHealthyPayload(result.payload)) return { ready: true, reason: 'healthy' }
      lastReason = result?.reason || 'malformed_health_response'
    } catch (error) {
      lastReason = error instanceof Error ? error.message : 'probe_failed'
    }
    const remaining = deadline - now()
    if (remaining <= 0) break
    await sleep(Math.min(intervalMs, remaining))
  }
  return { ready: false, reason: lastReason }
}

function restartDecision(attempt, maxAttempts = 3) {
  if (!Number.isInteger(attempt) || attempt < 0) return { restart: false, state: 'restart-exhausted' }
  if (attempt >= maxAttempts) return { restart: false, state: 'restart-exhausted' }
  return { restart: true, state: 'starting', delayMs: Math.min(5000, 500 * 2 ** attempt) }
}

function terminateProcessTree({ pid, platform = process.platform, spawnSync = require('node:child_process').spawnSync, kill = process.kill }) {
  if (!Number.isInteger(pid) || pid <= 0) return { terminated: false, reason: 'invalid_pid' }
  try {
    if (platform === 'win32') {
      const result = spawnSync('taskkill', ['/pid', String(pid), '/t', '/f'], { windowsHide: true, stdio: 'ignore' })
      return { terminated: result?.status === 0 || result?.status === 128, reason: result?.status === 128 ? 'already_stopped' : 'taskkill' }
    }
    kill(pid, 'SIGTERM')
    return { terminated: true, reason: 'sigterm' }
  } catch (error) {
    return { terminated: false, reason: error instanceof Error ? error.message : 'termination_failed' }
  }
}

function parseBoundedJson(text) {
  if (typeof text !== 'string' || Buffer.byteLength(text, 'utf8') > MAX_BACKEND_RESPONSE_BYTES) return { ok: false, reason: 'response_too_large' }
  try {
    return { ok: true, payload: JSON.parse(text) }
  } catch {
    return { ok: false, reason: 'malformed_json' }
  }
}

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms))
}

module.exports = { SIDECAR_STATES, isHealthyPayload, waitForHealthy, restartDecision, terminateProcessTree, parseBoundedJson }
