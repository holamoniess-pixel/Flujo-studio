import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { RefinedChat } from './RefinedSurfaces'

const { MockBackendRequestError, fetchConversation, queueCreativeBrief } = vi.hoisted(() => {
  class HoistedBackendRequestError extends Error {
    readonly status = 0
    readonly code = 'OFFLINE'
    readonly retryable = true
    constructor(message: string) {
      super(message)
      this.name = 'BackendRequestError'
    }
  }
  return { MockBackendRequestError: HoistedBackendRequestError, fetchConversation: vi.fn(), queueCreativeBrief: vi.fn() }
})

vi.mock('./backendClient', () => ({
  BackendRequestError: MockBackendRequestError,
  backendHealth: vi.fn().mockResolvedValue({ status: 'ok', version: 'test', database: true, ffmpeg: true, ffprobe: true, worker_capacity: 1 }),
  fetchConversation,
  queueCreativeBrief,
  backendFetch: vi.fn(),
  reportFrontendError: vi.fn().mockResolvedValue(undefined),
}))

describe('Creative Chat reliability contract', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    fetchConversation.mockResolvedValue({ messages: [] })
  })

  it('renders backend conversation messages once loaded', async () => {
    fetchConversation.mockResolvedValue({
      messages: [
        { id: 'm1', conversation_id: 'conversation_citrus', role: 'user', content: 'Backend-seeded user brief', created_at: '2026-10-11T10:42:00' },
        { id: 'm2', conversation_id: 'conversation_citrus', role: 'assistant', content: 'Backend-seeded assistant reply', created_at: '2026-10-11T10:43:00' },
      ],
    })
    render(<RefinedChat go={vi.fn()} announce={vi.fn()} inspect={vi.fn()} theme="graphite" setTheme={vi.fn()} compact={false} setCompact={vi.fn()} />)
    await waitFor(() => expect(screen.getByText('Backend-seeded user brief')).toBeInTheDocument())
    expect(screen.getByText('Backend-seeded assistant reply')).toBeInTheDocument()
  })

  it('queues a brief through the backend on send and shows the live refetched reply', async () => {
    fetchConversation.mockResolvedValueOnce({ messages: [] }).mockResolvedValue({
      messages: [
        { id: 'm1', conversation_id: 'conversation_citrus', role: 'user', content: 'Build a local brief', created_at: '2026-10-11T10:42:00' },
        { id: 'm2', conversation_id: 'conversation_citrus', role: 'assistant', content: 'Live recorded reply from the engine room', created_at: '2026-10-11T10:44:00' },
      ],
    })
    queueCreativeBrief.mockResolvedValue({ job_id: 'job-1', workflow: 'creative_brief', status: 'queued', trace_id: 'trace-1' })
    render(<RefinedChat go={vi.fn()} announce={vi.fn()} inspect={vi.fn()} theme="graphite" setTheme={vi.fn()} compact={false} setCompact={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText('Tell Flujo what you want to create...'), { target: { value: 'Build a local brief' } })
    fireEvent.click(screen.getByRole('button', { name: /Send/ }))
    await waitFor(() => expect(queueCreativeBrief).toHaveBeenCalledTimes(1))
    expect(queueCreativeBrief.mock.calls[0][0]).toBe('Build a local brief')
    expect(queueCreativeBrief.mock.calls[0][2]).toMatch(/^chat-/)
    await waitFor(() => expect(screen.getByText('Live recorded reply from the engine room')).toBeInTheDocument(), { timeout: 10000 })
  })
  it('keeps a failed draft retryable with the same idempotency key', async () => {
    queueCreativeBrief.mockRejectedValueOnce(new MockBackendRequestError('Local engine is offline.')).mockResolvedValueOnce({ job_id: 'job-1', workflow: 'creative_brief', status: 'queued', trace_id: 'trace-1' })
    render(<RefinedChat go={vi.fn()} announce={vi.fn()} inspect={vi.fn()} theme="graphite" setTheme={vi.fn()} compact={false} setCompact={vi.fn()} />)
    fireEvent.change(screen.getByPlaceholderText('Tell Flujo what you want to create...'), { target: { value: 'Build a local brief' } })
    fireEvent.click(screen.getByRole('button', { name: /Send/ }))
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Offline'))
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }))
    await waitFor(() => expect(queueCreativeBrief).toHaveBeenCalledTimes(2))
    expect(queueCreativeBrief.mock.calls[0][2]).toBe(queueCreativeBrief.mock.calls[1][2])
  })
})
