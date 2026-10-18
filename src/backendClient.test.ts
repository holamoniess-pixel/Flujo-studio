import { describe, expect, it, vi } from 'vitest'
import { backendFetch } from './backendClient'

describe('frontend backend request boundary', () => {
  it('rejects unsupported methods instead of silently converting them to GET', async () => {
    window.flujoDesktop = {
      platform: 'test',
      version: 'test',
      mode: 'local-backend',
      requestBackend: vi.fn(),
    }
    await expect(backendFetch('/health', { method: 'TRACE' })).rejects.toMatchObject({
      code: 'INVALID_FRONTEND_METHOD',
      status: 400,
    })
    expect(window.flujoDesktop.requestBackend).not.toHaveBeenCalled()
  })

  it('rejects traversal and absolute paths before crossing the bridge', async () => {
    const requestBackend = vi.fn()
    window.flujoDesktop = { platform: 'test', version: 'test', mode: 'local-backend', requestBackend }
    await expect(backendFetch('/api/../secrets')).rejects.toMatchObject({ code: 'INVALID_FRONTEND_PATH' })
    await expect(backendFetch('/api\\secret')).rejects.toMatchObject({ code: 'INVALID_FRONTEND_PATH' })
    await expect(backendFetch('https://evil.example/api')).rejects.toMatchObject({ code: 'INVALID_FRONTEND_PATH' })
    expect(requestBackend).not.toHaveBeenCalled()
  })

  it('rejects oversized string bodies before crossing the bridge', async () => {
    const requestBackend = vi.fn()
    window.flujoDesktop = { platform: 'test', version: 'test', mode: 'local-backend', requestBackend }
    const body = 'x'.repeat(1_048_577)
    await expect(backendFetch('/api/test', { method: 'POST', body })).rejects.toMatchObject({
      code: 'FRONTEND_REQUEST_TOO_LARGE',
      status: 413,
    })
    expect(requestBackend).not.toHaveBeenCalled()
  })

  it('sends valid bounded requests through the existing bridge contract', async () => {
    const requestBackend = vi.fn().mockResolvedValue({ ok: true, status: 200, body: { accepted: true } })
    window.flujoDesktop = { platform: 'test', version: 'test', mode: 'local-backend', requestBackend }
    await expect(backendFetch<{ accepted: boolean }>('/api/diagnostics/events', { method: 'POST', body: '{"ok":true}' })).resolves.toEqual({ accepted: true })
    expect(requestBackend).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/diagnostics/events', method: 'POST', body: '{"ok":true}' }))
  })
})

describe('publish backend client', () => {
  it('fetches adapter status through the bridge', async () => {
    const requestBackend = vi.fn().mockResolvedValue({ ok: true, status: 200, body: [{ platform_id: 'youtube', configured: false }] })
    window.flujoDesktop = { platform: 'test', version: 'test', mode: 'local-backend', requestBackend }
    const { fetchPublishAdapters } = await import('./backendClient')
    await expect(fetchPublishAdapters()).resolves.toEqual([{ platform_id: 'youtube', configured: false }])
    expect(requestBackend).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/publish/adapters', method: 'GET' }))
  })

  it('starts a prep run with a bounded payload and uploads through the prep run id', async () => {
    const requestBackend = vi.fn()
      .mockResolvedValueOnce({ ok: true, status: 200, body: { run_id: 'publish-prep_1', status: 'completed' } })
      .mockResolvedValueOnce({ ok: true, status: 200, body: { run_id: 'publish-prep_1', status: 'uploaded' } })
    window.flujoDesktop = { platform: 'test', version: 'test', mode: 'local-backend', requestBackend }
    const { startPublishPrep, uploadPublishPrepRun } = await import('./backendClient')
    await expect(startPublishPrep('project_northstar', 'artifact_1')).resolves.toEqual({ run_id: 'publish-prep_1', status: 'completed' })
    expect(requestBackend).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/projects/project_northstar/publish/prep', method: 'POST', body: '{"artifact_id":"artifact_1","platform":"youtube"}' }))
    await expect(uploadPublishPrepRun('publish-prep_1')).resolves.toEqual({ run_id: 'publish-prep_1', status: 'uploaded' })
    expect(requestBackend).toHaveBeenCalledWith(expect.objectContaining({ path: '/api/publish/prep/publish-prep_1/upload', method: 'POST' }))
  })
})
