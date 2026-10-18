const { app, BrowserWindow, shell, ipcMain } = require('electron')
const path = require('node:path')
const fs = require('node:fs')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, spawnSync } = require('node:child_process')
const {
  IPC_CHANNELS,
  validateBackendRequest,
  buildForwardHeaders,
  isApprovedExternalUrl,
} = require('./shellContracts.cjs')
const { configureUserData } = require('./userData.cjs')
const { waitForHealthy, restartDecision, terminateProcessTree, parseBoundedJson } = require('./sidecarSupervisor.cjs')

const backendPort = 47821
const backendToken = crypto.randomBytes(32).toString('hex')
const executorToken = crypto.randomBytes(32).toString('hex')
let backendProcess
let backendStartPromise
let mainWindow
let shuttingDown = false
let restartAttempt = 0
let rendererRecoveryAttempt = 0
const expectedBackendExit = new Set()
let restartTimer

function projectRoot() {
  return app.isPackaged ? path.join(process.resourcesPath, 'app.asar.unpacked') : path.join(__dirname, '..')
}

function runtimeRoot() {
  return app.getPath('userData')
}

function flujoDataRoot() {
  return path.join(runtimeRoot(), '.flujo')
}

function runtimeLog() {
  return path.join(flujoDataRoot(), 'logs', 'electron.jsonl')
}

function backendPidFile() {
  return path.join(flujoDataRoot(), 'runtime', 'backend.pid.json')
}

function packagedPaths() {
  const root = projectRoot()
  const appRoot = app.isPackaged ? app.getAppPath() : root
  return {
    root,
    renderer: path.join(appRoot, 'dist', 'index.html'),
    backend: path.join(root, 'backend', 'dist', 'FlujoEngine.exe'),
    executor: path.join(root, 'resources', 'executor', 'windows-x64', 'FlujoSecureExecutor.exe'),
    ffmpeg: path.join(root, 'resources', 'media', 'windows-x64', 'ffmpeg.exe'),
    ffprobe: path.join(root, 'resources', 'media', 'windows-x64', 'ffprobe.exe'),
    executorManifest: path.join(root, 'resources', 'executor', 'windows-x64', 'manifest.json'),
    mediaManifest: path.join(root, 'resources', 'media', 'windows-x64', 'manifest.json'),
    notices: path.join(appRoot, 'resources', 'third-party', 'FFMPEG_NOTICES.md'),
    license: path.join(root, 'resources', 'media', 'windows-x64', 'LICENSE.txt'),
    configuration: path.join(flujoDataRoot(), 'config'),
  }
}

function logEvent(event, detail = {}) {
  try {
    fs.mkdirSync(path.dirname(runtimeLog()), { recursive: true })
    fs.appendFileSync(runtimeLog(), `${JSON.stringify({ timestamp: new Date().toISOString(), event, ...detail })}\n`)
  } catch {
    // Diagnostics must never prevent a safe desktop state.
  }
}

function emitSidecarStatus(state, detail = {}) {
  logEvent(`sidecar_${state}`, detail)
  if (mainWindow && !mainWindow.isDestroyed()) {
    try { mainWindow.webContents.send(IPC_CHANNELS.SIDECAR_STATUS, { state, ...detail }) } catch (error) {
      logEvent('sidecar_status_delivery_error', { state, message: error instanceof Error ? error.message : String(error) })
    }
  }
}

function backendEnvironment(cwd) {
  const env = {}
  for (const key of ['PATH', 'PATHEXT', 'SystemRoot', 'WINDIR', 'TEMP', 'TMP']) {
    if (process.env[key]) env[key] = process.env[key]
  }
  return {
    ...env,
    PYTHONUTF8: '1',
    FLUJO_PROJECT_ROOT: cwd,
    FLUJO_DATA_DIR: flujoDataRoot(),
    FLUJO_MEDIA_DIR: path.join(cwd, 'resources', 'media'),
    FLUJO_HOST: '127.0.0.1',
    FLUJO_PORT: String(backendPort),
    FLUJO_APP_TOKEN: backendToken,
    FLUJO_EXECUTOR_TOKEN: executorToken,
  }
}

