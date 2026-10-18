import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { Calendar, Copilot, Council, Credentials, Governor, Library, QA, ReviewQueue, TerminalSurface } from './RealSurfaces'
import { SettingsHub } from './RefinedSurfaces'

const fetchMock = vi.hoisted(() => vi.fn())

vi.mock('./backendClient', () => ({
  backendFetch: fetchMock,
  backendHealth: vi.fn().mockResolvedValue({ status: 'ok' }),
  reportFrontendError: vi.fn().mockResolvedValue(undefined),
}))

type RouteHandler = (path: string, init?: RequestInit) => unknown

function useRoutes(handlers: Record<string, RouteHandler | unknown>) {
  fetchMock.mockImplementation((path: string, init?: RequestInit) => {
    const method = (init?.method || 'GET').toUpperCase()
    for (const [key, handler] of Object.entries(handlers)) {
      const space = key.indexOf(' ')
      const keyMethod = space === -1 ? 'GET' : key.slice(0, space)
      const keyPath = space === -1 ? key : key.slice(space + 1)
      if (keyMethod !== method) continue
      if (path === keyPath || path.startsWith(`${keyPath}?`) || path.startsWith(keyPath)) {
        const value = typeof handler === 'function' ? (handler as RouteHandler)(path, init) : handler
        return Promise.resolve(value)
      }
    }
    return Promise.resolve([])
  })
}

const props = { go: vi.fn(), announce: vi.fn(), inspect: vi.fn() }

beforeEach(() => {
  vi.clearAllMocks()
})

