<#
  Flujo Studio - one-command Windows build.
  Run from the repo root:  powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
  Output: release/Flujo Studio-<version>-portable.exe
#>
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

function Require-Command($name, $hint) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    Write-Host "MISSING: $name. $hint" -ForegroundColor Red
    exit 1
  }
}

Require-Command "node" "Install Node.js 20+ from https://nodejs.org"
Require-Command "npm" "Install Node.js 20+ from https://nodejs.org"
Require-Command "python" "Install Python 3.11+ from https://www.python.org (tick 'Add python to PATH')"

Write-Host ""
Write-Host "[1/6] Tool versions"
node --version
npm --version
python --version

Write-Host ""
Write-Host "[2/6] Installing JS dependencies (this takes a few minutes the first time)"
npm install

Write-Host ""
Write-Host "[3/6] Building the frontend"
npm run build

Write-Host ""
Write-Host "[4/6] Installing Python dependencies"
python -m pip install --upgrade pip
python -m pip install -r backend/requirements.txt pyinstaller

Write-Host ""
Write-Host "[5/6] Freezing the backend to backend/dist/FlujoEngine.exe"
if (Test-Path "backend/dist") { Remove-Item -Recurse -Force "backend/dist" }
if (Test-Path "backend/build") { Remove-Item -Recurse -Force "backend/build" }
python -m PyInstaller --noconfirm --clean backend/flujo-engine.spec --distpath backend/dist --workpath backend/build
if (-not (Test-Path "backend/dist/FlujoEngine.exe")) {
  Write-Host "PyInstaller did not produce backend/dist/FlujoEngine.exe" -ForegroundColor Red
  exit 1
}

$executorSrc = "executor/target/release/flujo-secure-executor.exe"
if ((Get-Command "cargo" -ErrorAction SilentlyContinue) -and (Test-Path "executor/Cargo.toml")) {
  Write-Host ""
  Write-Host "[5b/6] Rebuilding the Rust executor (optional, a current binary is already staged)"
  Push-Location "executor"
  cargo build --release
  Pop-Location
  if (Test-Path $executorSrc) {
    $dest = "resources/executor/windows-x64/FlujoSecureExecutor.exe"
    New-Item -ItemType Directory -Force -Path (Split-Path $dest) | Out-Null
    Copy-Item $executorSrc $dest -Force
    Write-Host "Executor refreshed at $dest"
  }
} else {
  Write-Host ""
  Write-Host "[5b/6] Skipping executor rebuild (cargo not found). Using the staged binary."
}

Write-Host ""
Write-Host "[6/6] Packaging the portable Windows app"
npx electron-builder --win portable

Write-Host ""
Write-Host "DONE. Your app is in the release/ folder."
Get-ChildItem "release/*.exe" | ForEach-Object { Write-Host "  $($_.FullName)" }
