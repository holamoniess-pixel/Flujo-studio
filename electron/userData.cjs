const fs = require('node:fs')
const path = require('node:path')
const { resolveUserDataPolicy } = require('./shellContracts.cjs')

const DATA_SCHEMA_VERSION = 1
const MIGRATABLE_FILES = Object.freeze([
  path.join('runtime', 'flujo.sqlite3'),
  path.join('runtime', 'flujo.sqlite3-wal'),
  path.join('runtime', 'flujo.sqlite3-shm'),
])

function timestampSuffix(now = new Date()) {
  return now.toISOString().replace(/[:.]/g, '-')
}

function safeRenameCorrupt(filePath, now) {
  if (!fs.existsSync(filePath)) return undefined
  const destination = `${filePath}.corrupt-${timestampSuffix(now)}`
  fs.renameSync(filePath, destination)
  return destination
}

function isSqliteFile(filePath) {
  if (!fs.existsSync(filePath)) return true
  const handle = fs.openSync(filePath, 'r')
  try {
    const header = Buffer.alloc(16)
    fs.readSync(handle, header, 0, 16, 0)
    return header.toString('utf8') === 'SQLite format 3\u0000'
  } finally {
    fs.closeSync(handle)
  }
}

function ensureUserDataLayout({ dataRoot, legacyFlujoDir, now = new Date() }) {
  if (!path.isAbsolute(dataRoot)) throw new Error('Electron data root must be absolute.')
  const flujoDir = path.join(dataRoot, '.flujo')
  const runtimeDir = path.join(flujoDir, 'runtime')
  const logsDir = path.join(flujoDir, 'logs')
  const configDir = path.join(flujoDir, 'config')
  for (const directory of [flujoDir, runtimeDir, logsDir, configDir]) fs.mkdirSync(directory, { recursive: true })

  const migrated = []
  if (legacyFlujoDir && path.resolve(legacyFlujoDir) !== path.resolve(flujoDir) && !fs.existsSync(path.join(runtimeDir, 'flujo.sqlite3'))) {
    for (const relative of MIGRATABLE_FILES) {
      const source = path.join(legacyFlujoDir, relative)
      const destination = path.join(flujoDir, relative)
      if (fs.existsSync(source)) {
        fs.mkdirSync(path.dirname(destination), { recursive: true })
        fs.copyFileSync(source, destination)
        migrated.push(relative)
      }
    }
  }

  const quarantined = []
  const database = path.join(runtimeDir, 'flujo.sqlite3')
  if (!isSqliteFile(database)) {
    const destination = safeRenameCorrupt(database, now)
    if (destination) quarantined.push(destination)
    for (const suffix of ['-wal', '-shm']) {
      const associated = safeRenameCorrupt(`${database}${suffix}`, now)
      if (associated) quarantined.push(associated)
    }
  }

  const schemaPath = path.join(flujoDir, 'data-schema.json')
  let schemaRecovered = false
  if (fs.existsSync(schemaPath)) {
    try {
      const schema = JSON.parse(fs.readFileSync(schemaPath, 'utf8'))
      if (!schema || schema.version !== DATA_SCHEMA_VERSION) throw new Error('unsupported data schema')
    } catch {
      safeRenameCorrupt(schemaPath, now)
      schemaRecovered = true
    }
  }
  if (!fs.existsSync(schemaPath)) {
    fs.writeFileSync(schemaPath, `${JSON.stringify({ version: DATA_SCHEMA_VERSION, initialized_at: new Date(now).toISOString() }, null, 2)}\n`, { flag: 'wx' })
  }
  return { flujoDir, runtimeDir, logsDir, configDir, schemaPath, migrated, quarantined, schemaRecovered }
}

function configureUserData({ app, env = process.env, legacyFlujoDir }) {
  const defaultPath = app.getPath('userData')
  const policy = resolveUserDataPolicy({
    defaultPath,
    customPath: env.FLUJO_USER_DATA_DIR,
    portableDir: app.isPackaged ? env.PORTABLE_EXECUTABLE_DIR : undefined,
  })
  if (policy.path !== defaultPath) app.setPath('userData', policy.path)
  const layout = ensureUserDataLayout({ dataRoot: policy.path, legacyFlujoDir })
  return { policy, layout }
}

module.exports = {
  DATA_SCHEMA_VERSION,
  MIGRATABLE_FILES,
  isSqliteFile,
  ensureUserDataLayout,
  configureUserData,
}
