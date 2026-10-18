import type { FrontendRoute, FrontendSurfaceContract, RequestPhase } from './domain'

export const allFrontendRoutes: FrontendRoute[] = ['command', 'chat', 'projects', 'campaigns', 'library', 'intake', 'analysis', 'evidence', 'entities', 'genome', 'genomeCompare', 'blueprint', 'compiler', 'production', 'qa', 'reviewQueue', 'governor', 'council', 'modelRegistry', 'toolLab', 'mediaEngine', 'mediaTools', 'composition', 'terminal', 'replay', 'creativeMode', 'experiments', 'performance', 'analytics', 'drift', 'adaptation', 'adaptationCenter', 'skills', 'recipes', 'memory', 'evolution', 'automation', 'calendar', 'sourceMonitor', 'socialAccounts', 'publishing', 'workflow', 'doctor', 'incidents', 'sandbox', 'resources', 'observability', 'credentials', 'security', 'policies', 'controlPlane', 'memorySources', 'permissions', 'setup', 'copilot', 'skillDiff', 'lineage', 'guard', 'monitor', 'settings']

const liveRoutes: FrontendRoute[] = ['production', 'modelRegistry', 'monitor', 'doctor', 'replay', 'lineage', 'workflow', 'guard', 'observability', 'chat', 'setup', 'publishing',
  // Phase 2B: de-simulated fixture surfaces, now backed by local backend endpoints.
  'command', 'projects', 'campaigns', 'library', 'intake', 'analysis', 'evidence', 'entities', 'genome', 'genomeCompare', 'blueprint', 'compiler',
  'qa', 'reviewQueue', 'governor', 'council', 'toolLab', 'mediaEngine', 'mediaTools', 'composition', 'experiments', 'performance', 'analytics',
  'drift', 'adaptation', 'adaptationCenter', 'skills', 'recipes', 'memory', 'evolution', 'automation', 'calendar', 'sourceMonitor', 'socialAccounts',
  'controlPlane', 'memorySources', 'permissions', 'credentials', 'security', 'policies', 'copilot', 'skillDiff', 'terminal', 'incidents', 'resources']
const contractStubRoutes: FrontendRoute[] = ['settings']
// sandbox stays DEMO_ONLY by design: it is an intentional, labeled simulator.
// creativeMode stays DEMO_ONLY: it is a local mode selector with no backend contract.
const missingBackendRoutes: FrontendRoute[] = []
const phases: RequestPhase[] = ['loading', 'empty', 'success', 'blocked', 'error', 'unauthorized', 'offline', 'retry', 'running', 'cancelling', 'cancelled']

function statesFor(mode: FrontendSurfaceContract['data_mode'], route: FrontendRoute): Partial<Record<RequestPhase, boolean>> {
  if (mode === 'REAL') return { loading: true, empty: true, success: true, blocked: true, error: true, unauthorized: true, offline: true, retry: true, running: route === 'production', cancelling: route === 'production' }
  if (mode === 'CONTRACT_STUB') return { empty: true, success: true, blocked: true, error: true, offline: true, retry: true }
  if (mode === 'MISSING_BACKEND') return { empty: true, blocked: true, error: true, offline: true }
  return { empty: true, success: true, blocked: true }
}

function modeFor(route: FrontendRoute): FrontendSurfaceContract['data_mode'] {
  if (liveRoutes.includes(route)) return 'REAL'
  if (contractStubRoutes.includes(route)) return 'CONTRACT_STUB'
  if (missingBackendRoutes.includes(route)) return 'MISSING_BACKEND'
  return 'DEMO_ONLY'
}

