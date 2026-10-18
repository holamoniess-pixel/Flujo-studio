# Building Flujo Studio on Windows

## Option A: GitHub Actions (no toolchain on your machine)

This is the easiest path to a real `.exe` you can hand to a friend.

1. Push this repo to GitHub.
2. On github.com, open the repo, go to **Actions**, select **Windows build**,
   and click **Run workflow**.
3. When the run finishes, download the **flujo-studio-windows-portable**
   artifact and unzip it.
4. Hand the `.exe` to your friend. Portable, no install, no admin rights.

The workflow builds everything on a real Windows runner: the React
frontend, the frozen Python backend (`FlujoEngine.exe`), the Rust secure
executor, the pinned FFmpeg build, and the final portable package. The exe
is **unsigned** (fine for sharing with a friend; Windows SmartScreen shows a
one-time warning). Signing for public distribution needs your own
code-signing certificate, see the commented step in
`.github/workflows/windows-build.yml`.

## Option B: Build on your own Windows machine

This repo cannot ship you a ready-made `.exe` from anywhere except a Windows
machine. The final app bundles Windows-only binaries (the frozen Python
backend, the Rust executor, FFmpeg for Windows), so the last build step must
run on Windows. This guide gets you from this source tree to a working
portable app in one command.

## What you need installed

- **Node.js 20+** - https://nodejs.org (LTS). Provides `node` and `npm`.
- **Python 3.11+** - https://www.python.org. During install, tick
  **"Add python.exe to PATH"**.
- **Rust** (optional) - https://rustup.rs. Only needed if you change the
  executor source. A current `FlujoSecureExecutor.exe` is already staged in
  `resources/executor/windows-x64/`, so you can skip this.

No code-signing certificate is needed for a portable build you run yourself.
Windows SmartScreen will warn on first launch of any unsigned app; that is
normal. Signing (for public distribution) is a separate step and needs your
own certificate.

## The one command

Open PowerShell in the repo root and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\build-windows.ps1
```

It does, in order:

1. Checks Node, npm, Python are present.
2. `npm install` - installs the JavaScript dependencies.
3. `npm run build` - compiles the React frontend into `dist/`.
4. Installs the Python dependencies (`backend/requirements.txt` + PyInstaller).
5. Freezes the backend into `backend/dist/FlujoEngine.exe` with PyInstaller.
6. Rebuilds the Rust executor if `cargo` is available (otherwise keeps the
   staged binary).
7. Runs `electron-builder --win portable`.

When it finishes, your app is at:

```
release/Flujo Studio-<version>-portable.exe
```

Double-click it. No installer, no admin rights needed. The backend starts
automatically on loopback; your data stays in your Windows user profile.

## What is already staged for you

- `resources/media/windows-x64/` - FFmpeg + FFprobe for Windows with a
  manifest. The media engine resolves these without trusting PATH.
- `resources/executor/windows-x64/` - the current secure-executor binary.
- `resources/third-party/` and `FFMPEG_NOTICES.md` - license notices the
  packager bundles.

## Developer mode (run from source, no packaging)

```powershell
npm install
npm run backend:dev     # terminal 1: starts the Python backend
npm run electron:dev    # terminal 2: builds the frontend, opens the app
```

## Troubleshooting

- **`python` not recognized** - reinstall Python and tick "Add python.exe to
  PATH", then open a fresh PowerShell.
- **PyInstaller fails on a missing module** - the spec file
  `backend/flujo-engine.spec` lists every backend package explicitly. If you
  add a new package under `backend/app/`, add it to `hiddenimports` there.
- **electron-builder asks for wine** - that only happens on macOS/Linux.
  On Windows it builds natively.
- **SmartScreen warning on launch** - expected for an unsigned portable app.
  Click "More info" then "Run anyway". Signing removes this and requires
  your own code-signing certificate.
- **Backend won't start (port in use)** - the app uses port 47821 on
  loopback. Close any other copy of Flujo Studio and try again.

## What is still not done (honest list)

- The portable exe is **unsigned**. Fine for personal use; not for public
  distribution without your certificate.
- **FFmpeg redistribution review** and **"Flujo" trademark clearance** are
  still open legal items from the original project.
- The AI gateway runs in simulator mode until you store an OpenCode Zen API
  key in the app's credential vault (Setup screen).