function probePort() {
  return new Promise(resolve => {
    const socket = net.createConnection({ host: '127.0.0.1', port: backendPort })
    let finished = false
    const finish = result => {
      if (finished) return
      finished = true
      socket.destroy()
      resolve(result)
    }
    socket.once('connect', () => finish(true))
    socket.once('error', () => finish(false))
    socket.setTimeout(750, () => finish(false))
  })
}

async function probeHealth() {
  try {
    const response = await fetch(`http://127.0.0.1:${backendPort}/health`, { signal: AbortSignal.timeout(1500) })
    const text = await response.text()
    const parsed = parseBoundedJson(text)
    if (!parsed.ok) return { ok: false, reason: parsed.reason }
    return { ok: response.ok, payload: parsed.payload, reason: response.ok ? undefined : `http_${response.status}` }
  } catch (error) {
    return { ok: false, reason: error instanceof Error ? error.message : 'health_probe_failed' }
  }
}

function processExists(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false
  if (process.platform !== 'win32') {
    try { process.kill(pid, 0); return true } catch { return false }
  }
  const result = spawnSync('tasklist', ['/fi', `PID eq ${pid}`, '/fo', 'csv', '/nh'], { windowsHide: true, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })
  return result.status === 0 && typeof result.stdout === 'string' && result.stdout.includes(`"${pid}"`)
}

function cleanupStaleBackend(executable) {
  try {
    if (!fs.existsSync(backendPidFile())) return
    const record = JSON.parse(fs.readFileSync(backendPidFile(), 'utf8'))
    if (record.executable !== executable || !Number.isInteger(record.pid)) {
      logEvent('stale_backend_pid_ignored', { reason: 'pid_record_mismatch' })
      return
    }
    if (processExists(record.pid)) {
      const result = terminateProcessTree({ pid: record.pid })
      logEvent('stale_backend_cleaned', { pid: record.pid, result })
    } else {
      logEvent('stale_backend_pid_removed', { pid: record.pid })
    }
    fs.rmSync(backendPidFile(), { force: true })
  } catch (error) {
    logEvent('stale_backend_cleanup_error', { message: error instanceof Error ? error.message : String(error) })
  }
}

async function startBackend({ isRestart = false } = {}) {
  if (backendStartPromise) return backendStartPromise
  if (backendProcess) return true
  backendStartPromise = (async () => {
    const paths = packagedPaths()
    const executable = paths.backend
    if (app.isPackaged) cleanupStaleBackend(executable)
    if (await probePort()) {
      emitSidecarStatus('port_conflict', { port: backendPort })
      return false
    }
    const cwd = projectRoot()
    const command = app.isPackaged ? executable : (process.platform === 'win32' ? 'py' : 'python3')
    const args = app.isPackaged ? [] : ['-u', path.join(cwd, 'backend', 'run.py')]
    if (!fs.existsSync(paths.renderer)) {
      emitSidecarStatus('startup_failed', { reason: 'renderer_bundle_missing', path: paths.renderer })
      return false
    }
    if (app.isPackaged && !fs.existsSync(executable)) {
      emitSidecarStatus('startup_failed', { reason: 'backend_bundle_missing', path: executable })
      return false
    }
    try {
      backendProcess = spawn(command, args, {
        cwd,
        windowsHide: true,
        env: backendEnvironment(cwd),
        stdio: ['ignore', 'pipe', 'pipe'],
      })
      backendProcess.stdout?.on('data', data => logEvent('backend_stdout', { message: String(data).slice(0, 2000) }))
      backendProcess.stderr?.on('data', data => logEvent('backend_stderr', { message: String(data).slice(0, 2000) }))
      backendProcess.on('error', error => emitSidecarStatus('spawn_error', { message: error.message }))
      const child = backendProcess
      child.on('exit', (code, signal) => {
        const pid = child.pid
        const expected = shuttingDown || expectedBackendExit.delete(pid)
        if (backendProcess === child) backendProcess = undefined
        try { fs.rmSync(backendPidFile(), { force: true }) } catch {}
        emitSidecarStatus(expected ? 'stopped' : 'crashed', { code, signal, pid })
        if (!expected) scheduleBackendRestart()
      })
      fs.mkdirSync(path.dirname(backendPidFile()), { recursive: true })
      fs.writeFileSync(backendPidFile(), JSON.stringify({ pid: backendProcess.pid, executable, started_at: new Date().toISOString() }))
      emitSidecarStatus('starting', { pid: backendProcess.pid, port: backendPort, restart: isRestart })
      const health = await waitForHealthy(probeHealth, { timeoutMs: app.isPackaged ? 60000 : 30000 })
      if (!health.ready) {
        emitSidecarStatus('timeout', { pid: backendProcess.pid, port: backendPort, reason: health.reason })
        stopBackend('health-timeout', { restart: true })
        return false
      }
      restartAttempt = 0
      emitSidecarStatus('ready', { pid: backendProcess.pid, port: backendPort })
      return true
    } catch (error) {
      backendProcess = undefined
      emitSidecarStatus('startup_failed', { message: error instanceof Error ? error.message : String(error) })
      return false
    } finally {
      backendStartPromise = undefined
    }
  })()
  return backendStartPromise
}

