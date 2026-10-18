import { describe, expect, it } from 'vitest'
import { BackendRequestError } from './backendClient'
import { requestPhaseLabel, toUiFailure } from './frontendState'

describe('frontend request-state classification', () => {
  it('classifies offline failures as retryable offline state', () => {
    const failure = toUiFailure(new BackendRequestError(0, { code: 'OFFLINE', message: 'Local engine is unreachable.', retryable: true }), 'Fallback')
    expect(failure.phase).toBe('offline')
    expect(failure.retryable).toBe(true)
    expect(requestPhaseLabel(failure.phase)).toBe('Offline')
  })

  it('classifies authentication and policy failures explicitly', () => {
    const unauthorized = toUiFailure(new BackendRequestError(401, { code: 'UNAUTHENTICATED_LOCAL_REQUEST', message: 'Session required.' }), 'Fallback')
    const blocked = toUiFailure(new BackendRequestError(503, { code: 'PROVIDER_DISABLED', message: 'Provider is disabled.' }), 'Fallback')
    expect(unauthorized.phase).toBe('unauthorized')
    expect(blocked.phase).toBe('blocked')
    expect(requestPhaseLabel(blocked.phase)).toBe('Blocked')
  })
})
