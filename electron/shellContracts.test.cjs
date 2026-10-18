const test = require('node:test')
const assert = require('node:assert/strict')
const {
  IPC_CHANNELS,
  MAX_IPC_BODY_BYTES,
  validateIpcChannel,
  validateBackendRequest,
  buildForwardHeaders,
  isApprovedExternalUrl,
} = require('./shellContracts.cjs')

test('unknown IPC channels fail closed', () => {
  assert.equal(validateIpcChannel('unknown-channel').ok, false)
  assert.equal(validateIpcChannel(IPC_CHANNELS.BACKEND_REQUEST).ok, true)
})

test('malformed IPC bodies fail closed', () => {
  for (const input of [null, [], 'request', 42]) {
    const result = validateBackendRequest(input)
    assert.equal(result.ok, false)
    assert.equal(result.result.status, 400)
    assert.equal(result.result.body.error.code, 'MALFORMED_IPC_BODY')
  }
})

test('paths, methods, and bodies are bounded and allowlisted', () => {
  assert.equal(validateBackendRequest({ path: '/health', method: 'GET' }).ok, true)
  assert.equal(validateBackendRequest({ path: '/api/jobs', method: 'POST', body: '{}' }).ok, true)
  assert.equal(validateBackendRequest({ path: '/private/file', method: 'GET' }).result.body.error.code, 'INVALID_LOCAL_BACKEND_PATH')
  assert.equal(validateBackendRequest({ path: '/api/jobs', method: 'TRACE' }).result.body.error.code, 'INVALID_LOCAL_BACKEND_METHOD')
  assert.equal(validateBackendRequest({ path: '/api/jobs', method: 'POST', body: 'x'.repeat(MAX_IPC_BODY_BYTES + 1) }).result.body.error.code, 'IPC_BODY_TOO_LARGE')
  assert.equal(validateBackendRequest({ path: '/api/jobs', method: 'GET', body: '{}' }).result.body.error.code, 'BODY_NOT_ALLOWED_FOR_METHOD')
  assert.equal(validateBackendRequest({ path: '//127.0.0.1/private', method: 'GET' }).ok, false)
  assert.equal(validateBackendRequest({ path: '/api\\private', method: 'GET' }).ok, false)
})

test('forged or malformed headers are rejected and the app token cannot be supplied by the renderer', () => {
  const forged = validateBackendRequest({ path: '/api/jobs', headers: { 'x-flujo-app-token': 'forged' } })
  assert.equal(forged.ok, false)
  assert.equal(forged.result.body.error.code, 'FORBIDDEN_IPC_HEADER')
  assert.equal(validateBackendRequest({ path: '/api/jobs', headers: { 'x-flujo-trace-id': 'trace-1' } }).ok, true)
  assert.equal(validateBackendRequest({ path: '/api/jobs', headers: [] }).ok, false)
  const forwarded = buildForwardHeaders({ 'x-flujo-app-token': 'forged', 'x-flujo-trace-id': 'trace-1' }, 'real-token')
  assert.equal(forwarded['x-flujo-app-token'], 'real-token')
  assert.equal(forwarded['x-flujo-trace-id'], 'trace-1')
})

test('external navigation allows only approved HTTPS origins without credentials or ports', () => {
  assert.equal(isApprovedExternalUrl('https://help.manus.im/support'), true)
  assert.equal(isApprovedExternalUrl('https://docs.flujo.studio/guide'), true)
  assert.equal(isApprovedExternalUrl('http://help.manus.im/support'), false)
  assert.equal(isApprovedExternalUrl('https://evil.example/'), false)
  assert.equal(isApprovedExternalUrl('javascript:alert(1)'), false)
  assert.equal(isApprovedExternalUrl('https://help.manus.im:8443/'), false)
  assert.equal(isApprovedExternalUrl('https://user:pass@help.manus.im/'), false)
})
