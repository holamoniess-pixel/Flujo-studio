const path = require('node:path')

const IPC_CHANNELS = Object.freeze({ BACKEND_REQUEST: 'flujo-backend-request', SIDECAR_STATUS: 'flujo-sidecar-status' })
const ALLOWED_METHODS = Object.freeze(['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS'])
const FORWARDED_HEADERS = Object.freeze(['content-type', 'x-flujo-trace-id'])
const MAX_IPC_PATH_LENGTH = 2048
const MAX_IPC_BODY_BYTES = 4 * 1024 * 1024
const MAX_IPC_HEADER_LENGTH = 200
const MAX_BACKEND_RESPONSE_BYTES = 8 * 1024 * 1024
const APPROVED_EXTERNAL_ORIGINS = Object.freeze([
  'https://help.manus.im',
  'https://docs.flujo.studio',
])

function errorResult(status, code, message, retryable = false) {
  return { ok: false, status, body: { error: { code, message, retryable } } }
}

function isPlainObject(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

function validateIpcChannel(channel) {
  return channel === IPC_CHANNELS.BACKEND_REQUEST
    ? { ok: true, channel }
    : { ok: false, result: errorResult(404, 'UNKNOWN_IPC_CHANNEL', 'The requested desktop bridge channel is not available.') }
}

function validateBackendPath(value) {
  if (typeof value !== 'string' || value.length === 0 || value.length > MAX_IPC_PATH_LENGTH) {
    return false
  }
  if (value.includes('\u0000') || value.includes('\\') || value.startsWith('//')) return false
  if (value !== '/health' && !value.startsWith('/api/')) return false
  try {
    const parsed = new URL(value, 'http://127.0.0.1')
    return parsed.origin === 'http://127.0.0.1' && parsed.pathname === parsed.pathname.normalize('NFC')
  } catch {
    return false
  }
}

function validateBackendRequest(input) {
  if (!isPlainObject(input)) return { ok: false, result: errorResult(400, 'MALFORMED_IPC_BODY', 'The desktop bridge request must be a JSON object.') }
  if (!validateBackendPath(input.path)) return { ok: false, result: errorResult(400, 'INVALID_LOCAL_BACKEND_PATH', 'Only bounded /health and /api/ paths are accepted by the desktop bridge.') }

  const method = input.method === undefined ? 'GET' : typeof input.method === 'string' ? input.method.toUpperCase() : ''
  if (!ALLOWED_METHODS.includes(method)) return { ok: false, result: errorResult(405, 'INVALID_LOCAL_BACKEND_METHOD', 'The requested HTTP method is not permitted by the desktop bridge.') }

  if (input.body !== undefined && input.body !== null && (typeof input.body !== 'string' || Buffer.byteLength(input.body, 'utf8') > MAX_IPC_BODY_BYTES)) {
    return { ok: false, result: errorResult(413, 'IPC_BODY_TOO_LARGE', 'The desktop bridge request body exceeds the configured limit.') }
  }
  if (['GET', 'OPTIONS'].includes(method) && input.body !== undefined && input.body !== null && input.body !== '') {
    return { ok: false, result: errorResult(400, 'BODY_NOT_ALLOWED_FOR_METHOD', 'This desktop bridge method does not accept a request body.') }
  }

  const headers = input.headers === undefined ? {} : input.headers
  if (!isPlainObject(headers)) return { ok: false, result: errorResult(400, 'MALFORMED_IPC_HEADERS', 'Desktop bridge headers must be a simple object.') }
  const forwarded = {}
  for (const [rawKey, value] of Object.entries(headers)) {
    const key = rawKey.toLowerCase()
    if (!FORWARDED_HEADERS.includes(key)) return { ok: false, result: errorResult(400, 'FORBIDDEN_IPC_HEADER', `The desktop bridge will not forward header ${rawKey}.`) }
    if (typeof value !== 'string' || value.length > MAX_IPC_HEADER_LENGTH) return { ok: false, result: errorResult(400, 'INVALID_IPC_HEADER', `The desktop bridge header ${rawKey} is invalid.`) }
    forwarded[key] = value
  }
  return { ok: true, request: { path: input.path, method, body: input.body, headers: forwarded } }
}

function buildForwardHeaders(inputHeaders, appToken) {
  const headers = { 'content-type': 'application/json' }
  for (const key of FORWARDED_HEADERS) {
    if (inputHeaders && typeof inputHeaders[key] === 'string') headers[key] = inputHeaders[key]
  }
  headers['x-flujo-app-token'] = appToken
  return headers
}

function isApprovedExternalUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl)
    if (!['http:', 'https:'].includes(parsed.protocol)) return false
    if (parsed.username || parsed.password || parsed.port) return false
    return APPROVED_EXTERNAL_ORIGINS.includes(`${parsed.protocol}//${parsed.hostname}`)
  } catch {
    return false
  }
}

function resolveUserDataPolicy({ defaultPath, customPath, portableDir }) {
  const candidate = typeof customPath === 'string' && customPath.trim() ? customPath.trim() : typeof portableDir === 'string' && portableDir.trim() ? path.join(portableDir.trim(), 'user-data') : defaultPath
  if (typeof candidate !== 'string' || !path.isAbsolute(candidate)) throw new Error('Electron user-data path must be absolute.')
  return {
    path: path.normalize(candidate),
    mode: customPath ? 'custom' : portableDir ? 'portable' : 'default',
  }
}

function isBoundedResponseText(text) {
  return typeof text === 'string' && Buffer.byteLength(text, 'utf8') <= MAX_BACKEND_RESPONSE_BYTES
}

module.exports = {
  IPC_CHANNELS,
  ALLOWED_METHODS,
  FORWARDED_HEADERS,
  MAX_IPC_PATH_LENGTH,
  MAX_IPC_BODY_BYTES,
  MAX_IPC_HEADER_LENGTH,
  MAX_BACKEND_RESPONSE_BYTES,
  APPROVED_EXTERNAL_ORIGINS,
  errorResult,
  validateIpcChannel,
  validateBackendPath,
  validateBackendRequest,
  buildForwardHeaders,
  isApprovedExternalUrl,
  resolveUserDataPolicy,
  isBoundedResponseText,
}
