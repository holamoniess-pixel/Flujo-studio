import type { Attachment, BackendHealth, BackendJob, Conversation, ApiError, JsonValue, PublishAdapterStatus, PublishPrepRun } from './domain'
import type { DesktopBackendMethod } from '../electron/ipc-contract'

export type { BackendHealth, BackendJob }

type BridgeResponse = { ok: boolean; status: number; body: unknown }
type ErrorBody = { error?: Partial<ApiError>; detail?: Partial<ApiError> }

const fallbackBase = 'http://127.0.0.1:47821'
export const backendBase = () => window.flujoDesktop?.backendUrl || fallbackBase

export class BackendRequestError extends Error {
  readonly status: number
  readonly code: string
  readonly retryable: boolean
  readonly traceId?: string

  constructor(status: number, error: ApiError) {
    super(error.message)
    this.name = 'BackendRequestError'
    this.status = status
    this.code = error.code
    this.retryable = Boolean(error.retryable)
    this.traceId = error.trace_id
  }
}

function normalizeHeaders(input: HeadersInit | undefined): Record<string, string> {
  const result: Record<string, string> = { 'content-type': 'application/json' }
  if (!input) return result
  if (input instanceof Headers) {
    input.forEach((value, key) => { result[key] = value })
  } else if (Array.isArray(input)) {
    input.forEach(([key, value]) => { result[key] = value })
  } else {
    Object.entries(input).forEach(([key, value]) => { if (typeof value === 'string') result[key] = value })
  }
  return result
}

const allowedMethods: DesktopBackendMethod[] = ['GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'OPTIONS']
const MAX_FRONTEND_REQUEST_BODY_BYTES = 1_048_576

function normalizeMethod(method: string | undefined): DesktopBackendMethod {
  if (!method) return 'GET'
  const candidate = method.toUpperCase()
  if (allowedMethods.includes(candidate as DesktopBackendMethod)) return candidate as DesktopBackendMethod
  throw new BackendRequestError(400, { code: 'INVALID_FRONTEND_METHOD', message: 'The frontend request method is not allowed.', retryable: false })
}

function validatePath(path: string): void {
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\') || path.split('/').includes('..')) {
    throw new BackendRequestError(400, { code: 'INVALID_FRONTEND_PATH', message: 'The frontend request path is not allowed.', retryable: false })
  }
  const base = new URL(backendBase())
  const resolved = new URL(path, base)
  if (resolved.origin !== base.origin) {
    throw new BackendRequestError(400, { code: 'INVALID_FRONTEND_PATH', message: 'The frontend request path is not allowed.', retryable: false })
  }
}

function validateBody(body: BodyInit | null | undefined): void {
  if (typeof body !== 'string') return
  const bytes = new TextEncoder().encode(body).byteLength
  if (bytes > MAX_FRONTEND_REQUEST_BODY_BYTES) {
    throw new BackendRequestError(413, { code: 'FRONTEND_REQUEST_TOO_LARGE', message: 'The frontend request body is too large.', retryable: false })
  }
}

function errorFromBody(body: unknown, status: number): ApiError {
  const candidate = (body && typeof body === 'object' ? body : {}) as ErrorBody
  const error = candidate.error || candidate.detail || {}
  return {
    code: typeof error.code === 'string' ? error.code : status === 401 ? 'UNAUTHENTICATED_LOCAL_REQUEST' : status === 503 ? 'LOCAL_SESSION_TOKEN_UNCONFIGURED' : 'UNKNOWN',
    message: typeof error.message === 'string' ? error.message : `Backend request failed with status ${status}.`,
    retryable: typeof error.retryable === 'boolean' ? error.retryable : status >= 500,
    trace_id: typeof error.trace_id === 'string' ? error.trace_id : undefined,
    error_id: typeof error.error_id === 'string' ? error.error_id : undefined,
  }
}

export async function backendFetch<T>(path: string, init: RequestInit = {}): Promise<T> {
  validatePath(path)
  validateBody(init.body)
  const headers = normalizeHeaders(init.headers)
  const method = normalizeMethod(init.method)
  let result: BridgeResponse
  try {
    if (window.flujoDesktop?.requestBackend) {
      result = await window.flujoDesktop.requestBackend({
        path,
        method,
        headers,
        body: typeof init.body === 'string' ? init.body : undefined,
      })
    } else {
      const response = await fetch(`${backendBase()}${path}`, { ...init, headers })
      result = { ok: response.ok, status: response.status, body: await response.json().catch(() => ({})) }
    }
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : 'The local backend is offline.'
    throw new BackendRequestError(0, { code: 'OFFLINE', message, retryable: true })
  }
  if (!result.ok) throw new BackendRequestError(result.status, errorFromBody(result.body, result.status))
  return result.body as T
}

export const backendHealth = () => backendFetch<BackendHealth>('/health')
export const queueCreativeBrief = (content: string, attachments: Attachment[] = [], idempotencyKey = `chat-${Date.now()}`) => backendFetch<BackendJob>('/api/conversations/conversation_citrus/messages', { method: 'POST', body: JSON.stringify({ content, attachments, idempotency_key: idempotencyKey, project_id: 'project_northstar', campaign_id: 'campaign_citrus' }) })
export const fetchConversation = () => backendFetch<Conversation>('/api/conversations/conversation_citrus')
export const reportFrontendError = (payload: Record<string, JsonValue>) => backendFetch<{ accepted: boolean; event_id: string }>('/api/diagnostics/events', { method: 'POST', body: JSON.stringify(payload) }).catch(() => undefined)
export const fetchPublishAdapters = () => backendFetch<PublishAdapterStatus[]>('/api/publish/adapters')
export const listPublishPrepRuns = (projectId = 'project_northstar', limit = 10) => backendFetch<PublishPrepRun[]>(`/api/projects/${projectId}/publish/prep?limit=${limit}`)
export const startPublishPrep = (projectId: string, artifactId: string, platform = 'youtube') => backendFetch<PublishPrepRun>(`/api/projects/${projectId}/publish/prep`, { method: 'POST', body: JSON.stringify({ artifact_id: artifactId, platform }) })
export const uploadPublishPrepRun = (runId: string) => backendFetch<{ run_id: string; status: string; platform: string; receipt: Record<string, JsonValue> }>(`/api/publish/prep/${runId}/upload`, { method: 'POST' })
