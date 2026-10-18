import { BackendRequestError } from './backendClient'
import type { RequestPhase } from './domain'

export type UiFailure = {
  phase: Extract<RequestPhase, 'error' | 'unauthorized' | 'offline' | 'blocked'>
  code: string
  message: string
  retryable: boolean
}

const blockedCodes = new Set([
  'LOCAL_SESSION_TOKEN_UNCONFIGURED',
  'MEDIA_DEPENDENCY_MISSING',
  'MEDIA_DEPENDENCY_CORRUPT',
  'MEDIA_PATH_FORBIDDEN',
  'MEDIA_OPERATION_FORBIDDEN',
  'MEDIA_OUTPUT_INVALID',
  'MONITOR_HALTED',
  'WORKFLOW_RECOVERY_REQUIRED',
  'PROVIDER_DISABLED',
  'PROVIDER_CREDENTIAL_UNAVAILABLE',
])

export function toUiFailure(error: unknown, fallback: string): UiFailure {
  if (error instanceof BackendRequestError) {
    const phase = error.status === 0 || error.code === 'OFFLINE'
      ? 'offline'
      : error.status === 401 || error.code.includes('UNAUTHENTICATED')
        ? 'unauthorized'
        : blockedCodes.has(error.code) || error.status === 403 || error.status === 423 || error.status === 503
          ? 'blocked'
          : 'error'
    return { phase, code: error.code, message: error.message || fallback, retryable: error.retryable }
  }
  if (error instanceof Error) return { phase: 'error', code: 'UNKNOWN', message: error.message || fallback, retryable: true }
  return { phase: 'error', code: 'UNKNOWN', message: fallback, retryable: true }
}

export function requestPhaseLabel(phase: UiFailure['phase']): string {
  return phase === 'offline' ? 'Offline' : phase === 'unauthorized' ? 'Unauthorized' : phase === 'blocked' ? 'Blocked' : 'Request failed'
}
