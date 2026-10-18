from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ExecutorResult:
    status: str
    error_code: str | None
    message: str
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: int
    operation_id: str
    trace_id: str | None
    project_id: str | None


class SecureExecutorUnavailable(RuntimeError):
    """Raised only when the secure executor cannot be used safely."""


class SecureExecutorClient:
    PROTOCOL_VERSION = 1
    MAX_TIMEOUT_MS = 1_800_000
    MAX_OUTPUT_BYTES = 1_048_576

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        configured = os.environ.get("FLUJO_EXECUTOR_PATH")
        self.executable = Path(configured).resolve() if configured else self._default_path()
        self.token = os.environ.get("FLUJO_EXECUTOR_TOKEN", "")

    def _default_path(self) -> Path:
        platform_root = self.project_root / "resources" / "executor" / "windows-x64"
        return platform_root / "FlujoSecureExecutor.exe"

    def _manifest(self) -> dict[str, Any]:
        path = self.executable.parent / "manifest.json"
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, json.JSONDecodeError):
            return {}

    def _sha256(self) -> str:
        digest = hashlib.sha256()
        with self.executable.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def health(self) -> dict[str, Any]:
        manifest = self._manifest()
        if not self.executable.is_file():
            return {"status": "missing", "code": "SECURE_EXECUTOR_MISSING", "path": str(self.executable), "sha256": None}
        try:
            actual = self._sha256()
        except OSError as exc:
            return {"status": "corrupt", "code": "SECURE_EXECUTOR_CORRUPT", "path": str(self.executable), "sha256": None, "message": str(exc)}
        expected = manifest.get("sha256")
        if not expected or actual.lower() != str(expected).lower():
            return {"status": "corrupt", "code": "SECURE_EXECUTOR_CORRUPT", "path": str(self.executable), "sha256": actual, "expected_sha256": expected}
        return {"status": "healthy", "code": None, "path": str(self.executable), "sha256": actual, "version": manifest.get("version")}

    def execute(
        self,
        *,
        operation_id: str,
        operation: str,
        trace_id: str | None,
        project_id: str | None,
        binary: Path,
        args: list[str],
        input_paths: list[Path],
        output_paths: list[Path],
        allowed_roots: list[Path],
        binary_roots: list[Path],
        timeout_seconds: int,
        max_output_bytes: int = 65_536,
    ) -> ExecutorResult:
        if not self.token:
            raise SecureExecutorUnavailable("The secure executor parent token is not configured.")
        health = self.health()
        if health["status"] != "healthy":
            raise SecureExecutorUnavailable("The compiled secure executor is missing or failed manifest verification.")
        timeout_ms = max(1, min(self.MAX_TIMEOUT_MS, int(timeout_seconds * 1000)))
        bounded_output = max(1, min(self.MAX_OUTPUT_BYTES, int(max_output_bytes)))
        payload = {
            "protocol_version": self.PROTOCOL_VERSION,
            "auth_token": self.token,
            "operation_id": operation_id,
            "operation": operation,
            "trace_id": trace_id,
            "project_id": project_id,
            "binary": str(binary),
            "args": args,
            "input_paths": [str(path) for path in input_paths],
            "output_paths": [str(path) for path in output_paths],
            "allowed_roots": [str(path) for path in allowed_roots],
            "binary_roots": [str(path) for path in binary_roots],
            "timeout_ms": timeout_ms,
            "max_output_bytes": bounded_output,
            "max_memory_bytes": 512 * 1024 * 1024,
            "max_cpu_time_ms": timeout_ms,
        }
        try:
            completed = subprocess.run(
                [str(self.executable)],
                input=(json.dumps(payload, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8"),
                capture_output=True,
                text=False,
                timeout=timeout_seconds + 10,
                check=False,
                shell=False,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except subprocess.TimeoutExpired as exc:
            raise SecureExecutorUnavailable("The secure executor process exceeded its parent timeout.") from exc
        except OSError as exc:
            raise SecureExecutorUnavailable(f"The secure executor could not start safely: {exc}") from exc
        stdout_text = (completed.stdout or b"").decode("utf-8", errors="replace")
        lines = [line for line in stdout_text.splitlines() if line.strip()]
        if not lines:
            raise SecureExecutorUnavailable("The secure executor returned no structured response.")
        try:
            result = json.loads(lines[-1])
        except json.JSONDecodeError as exc:
            raise SecureExecutorUnavailable("The secure executor returned malformed JSON.") from exc
        if completed.returncode != 0 and result.get("status") not in {"completed", "failed", "blocked"}:
            raise SecureExecutorUnavailable("The secure executor exited without a valid response.")
        return ExecutorResult(
            status=str(result.get("status", "failed")),
            error_code=result.get("error_code"),
            message=str(result.get("message", "The secure executor returned no message.")),
            exit_code=result.get("exit_code"),
            stdout=str(result.get("stdout", "")),
            stderr=str(result.get("stderr", "")),
            duration_ms=int(result.get("duration_ms", 0)),
            operation_id=str(result.get("operation_id", operation_id)),
            trace_id=result.get("trace_id", trace_id),
            project_id=result.get("project_id", project_id),
        )
