import type { DesktopBackendRequest, DesktopBridgeResponse, SidecarStatus } from '../electron/ipc-contract'

export {}

declare global {
  interface Window {
    flujoDesktop?: {
      platform: string
      version: string
      mode: 'local-backend'
      backendUrl?: string
      requestBackend?: (request: DesktopBackendRequest) => Promise<DesktopBridgeResponse>
      reportError?: (payload: Record<string, unknown>) => Promise<boolean>
      onSidecarStatus?: (callback: (status: SidecarStatus) => void) => () => void
    }
  }
}
