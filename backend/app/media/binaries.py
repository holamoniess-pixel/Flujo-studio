from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class BinaryCheck:
    name: str
    status: str
    code: str | None
    path: str | None
    version: str | None
    sha256: str | None
    expected_sha256: str | None
    message: str
    developer_fallback: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "code": self.code,
            "path": self.path,
            "version": self.version,
            "sha256": self.sha256,
            "expected_sha256": self.expected_sha256,
            "message": self.message,
            "developer_fallback": self.developer_fallback,
        }


class MediaBinaryManager:
    """Resolve and verify Flujo-owned media binaries without trusting PATH by default."""

    def __init__(self, media_root: Path | None = None) -> None:
        configured_root = os.environ.get("FLUJO_MEDIA_DIR")
        self.media_root = Path(media_root or configured_root or Path(__file__).resolve().parents[3] / "resources" / "media")
        architecture = (platform.machine() or os.environ.get("PROCESSOR_ARCHITEW6432") or os.environ.get("PROCESSOR_ARCHITECTURE") or "").lower()
        architecture = {"amd64": "x64", "x86_64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(architecture, architecture)
        if os.name == "nt" and architecture in {"", "unknown"}:
            architecture = "x64"
        self.platform_root = self.media_root / f"{platform.system().lower()}-{architecture}"
        self.manifest_path = self.platform_root / "manifest.json"
        self.manifest = self._load_manifest()
        self.developer_fallback = os.environ.get("FLUJO_DEVELOPER_MEDIA_FALLBACK", "0") == "1"

    def _load_manifest(self) -> dict[str, Any]:
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _configured(self, key: str) -> dict[str, Any]:
        value = self.manifest.get(key, {})
        return value if isinstance(value, dict) else {}

    def resolve_ffmpeg(self) -> Path | None:
        bundled = self.platform_root / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if bundled.is_file():
            return bundled
        return Path(shutil.which("ffmpeg")) if self.developer_fallback and shutil.which("ffmpeg") else None

    def resolve_ffprobe(self) -> Path | None:
        bundled = self.platform_root / ("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if bundled.is_file():
            return bundled
        return Path(shutil.which("ffprobe")) if self.developer_fallback and shutil.which("ffprobe") else None

    def _sha256(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _version(self, path: Path) -> str | None:
        try:
            result = subprocess.run([str(path), "-version"], capture_output=True, text=True, timeout=5, check=False, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            first_line = (result.stdout or result.stderr).splitlines()[0:1]
            return first_line[0][:300] if first_line else None
        except (OSError, subprocess.SubprocessError):
            return None

    def verify_binary(self, name: str, path: Path | None) -> BinaryCheck:
        if path is None:
            return BinaryCheck(name, "missing", "MEDIA_DEPENDENCY_MISSING", None, None, None, self._configured(name).get("sha256"), f"{name} is not bundled for this platform.", self.developer_fallback)
        configured = self._configured(name)
        expected_hash = configured.get("sha256")
        try:
            actual_hash = self._sha256(path)
            version = self._version(path)
        except (OSError, PermissionError) as exc:
            return BinaryCheck(name, "corrupt", "MEDIA_DEPENDENCY_CORRUPT", str(path), None, None, expected_hash, f"{name} could not be read: {exc}", self.developer_fallback)
        if expected_hash and actual_hash.lower() != str(expected_hash).lower():
            return BinaryCheck(name, "corrupt", "MEDIA_DEPENDENCY_CORRUPT", str(path), version, actual_hash, str(expected_hash), f"{name} checksum does not match the release manifest.", self.developer_fallback)
        if not version:
            return BinaryCheck(name, "corrupt", "MEDIA_DEPENDENCY_CORRUPT", str(path), None, actual_hash, expected_hash, f"{name} exists but did not execute successfully.", self.developer_fallback)
        expected_version = configured.get("version")
        if expected_version and str(expected_version) not in version:
            return BinaryCheck(name, "corrupt", "MEDIA_DEPENDENCY_CORRUPT", str(path), version, actual_hash, expected_hash, f"{name} version does not match the release manifest.", self.developer_fallback)
        return BinaryCheck(name, "healthy", None, str(path), version, actual_hash, expected_hash, f"{name} is available and verified.", self.developer_fallback)

    def health_check(self) -> dict[str, Any]:
        ffmpeg = self.verify_binary("ffmpeg", self.resolve_ffmpeg())
        ffprobe = self.verify_binary("ffprobe", self.resolve_ffprobe())
        if ffmpeg.status == "healthy" and ffprobe.status == "healthy":
            status = "healthy"
        elif ffmpeg.code == "MEDIA_DEPENDENCY_CORRUPT" or ffprobe.code == "MEDIA_DEPENDENCY_CORRUPT":
            status = "corrupt"
        else:
            status = "missing"
        return {"status": status, "ffmpeg": ffmpeg.as_dict(), "ffprobe": ffprobe.as_dict(), "developer_fallback": self.developer_fallback, "platform_root": str(self.platform_root)}

    # Compatibility aliases matching the engine-room contract language.
    def resolveFFmpeg(self) -> Path | None:
        return self.resolve_ffmpeg()

    def resolveFFprobe(self) -> Path | None:
        return self.resolve_ffprobe()

    def verifyBinary(self, name: str, path: Path | None) -> BinaryCheck:
        return self.verify_binary(name, path)

    def getVersion(self, path: Path) -> str | None:
        return self._version(path)

    def healthCheck(self) -> dict[str, Any]:
        return self.health_check()
