export type DesktopBackendMethod = 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'OPTIONS'

export type DesktopBackendRequest = {
  path: string
  method?: DesktopBackendMethod
  headers?: Record<string, string>
  body?: string
}

export type DesktopBridgeResponse = {
  ok: boolean
  status: number
  body: unknown
}

export type SidecarState = 'starting' | 'ready' | 'timeout' | 'crashed' | 'port-conflict' | 'stopped' | 'restart-exhausted' | 'startup_failed' | 'spawn_error' | 'restart_scheduled' | 'stopping'

export type SidecarStatus = {
  state: SidecarState
  pid?: number
  port?: number
  reason?: string
  code?: number | null
  signal?: string | null
  attempt?: number
  delayMs?: number
}
