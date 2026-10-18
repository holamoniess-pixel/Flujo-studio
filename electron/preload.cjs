const { contextBridge, ipcRenderer } = require('electron')

const MAX_DIAGNOSTIC_BYTES = 64 * 1024

function serializeDiagnostic(payload) {
  try {
    const body = JSON.stringify({ category: 'renderer', message: 'frontend diagnostic', output_summary: payload })
    return Buffer.byteLength(body, 'utf8') <= MAX_DIAGNOSTIC_BYTES ? body : undefined
  } catch {
    return undefined
  }
}

contextBridge.exposeInMainWorld('flujoDesktop', {
  platform: process.platform,
  version: process.versions.electron,
  mode: 'local-backend',
  backendUrl: 'http://127.0.0.1:47821',
  requestBackend: request => ipcRenderer.invoke('flujo-backend-request', request),
  reportError: async payload => {
    const body = serializeDiagnostic(payload)
    if (!body) return false
    try {
      const result = await ipcRenderer.invoke('flujo-backend-request', {
        path: '/api/diagnostics/events',
        method: 'POST',
        body,
      })
      return Boolean(result?.ok)
    } catch {
      return false
    }
  },
  onSidecarStatus: callback => {
    if (typeof callback !== 'function') return () => {}
    const listener = (_event, state) => callback(state)
    ipcRenderer.on('flujo-sidecar-status', listener)
    return () => ipcRenderer.removeListener('flujo-sidecar-status', listener)
  },
})