function scheduleBackendRestart() {
  if (shuttingDown || restartTimer) return
  const decision = restartDecision(restartAttempt)
  if (!decision.restart) {
    emitSidecarStatus('restart_exhausted', { attempts: restartAttempt })
    return
  }
  const attempt = restartAttempt
  restartAttempt += 1
  emitSidecarStatus('restart_scheduled', { attempt: attempt + 1, delayMs: decision.delayMs })
  restartTimer = setTimeout(() => {
    restartTimer = undefined
    void startBackend({ isRestart: true })
  }, decision.delayMs)
}

function stopBackend(reason = 'app-quit', { restart = false } = {}) {
  if (restartTimer) {
    clearTimeout(restartTimer)
    restartTimer = undefined
  }
  const trackedPid = backendProcess?.pid
  let pid = trackedPid
  if (!pid && fs.existsSync(backendPidFile())) {
    try {
      const record = JSON.parse(fs.readFileSync(backendPidFile(), 'utf8'))
      if (record.executable === packagedPaths().backend) pid = record.pid
    } catch {}
  }
  if (pid) {
    expectedBackendExit.add(pid)
    const result = terminateProcessTree({ pid })
    emitSidecarStatus('stopping', { pid, reason, result })
  }
  try { fs.rmSync(backendPidFile(), { force: true }) } catch {}
  backendProcess = undefined
  if (restart && !shuttingDown) scheduleBackendRestart()
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]))
}

