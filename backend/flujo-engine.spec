from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path.cwd()
hiddenimports = [
    "backend.app.main",
    "backend.app.db",
    "backend.app.constitution",
    "backend.app.adaptation",
    "backend.app.doctor",
    "backend.app.guard",
    "backend.app.guard.policy",
    "backend.app.gateway",
    "backend.app.gateway.contracts",
    "backend.app.gateway.meta_provider",
    "backend.app.gateway.openai_provider",
    "backend.app.gateway.zen_provider",
    "backend.app.gateway.nvidia_provider",
    "backend.app.governor",
    "backend.app.governor.live_consult",
    "backend.app.monitor",
    "backend.app.monitor.control",
    "backend.app.observability.events",
    "backend.app.media",
    "backend.app.media.binaries",
    "backend.app.media.engine",
    "backend.app.publishing",
    "backend.app.publishing.contracts",
    "backend.app.publishing.prep",
    "backend.app.publishing.youtube_adapter",
    "backend.app.security",
    "backend.app.security.credentials",
    "backend.app.workflows",
    "backend.app.workflows.engine",
    *collect_submodules("uvicorn"),
]


a = Analysis(
    [str(ROOT / "backend" / "run.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="FlujoEngine",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