describe('de-simulated surfaces read the local backend', () => {
  it('Library renders persisted artifacts instead of fixture KPIs', async () => {
    useRoutes({
      '/api/artifacts': [{ id: 'a1', project_id: 'p', kind: 'generated', name: 'Citrus Cut 01', status: 'created', created_at: '2026-10-12T10:00:00' }],
    })
    render(<Library {...props} />)
    await waitFor(() => expect(screen.getByText('Citrus Cut 01')).toBeInTheDocument())
    expect(screen.queryByText('42')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/artifacts?project_id=project_northstar&limit=200')
  })

  it('Library shows an explicit empty state when the backend has no artifacts', async () => {
    useRoutes({ '/api/artifacts': [] })
    render(<Library {...props} />)
    await waitFor(() => expect(screen.getByText('No artifacts recorded yet.')).toBeInTheDocument())
  })

  it('Credentials renders masked credentials from the secure store', async () => {
    useRoutes({
      '/api/credentials': [{ credential_id: 'c1', provider: 'NVIDIA', display_name: 'Primary', masked_value: 'nv_•••82F', scope: 'models', status: 'active', updated_at: '2026-10-12T10:00:00' }],
      '/api/providers': [],
    })
    render(<Credentials {...props} />)
    await waitFor(() => expect(screen.getByText('nv_•••82F')).toBeInTheDocument())
    expect(screen.queryByText(/5 keys configured/)).not.toBeInTheDocument()
  })

  it('TerminalSurface is a read-only audit viewer with no shell', async () => {
    useRoutes({
      '/api/terminal/log': [{ id: 'e1', timestamp: '2026-10-12T10:00:00', severity: 'INFO', category: 'http', message: 'GET /health', route: '/health', operation: 'GET', status: '200' }],
    })
    render(<TerminalSurface {...props} />)
    await waitFor(() => expect(screen.getByText(/GET \/health/)).toBeInTheDocument())
    expect(screen.getByText('Read-only by design.')).toBeInTheDocument()
    expect(screen.queryByPlaceholderText(/command/i)).not.toBeInTheDocument()
    const calls = fetchMock.mock.calls.map(call => String(call[0]))
    expect(calls.some(call => call.startsWith('/api/terminal/log'))).toBe(true)
    expect(calls.every(call => !/exec|shell|command/i.test(call))).toBe(true)
  })

  it('Council asks the real Governor decide endpoint and renders the fused answer', async () => {
    useRoutes({
      '/api/governor/advisors': [{ id: 'a1', name: 'Creative Advisor', role: 'Narrative reasoning', status: 'active' }],
      '/health': { gateway: { simulator_only: true, model_count: 2 } },
      'POST /api/governor/decide': { id: 'd1', status: 'decided', task: 'Test question', fused_answer: 'Keep the tactile opening.', confidence: 0.9, selected_advisors: [{ id: 'a1' }], created_at: '2026-10-12T10:00:00' },
    })
    render(<Council {...props} />)
    await waitFor(() => expect(screen.getAllByText('Creative Advisor').length).toBeGreaterThan(0))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Test question' } })
    fireEvent.click(screen.getByRole('button', { name: /Ask Council/ }))
    await waitFor(() => expect(screen.getByText('Keep the tactile opening.')).toBeInTheDocument())
    expect(fetchMock).toHaveBeenCalledWith('/api/governor/decide', expect.objectContaining({ method: 'POST' }))
    expect(screen.queryByText(/assembled from local advisor fixtures/)).not.toBeInTheDocument()
  })

  it('ReviewQueue persists approve decisions through the guard review endpoint', async () => {
    useRoutes({
      '/api/projects/project_northstar/guard/reviews': [{ id: 'gr1', project_id: 'p', reason: 'Caption safety check', status: 'PENDING', decision: 'PENDING', created_at: '2026-10-12T10:00:00' }],
      'POST /api/guard/reviews/gr1/decision': { review_id: 'gr1', status: 'APPROVED' },
    })
    render(<ReviewQueue {...props} />)
    await waitFor(() => expect(screen.getByText('Caption safety check')).toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: /Approve/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/guard/reviews/gr1/decision', expect.objectContaining({ method: 'POST' })))
    const body = JSON.parse(String(fetchMock.mock.calls.find(call => String(call[0]).includes('/decision'))?.[1]?.body || '{}'))
    expect(body.decision).toBe('APPROVE')
  })

  it('QA renders persisted findings and runs verification against local state', async () => {
    useRoutes({
      '/api/qa': [{ id: 'qa1', project_id: 'p', status: 'blocked', findings_json: JSON.stringify([{ check: 'Identity consistency', status: 'blocked', detail: 'No identity reference is attached.' }]), created_at: '2026-10-12T10:00:00' }],
      'POST /api/qa/run': { run_id: 'qa2', status: 'review', findings: [] },
    })
    render(<QA {...props} />)
    await waitFor(() => expect(screen.getByText('Identity consistency')).toBeInTheDocument())
    expect(screen.getByText('No identity reference is attached.')).toBeInTheDocument()
    expect(screen.queryByText('82')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Run verification/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/qa/run', expect.objectContaining({ method: 'POST' })))
  })

  it('Copilot explains from persisted evidence and says so explicitly', async () => {
    useRoutes({
      '/api/copilot/explain': { question: 'Why?', answer: 'Based on 1 local record(s): Keep the tactile opening.', evidence: [{ source: 'Governor decision', text: 'Keep the tactile opening.', detail: 'confidence 0.9' }], matched: true },
    })
    render(<Copilot {...props} />)
    fireEvent.click(screen.getByRole('button', { name: /Explain/ }))
    await waitFor(() => expect(screen.getByText(/Based on 1 local record/)).toBeInTheDocument())
    expect(screen.queryByText(/generated from local evidence fixtures/)).not.toBeInTheDocument()
  })

  it('Governor renders registered advisors and the live gateway state', async () => {
    useRoutes({
      '/api/governor/advisors': [{ id: 'a1', name: 'QA Advisor', role: 'Verification policy', status: 'active' }],
      '/health': { gateway: { simulator_only: true, model_count: 2 } },
      '/api/projects/project_northstar/guard/reviews': [],
    })
    render(<Governor {...props} />)
    await waitFor(() => expect(screen.getByText('QA Advisor')).toBeInTheDocument())
    expect(screen.getByText('Simulator: on')).toBeInTheDocument()
    expect(screen.queryByText(/This recommendation is simulated/)).not.toBeInTheDocument()
  })

  it('Calendar renders persisted schedule items', async () => {
    useRoutes({
      '/api/calendar': [{ id: 'cal1', project_id: 'p', title: 'Launch cut', scheduled_at: '2026-10-20 13:00', status: 'Scheduled', route: 'production', created_at: '2026-10-12T10:00:00' }],
    })
    render(<Calendar {...props} />)
    await waitFor(() => expect(screen.getByText('Launch cut')).toBeInTheDocument())
    expect(screen.queryByText('AI Story #41')).not.toBeInTheDocument()
  })
})

describe('SettingsHub Models section reads the live gateway', () => {
  const settingsProps = { go: vi.fn(), announce: vi.fn(), inspect: vi.fn(), theme: 'graphite' as const, setTheme: vi.fn(), compact: false, setCompact: vi.fn() }

  function renderModelsTab() {
    render(<SettingsHub {...settingsProps} />)
    fireEvent.click(screen.getByRole('button', { name: /Models/ }))
  }

  it('loads the real catalog and labels the explicit simulator state from gateway health', async () => {
    useRoutes({
      '/api/models': [{ provider: 'nvidia', model_id: 'live-test-model', version: '1', capabilities: ['text_generation'], modalities: ['text'], fallback_group: 'default', priority: 1, status: 'healthy' }],
      '/api/gateway/health': { simulator_only: true, model_count: 1, status: 'ok' },
      '/api/providers': [],
      '/api/credentials': [],
      '/api/usage': { events: [], totals: { requests: 0, input_tokens: 0, output_tokens: 0, duration_ms: 0, estimated_cost: 0 } },
    })
    renderModelsTab()
    fireEvent.click(screen.getByRole('button', { name: 'Model Inventory' }))
    await waitFor(() => expect(screen.getAllByText('live-test-model').length).toBeGreaterThan(0))
    expect(screen.getAllByText('Simulator only').length).toBeGreaterThan(0)
    expect(screen.queryByText('Nemotron 3 Ultra')).not.toBeInTheDocument()
    expect(screen.queryByText('Kimi K2.6')).not.toBeInTheDocument()
    expect(fetchMock).toHaveBeenCalledWith('/api/models')
    expect(fetchMock).toHaveBeenCalledWith('/api/gateway/health')
  })

  it('saves a provider with a masked key through the providers endpoint', async () => {
    useRoutes({
      '/api/models': [],
      '/api/gateway/health': { simulator_only: true, model_count: 0 },
      '/api/providers': [],
      '/api/credentials': [],
      '/api/usage': { events: [], totals: { requests: 0, input_tokens: 0, output_tokens: 0, duration_ms: 0, estimated_cost: 0 } },
      'POST /api/providers': { id: 'p1', name: 'NVIDIA', status: 'configured' },
    })
    renderModelsTab()
    fireEvent.click(screen.getByRole('button', { name: 'Providers & API Keys' }))
    await waitFor(() => expect(screen.getByPlaceholderText('Paste API key')).toBeInTheDocument())
    fireEvent.change(screen.getByPlaceholderText('Paste API key'), { target: { value: 'nv-secret-key' } })
    fireEvent.click(screen.getByRole('button', { name: /Add provider/ }))
    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/providers', expect.objectContaining({ method: 'POST' })))
    const body = JSON.parse(String(fetchMock.mock.calls.find(call => call[1]?.method === 'POST' && String(call[0]) === '/api/providers')?.[1]?.body || '{}'))
    expect(body.name).toBe('NVIDIA')
    expect(body.api_key).toBe('nv-secret-key')
  })

  it('renders usage from the local ledger instead of fixture numbers', async () => {
    useRoutes({
      '/api/models': [],
      '/api/gateway/health': { simulator_only: true, model_count: 0 },
      '/api/providers': [],
      '/api/credentials': [],
      '/api/usage': { events: [{ id: 'u1', model_id: 'live-test-model', requests: 7, input_tokens: 1000, output_tokens: 500, estimated_cost: 0.42 }], totals: { requests: 7, input_tokens: 1000, output_tokens: 500, duration_ms: 9000, estimated_cost: 0.42 } },
    })
    renderModelsTab()
    fireEvent.click(screen.getByRole('button', { name: 'API Usage' }))
    await waitFor(() => expect(screen.getAllByText('live-test-model').length).toBeGreaterThan(0))
    expect(screen.queryByText('$2.71')).not.toBeInTheDocument()
  })
})
