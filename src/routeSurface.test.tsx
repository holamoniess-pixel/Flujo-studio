import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

const { deferredPost } = vi.hoisted(() => {
  const deferredPost: { resolve: (value: unknown) => void } = { resolve: () => undefined }
  return { deferredPost }
})

vi.mock('./backendClient', () => ({
  backendHealth: vi.fn().mockResolvedValue({
    status: 'ok',
    service: 'flujo-local-engine-room',
    version: 'test',
    database: true,
    ffmpeg: true,
    ffprobe: true,
    worker_capacity: 2,
    trace_id: 'test-trace',
    gateway: { status: 'healthy', model_count: 0, simulator_only: true },
  }),
  backendFetch: vi.fn((path: string, init?: RequestInit) => {
    if (path === '/api/installation') return Promise.resolve({ show_onboarding: false })
    if (init?.method === 'POST' && path.includes('/api/conversations/')) {
      return new Promise(resolve => { deferredPost.resolve = resolve as (value: unknown) => void })
    }
    return Promise.resolve([])
  }),
  reportFrontendError: vi.fn().mockResolvedValue(undefined),
}))

describe('frontend shell route contract', () => {
  it('mounts the command center and navigates to settings', async () => {
    render(<App />)
    expect(screen.getByRole('heading', { name: 'Command Center' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))
    // M2: explicit waitFor budget (same assertion). The default 1s budget is
    // tight for jsdom + React render under load; this was the flaky timeout.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument(), { timeout: 10000 })
  })

  it('shows live backend state instead of demo labels', async () => {
    render(<App />)
    // command center is a REAL backend surface now
    expect(screen.getByText('Local backend')).toBeInTheDocument()
    // gateway reports simulator_only in the mock, so the hero says LOCAL ENGINE
    await waitFor(() => expect(screen.getByText('LOCAL ENGINE')).toBeInTheDocument(), { timeout: 10000 })
    expect(screen.getByText(/Model Gateway is simulator-only/)).toBeInTheDocument()
    expect(screen.queryByText('DEMO STATE')).not.toBeInTheDocument()
  })

  it('exposes semantic navigation and accessible names for shell buttons', () => {
    render(<App />)
    expect(screen.getByRole('navigation', { name: 'Primary navigation' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Toggle navigation' })).toBeInTheDocument()
    const unnamed = screen.getAllByRole('button').filter(button => !button.getAttribute('aria-label') && !button.textContent?.trim())
    expect(unnamed).toHaveLength(0)
  })

  it('queues a real brief and disables the button while the request is in flight', async () => {
    render(<App />)
    const start = screen.getByRole('button', { name: 'Continue the flow' })
    fireEvent.click(start)
    // while the queue request is in flight the button is disabled: no duplicate queue
    await waitFor(() => expect(screen.getByRole('button', { name: 'Queueing…' })).toBeDisabled(), { timeout: 10000 })
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
    deferredPost.resolve({ messages: [] })
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Brief queued in the local backend.'), { timeout: 10000 })
    await waitFor(() => expect(screen.getByRole('button', { name: 'Continue the flow' })).toBeEnabled(), { timeout: 10000 })
  })

  it('persists the selected theme across a shell remount', async () => {
    const first = render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Open settings' }))
    // M2: same resilience as above; assertion unchanged.
    await waitFor(() => expect(screen.getByRole('heading', { name: 'Settings' })).toBeInTheDocument(), { timeout: 10000 })
    fireEvent.click(screen.getByRole('button', { name: /Flujo Light/ }))
    expect(document.documentElement.dataset.theme).toBe('light')
    first.unmount()
    render(<App />)
    expect(document.documentElement.dataset.theme).toBe('light')
  })
})