function safeWindowState(window, title, message) {
  if (!window || window.isDestroyed()) return
  const html = `<!doctype html><meta charset="utf-8"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'"><title>${escapeHtml(title)}</title><style>body{font-family:system-ui,sans-serif;background:#171716;color:#f2eee7;margin:0;display:grid;place-items:center;min-height:100vh}main{max-width:640px;padding:32px;border:1px solid #3c3832;border-radius:14px;background:#201f1d}h1{font-size:22px}p{color:#a6a095;line-height:1.6}code{color:#d87f65}</style><main><h1>${escapeHtml(title)}</h1><p>${escapeHtml(message)}</p><p><code>Flujo Studio remains in a safe local state. Restart the application to try again.</code></p></main>`
  void window.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(html)}`)
}

function isInternalRendererUrl(rawUrl) {
  try {
    const parsed = new URL(rawUrl)
    if (parsed.protocol !== 'file:') return false
    return path.normalize(require('node:url').fileURLToPath(parsed)) === path.normalize(packagedPaths().renderer)
  } catch {
    return false
  }
}

function openApprovedExternal(rawUrl) {
  if (!isApprovedExternalUrl(rawUrl)) {
    logEvent('external_navigation_blocked', { url: String(rawUrl).slice(0, 500), reason: 'destination_not_approved' })
    return false
  }
  void shell.openExternal(rawUrl).catch(error => logEvent('external_navigation_error', { message: error.message }))
  return true
}

function createWindow() {
  const window = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 1120,
    minHeight: 700,
    backgroundColor: '#171716',
    title: 'Flujo Studio',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
      webSecurity: true,
      devTools: !app.isPackaged,
    },
  })
  mainWindow = window
  window.webContents.setWindowOpenHandler(({ url }) => {
    openApprovedExternal(url)
    return { action: 'deny' }
  })
  window.webContents.on('will-attach-webview', event => event.preventDefault())
  window.webContents.on('will-navigate', (event, url) => {
    if (isInternalRendererUrl(url)) return
    event.preventDefault()
    openApprovedExternal(url)
  })
  window.webContents.on('render-process-gone', (_event, details) => {
    logEvent('renderer_gone', { reason: details.reason, exitCode: details.exitCode })
    if (rendererRecoveryAttempt < 1 && !window.isDestroyed()) {
      rendererRecoveryAttempt += 1
      setTimeout(() => { if (!window.isDestroyed()) void window.loadFile(packagedPaths().renderer) }, 500)
    } else safeWindowState(window, 'Flujo Studio renderer stopped safely', `The renderer process stopped (${details.reason || 'unknown reason'}).`)
  })
  window.webContents.on('console-message', (_event, details) => { if (details.level >= 2) logEvent('renderer_console', { level: details.level, message: details.message.slice(0, 2000) }) })
  window.webContents.on('did-fail-load', (_event, code, description, validatedURL) => {
    logEvent('renderer_load_failed', { code, description, validatedURL })
    if (!window.isDestroyed() && code !== -3) safeWindowState(window, 'Flujo Studio could not load', `The renderer could not load (${description || code}).`)
  })
  window.on('closed', () => { if (mainWindow === window) mainWindow = undefined })
  void window.loadFile(packagedPaths().renderer)
  return window
}

ipcMain.handle(IPC_CHANNELS.BACKEND_REQUEST, async (_event, input) => {
  const validated = validateBackendRequest(input)
  if (!validated.ok) return validated.result
  const request = validated.request
  try {
    const response = await fetch(`http://127.0.0.1:${backendPort}${request.path}`, {
      method: request.method,
      headers: buildForwardHeaders(request.headers, backendToken),
      body: request.method === 'GET' || request.method === 'OPTIONS' ? undefined : request.body,
      signal: AbortSignal.timeout(15000),
    })
    const text = await response.text()
    const parsed = parseBoundedJson(text)
    if (!parsed.ok) {
      logEvent('backend_response_invalid', { path: request.path, reason: parsed.reason })
      return { ok: false, status: 502, body: { error: { code: parsed.reason === 'response_too_large' ? 'BACKEND_RESPONSE_TOO_LARGE' : 'MALFORMED_BACKEND_RESPONSE', message: 'The local engine returned an invalid response.', retryable: true } } }
    }
    return { ok: response.ok, status: response.status, body: parsed.payload }
  } catch (error) {
    logEvent('backend_request_error', { path: request.path, message: error instanceof Error ? error.message : String(error) })
    return { ok: false, status: 503, body: { error: { code: 'LOCAL_ENGINE_UNAVAILABLE', message: 'The local engine room is unavailable.', retryable: true } } }
  }
})

const hasSingleInstance = app.requestSingleInstanceLock()
if (!hasSingleInstance) {
  app.quit()
} else {
  app.on('second-instance', () => {
    if (!mainWindow || mainWindow.isDestroyed()) return
    if (mainWindow.isMinimized()) mainWindow.restore()
    mainWindow.show()
    mainWindow.focus()
  })
  app.whenReady().then(async () => {
    try {
      const userData = configureUserData({ app, env: process.env, legacyFlujoDir: path.join(projectRoot(), '.flujo') })
      logEvent('user_data_configured', { mode: userData.policy.mode, path: userData.policy.path, migrated: userData.layout.migrated, quarantined: userData.layout.quarantined, schemaRecovered: userData.layout.schemaRecovered })
    } catch (error) {
      logEvent('user_data_configuration_error', { message: error instanceof Error ? error.message : String(error) })
    }
    createWindow()
    void startBackend().catch(error => emitSidecarStatus('startup_failed', { message: error instanceof Error ? error.message : String(error) }))
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow()
    })
  }).catch(error => {
    logEvent('app_start_error', { message: error instanceof Error ? error.message : String(error) })
    if (!mainWindow) {
      app.whenReady().then(() => createWindow()).catch(() => {})
    }
  })
}

app.on('before-quit', () => {
  shuttingDown = true
  logEvent('app_quit')
  stopBackend('app-quit')
})
app.on('will-quit', () => stopBackend('will-quit'))
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit() })

module.exports = {
  packagedPaths,
  backendEnvironment,
  safeWindowState,
}