const routeBackendPath: Partial<Record<FrontendRoute, string>> = {
  command: '/api/jobs, /api/diagnostics/events, /api/gateway/health',
  projects: '/api/projects',
  campaigns: '/api/projects/{project_id}/campaigns',
  library: '/api/artifacts',
  intake: '/api/references',
  analysis: '/api/evidence',
  evidence: '/api/evidence',
  entities: '/api/entities',
  genome: '/api/genomes',
  genomeCompare: '/api/genomes',
  blueprint: '/api/blueprints',
  compiler: '/api/blueprints/{blueprint_id}/compile, /api/compile-runs',
  qa: '/api/qa, /api/qa/run',
  reviewQueue: '/api/projects/{project_id}/guard/reviews',
  governor: '/api/governor/advisors, /api/gateway/health',
  council: '/api/governor/decide, /api/governor/advisors',
  toolLab: '/api/tools',
  mediaEngine: '/api/tool-runs, /api/media/health',
  mediaTools: '/api/media/health',
  composition: '/api/compositions',
  experiments: '/api/adaptation/candidates',
  performance: '/api/usage, /api/adaptation/model-affinity',
  analytics: '/api/usage',
  drift: '/api/adaptation/drift',
  adaptation: '/api/adaptation/candidates',
  adaptationCenter: '/api/adaptation/candidates, /api/promotions',
  skills: '/api/skills',
  recipes: '/api/recipes',
  memory: '/api/memory/records',
  evolution: '/api/adaptation/experiences',
  automation: '/api/jobs',
  calendar: '/api/calendar',
  sourceMonitor: '/api/sources',
  socialAccounts: '/api/social-accounts',
  controlPlane: '/api/control-plane',
  memorySources: '/api/memory/records',
  permissions: '/api/permissions',
  credentials: '/api/credentials, /api/providers',
  security: '/api/credentials, /api/guard/policies, /api/diagnostics/errors',
  policies: '/api/constitution, /api/guard/policies',
  copilot: '/api/copilot/explain',
  skillDiff: '/api/skills',
  terminal: '/api/terminal/log (read-only audit viewer; no shell)',
  incidents: '/api/doctor/incidents, /api/diagnostics/errors',
  resources: '/api/monitor/spend',
  modelRegistry: '/api/models, /api/gateway/health',
  sandbox: 'intentional local simulator; no backend contract',
  creativeMode: 'local-only mode selector; no backend contract',
}

const routeNotes: Partial<Record<FrontendRoute, string>> = {
  chat: 'Conversation list and messages load from the local backend; briefs are queued as idempotent jobs. Failures remain explicit.',
  setup: 'Onboarding persists to /api/installation/onboarding; localStorage is fallback only when the backend is unreachable.',
  publishing: 'Prep checklist state and platform adapter status load from the local backend; platform upload stays credential-gated and fails closed without a stored credential.',
  settings: 'Local-only settings surface (theme, density, motion); no backend contract. Contract surface for installation-scoped preferences only.',
  sandbox: 'Intentional simulator surface: estimates are deterministic and labeled; no provider calls or publishing are possible.',
  terminal: 'Read-only backend log and event viewer. No command shell is shipped; this surface only reads persisted audit events.',
  socialAccounts: 'Account identities are recorded locally; platform OAuth is not implemented, so records never claim a live connection.',
  calendar: 'Schedule items persist in the local backend; nothing is published automatically.',
  sourceMonitor: 'Monitored sources persist locally on a local schedule; no external fetching is performed.',
}

export const frontendSurfaceContracts: FrontendSurfaceContract[] = allFrontendRoutes.map(route => {
  const data_mode = modeFor(route)
  const override = routeNotes[route]
  const backend_path = routeBackendPath[route]
  if (override) {
    return {
      route,
      data_mode,
      backend_path,
      states: statesFor(data_mode, route),
      notes: override,
    }
  }
  return {
    route,
    data_mode,
    backend_path,
    states: statesFor(data_mode, route),
    notes: data_mode === 'REAL'
      ? 'Uses persisted local backend state when connected; failures remain explicit.'
      : data_mode === 'CONTRACT_STUB'
        ? 'Uses a local contract surface or mixed backend-ready behavior; no external provider success is implied.'
        : data_mode === 'MISSING_BACKEND'
          ? 'The UI surface exists, but the corresponding backend integration is not connected.'
          : 'Local demo/fixture surface; must remain visibly labeled as non-production data.',
  }
})

export function surfaceContract(route: FrontendRoute): FrontendSurfaceContract {
  return frontendSurfaceContracts.find(contract => contract.route === route) || {
    route,
    data_mode: 'MISSING_BACKEND',
    states: { blocked: true, error: true, empty: true },
    notes: 'Unknown route contract; fail closed.',
  }
}

export const requestStatePhases = phases
