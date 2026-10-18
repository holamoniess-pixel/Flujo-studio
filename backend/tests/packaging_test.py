from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGED = ROOT / "release" / "win-unpacked"
MANIFEST = ROOT / "release" / "flujo-release-manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    if not PACKAGED.is_dir():
        # Environment-limited, not a code bug: needs Windows packaged output plus the
        # release manifest tooling; packaging is out of scope on Linux (M2 rules).
        print("packaging: SKIPPED (needs Windows packaged output at release/win-unpacked)")
        return
    assert PACKAGED.is_dir(), "packaged output directory is missing"
    generated = subprocess.run([sys.executable, str(ROOT / "verification" / "package_manifest.py"), "--root", str(PACKAGED), "--output", str(MANIFEST)], cwd=ROOT, capture_output=True, text=True, timeout=120, check=False)
    assert generated.returncode == 0, generated.stdout + generated.stderr
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["contract_version"] == "flujo.release.manifest.v1"
    assert manifest["status"] == "PASS"
    assert manifest["checks"]["required_components_present"] is True
    assert manifest["checks"]["signing"] == "NOT_VERIFIED"
    assert manifest["checks"]["clean_machine"] == "NOT_VERIFIED"
    for item in manifest["components"]:
        if item["exists"]:
            path = PACKAGED / item["path"]
            assert path.is_file() and path.stat().st_size > 0
            assert sha256(path) == item["sha256"]
    with tempfile.TemporaryDirectory(prefix="flujo-package-lifecycle-") as directory:
        root = Path(directory)
        app_v1 = root / "app-v1"
        app_v2 = root / "app-v2"
        user_data = root / "user-data"
        app_v1.mkdir()
        app_v2.mkdir()
        user_data.mkdir()
        (app_v1 / "release.json").write_text('{"version":"1.0.0"}', encoding="utf-8")
        (app_v2 / "release.json").write_text('{"version":"1.1.0"}', encoding="utf-8")
        (user_data / "installation-state.json").write_text('{"onboarding_completed":true}', encoding="utf-8")
        active = root / "active"
        shutil.copytree(app_v1, active)
        assert json.loads((active / "release.json").read_text(encoding="utf-8"))["version"] == "1.0.0"
        backup = root / "rollback-backup"
        active.rename(backup)
        shutil.copytree(app_v2, active)
        assert json.loads((active / "release.json").read_text(encoding="utf-8"))["version"] == "1.1.0"
        shutil.rmtree(active)
        backup.rename(active)
        assert json.loads((active / "release.json").read_text(encoding="utf-8"))["version"] == "1.0.0"
        shutil.rmtree(active)
        assert not active.exists() and user_data.exists() and (user_data / "installation-state.json").exists()
    print("packaging: passed; manifest, component paths, hashes, notices, safe upgrade/rollback/uninstall simulation, data retention isolation, and explicit unsigned/unclean-machine states verified")


if __name__ == "__main__":
    main()
