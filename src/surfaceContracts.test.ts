import { describe, expect, it } from 'vitest'
import { allFrontendRoutes, frontendSurfaceContracts, surfaceContract } from './surfaceContracts'

describe('frontend surface contracts', () => {
  it('covers every declared route exactly once', () => {
    expect(new Set(allFrontendRoutes).size).toBe(allFrontendRoutes.length)
    expect(frontendSurfaceContracts.map(contract => contract.route).sort()).toEqual([...allFrontendRoutes].sort())
  })

  it('treats the Phase 2B de-simulated surfaces as REAL backend surfaces', () => {
    for (const route of ['automation', 'calendar', 'sourceMonitor', 'socialAccounts', 'command', 'intake', 'governor', 'council', 'terminal', 'credentials', 'policies', 'permissions', 'copilot'] as const) {
      const contract = surfaceContract(route)
      expect(contract.data_mode).toBe('REAL')
      expect(contract.backend_path).toBeTruthy()
      expect(contract.states.success).toBe(true)
      expect(contract.states.empty).toBe(true)
      expect(contract.states.error).toBe(true)
    }
  })

  it('keeps the intentional simulator and local-only surfaces out of REAL', () => {
    expect(surfaceContract('sandbox').data_mode).toBe('DEMO_ONLY')
    expect(surfaceContract('sandbox').notes).toMatch(/intentional simulator/i)
    expect(surfaceContract('creativeMode').data_mode).toBe('DEMO_ONLY')
    expect(surfaceContract('settings').data_mode).toBe('CONTRACT_STUB')
  })

  it('marks the terminal as a read-only log viewer with no shell', () => {
    const contract = surfaceContract('terminal')
    expect(contract.notes).toMatch(/read-only/i)
    expect(contract.backend_path).toMatch(/terminal\/log/)
  })

  it('requires live surfaces to expose loading, empty, failure, retry, auth, and offline phases', () => {
    for (const route of ['production', 'modelRegistry', 'monitor', 'doctor', 'replay', 'lineage', 'workflow', 'guard', 'observability', 'chat', 'setup', 'publishing'] as const) {
      const states = surfaceContract(route).states
      expect(states.loading).toBe(true)
      expect(states.empty).toBe(true)
      expect(states.error).toBe(true)
      expect(states.retry).toBe(true)
      expect(states.unauthorized).toBe(true)
      expect(states.offline).toBe(true)
    }
  })

  it('labels the remaining demo surfaces honestly', () => {
    expect(surfaceContract('sandbox').notes).toMatch(/intentional simulator/i)
    expect(surfaceContract('creativeMode').data_mode).toBe('DEMO_ONLY')
  })

  it('treats chat and setup as REAL backend surfaces', () => {
    for (const route of ['chat', 'setup'] as const) {
      expect(surfaceContract(route).data_mode).toBe('REAL')
    }
    expect(surfaceContract('chat').notes).toMatch(/idempotent jobs/i)
    expect(surfaceContract('setup').notes).toMatch(/\/api\/installation\/onboarding/)
  })

  it('treats publishing as a REAL backend surface with a credential-gated upload', () => {
    const contract = surfaceContract('publishing')
    expect(contract.data_mode).toBe('REAL')
    expect(contract.notes).toMatch(/credential-gated/i)
    expect(contract.states.success).toBe(true)
    expect(contract.states.blocked).toBe(true)
  })

  it('keeps settings as a local-only contract stub', () => {
    const contract = surfaceContract('settings')
    expect(contract.data_mode).toBe('CONTRACT_STUB')
    expect(contract.notes).toMatch(/no backend contract/i)
  })
  it('fails closed for an unknown route contract', () => {
    const contract = surfaceContract('unknown-route' as never)
    expect(contract.data_mode).toBe('MISSING_BACKEND')
    expect(contract.states.blocked).toBe(true)
    expect(contract.states.success).not.toBe(true)
  })
})
