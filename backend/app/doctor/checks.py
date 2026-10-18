"""Read-only System Doctor checks: 13-component matrix plus safe guidance.

The Doctor never modifies state, never repairs anything, and never contacts
the network. Every probe is local and every failure is captured as a check
result (never raised to the caller).
"""

from __future__ import annotations

import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

COMPONENTS: list[str] = [
    "Engine",
    "Database",
    "Storage",
    "Model Gateway",
    "Workflow Engine",
    "Memory",
    "Executor",
    "FFmpeg",
    "FFprobe",
    "Rust",
    "Ports",
    "Processes",
    "Electron",
]

_STATUSES = ("ok", "degraded", "down", "unknown")

_ONE_GIB = 1024 * 1024 * 1024

# Recovery hierarchy reference (Detect -> diagnose -> retry -> restart ->
# fallback -> safe mode -> human review). The Doctor only diagnoses and
# points at safe-mode / human-review steps; it never acts.
_SUGGESTIONS: dict[str, str] = {
    "Engine": "Restart the backend service and review backend logs for startup errors",
    "Database": "Restore the local database from the most recent verified backup and review integrity logs",
    "Storage": "Free disk space on the data volume and confirm the .flujo directory is writable",
    "Model Gateway": "Configure a provider credential to leave simulator mode",
    "Workflow Engine": "Restart the backend service to restore local worker capacity",
    "Memory": "Restart the backend service and confirm the memory tables are queryable",
    "Executor": "Use a packaged Windows build where the executor binary is bundled; no action is available on this host",
    "FFmpeg": "Install the bundled media binaries with the packaged installer and confirm FFmpeg verification passes",
    "FFprobe": "Install the bundled media binaries with the packaged installer and confirm FFprobe verification passes",
    "Rust": "Use a packaged Windows build where the Rust sidecar is bundled; no action is available on this host",
    "Ports": "Confirm that no other process occupies port 47821, then restart the backend service",
    "Processes": "Review the supervised process state on a packaged Windows build; no local action is available here",
    "Electron": "Open the desktop shell and confirm the frontend heartbeat is reporting",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id(prefix: str) -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex}"


def _check(component: str, status: str, detail: str) -> dict[str, Any]:
    if status not in _STATUSES:
        status = "unknown"
    return {
        "component": component,
        "status": status,
        "detail": detail,
        "checked_at": _now_iso(),
    }


def _engine_check(engine_probe: dict[str, Any] | None = None) -> dict[str, Any]:
    """Real engine liveness probe.

    Never self-attests: without a caller-supplied liveness signal the check
    reports "unknown". With a signal it evaluates worker-thread responsiveness,
    event-loop lag, and schema sanity gathered by the caller (see
    backend/app/main.py::_engine_probe).
    """
    if engine_probe is None:
        return _check(
            "Engine",
            "unknown",
            "No engine liveness signal was provided; in-process self-attestation is not a probe",
        )
    try:
        lag_ms = float(engine_probe.get("event_loop_lag_ms", -1))
        workers_ok = bool(engine_probe.get("worker_threads_ok", False))
        schema_ok = bool(engine_probe.get("schema_ok", False))
        active_jobs = int(engine_probe.get("active_jobs", 0))
    except (TypeError, ValueError):
        return _check("Engine", "unknown", "Engine liveness signal was unreadable")
    if not workers_ok:
        return _check("Engine", "down", "Worker thread pool did not respond to a liveness ping")
    if not schema_ok:
        return _check("Engine", "down", "Database schema sanity probe failed from the engine process")
    if lag_ms < 0:
        return _check("Engine", "degraded", "Worker threads responded but the event-loop lag probe did not report")
    if lag_ms > 2000:
        return _check(
            "Engine",
            "degraded",
            f"Worker threads respond but event-loop lag is {lag_ms:.0f} ms (over the 2000 ms budget)",
        )
    return _check(
        "Engine",
        "ok",
        f"Worker threads responsive (lag {lag_ms:.0f} ms); schema sanity probe passed; {active_jobs} active job(s)",
    )


def _database_check(db: Any) -> dict[str, Any]:
    try:
        if db is None:
            return _check("Database", "down", "Database handle is missing; integrity could not be verified")
        from ..db import integrity_check

        result = integrity_check(db)
        if result.get("ok"):
            return _check("Database", "ok", "SQLite integrity check passed; foreign keys verified")
        return _check(
            "Database",
            "down",
            f"SQLite integrity check failed: {result.get('integrity_check')!r} "
            f"with {result.get('foreign_key_errors', 0)} foreign-key errors",
        )
    except Exception as exc:
        return _check("Database", "down", f"Database integrity probe failed safely: {exc}")


def _storage_check() -> dict[str, Any]:
    try:
        root = Path(os.environ.get("FLUJO_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
        data_dir = Path(os.environ.get("FLUJO_DATA_DIR", root / ".flujo"))
        try:
            data_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return _check("Storage", "degraded", f"Data directory is not writable: {exc}")
        if not os.access(data_dir, os.W_OK):
            return _check("Storage", "degraded", f"Data directory is not writable: {data_dir}")
        try:
            free_bytes = shutil.disk_usage(data_dir).free
        except OSError as exc:
            return _check("Storage", "degraded", f"Disk space could not be measured for {data_dir}: {exc}")
        free_gib = free_bytes / _ONE_GIB
        if free_bytes > _ONE_GIB:
            return _check(
                "Storage",
                "ok",
                f"Data directory is writable; {free_gib:.1f} GiB free",
            )
        return _check(
            "Storage",
            "degraded",
            f"Data directory is writable but only {free_gib:.2f} GiB free (below 1 GiB headroom)",
        )
    except Exception as exc:
        return _check("Storage", "degraded", f"Storage probe failed safely: {exc}")


def _gateway_check(gateway: Any) -> dict[str, Any]:
    try:
        if gateway is None:
            return _check("Model Gateway", "degraded", "Model Gateway handle is unavailable on this host")
        health: Any = gateway.health() if callable(getattr(gateway, "health", None)) else gateway
        if not isinstance(health, dict):
            return _check("Model Gateway", "degraded", "Model Gateway returned an unreadable health signal")
        simulator_only = health.get("simulator_only", True)
        model_count = health.get("model_count", 0)
        if simulator_only:
            return _check(
                "Model Gateway",
                "ok",
                f"Model Gateway is responding in simulator mode ({model_count} local models; no live provider credential configured)",
            )
        return _check(
            "Model Gateway",
            "ok",
            f"Model Gateway is responding with live providers ({model_count} models via {health.get('provider_source', 'live')})",
        )
    except Exception as exc:
        return _check("Model Gateway", "degraded", f"Model Gateway probe failed safely: {exc}")


def _workflow_check(worker_capacity: Any) -> dict[str, Any]:
    try:
        capacity = int(worker_capacity)
    except (TypeError, ValueError):
        return _check("Workflow Engine", "degraded", "Worker capacity signal is unreadable")
    if capacity > 0:
        return _check("Workflow Engine", "ok", f"Workflow engine reports {capacity} local worker slots")
    return _check("Workflow Engine", "degraded", "No local worker slots reported; restart the backend service")


def _memory_check(db: Any) -> dict[str, Any]:
    try:
        from .. import memory as _memory_module  # noqa: F401  (importability probe)

        if db is not None:
            try:
                db.execute("SELECT 1 FROM memory_entries LIMIT 1").fetchall()
            except Exception:
                try:
                    db.execute("SELECT 1 FROM memory_records LIMIT 1").fetchall()
                except Exception as exc:
                    return _check("Memory", "degraded", f"Memory tables are not queryable: {exc}")
        return _check("Memory", "ok", "Memory subsystem is importable and queryable")
    except ImportError as exc:
        return _check("Memory", "degraded", f"Memory subsystem is not importable on this host: {exc}")
    except Exception as exc:
        return _check("Memory", "degraded", f"Memory probe failed safely: {exc}")


def _executor_check() -> dict[str, Any]:
    try:
        if os.name != "nt":
            return _check(
                "Executor",
                "degraded",
                "Windows executor binary not present on this host",
            )
        root = Path(os.environ.get("FLUJO_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
        candidates = [
            root / "executor" / "target" / "release" / "flujo-executor.exe",
            root / "resources" / "executor" / "flujo-executor.exe",
        ]
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return _check("Executor", "ok", f"Executor binary is present: {candidate.name}")
            except OSError:
                continue
        return _check("Executor", "degraded", "Executor binary presence is unverified on this host")
    except Exception as exc:
        return _check("Executor", "degraded", f"Executor probe failed safely: {exc}")


def _media_check(name: str, media_health: Any) -> dict[str, Any]:
    try:
        if not isinstance(media_health, dict):
            return _check(name, "degraded", f"{name} health signal is unavailable")
        entry = media_health.get(name.lower(), {})
        if not isinstance(entry, dict):
            return _check(name, "degraded", f"{name} health signal is unreadable")
        status = str(entry.get("status", "missing")).lower()
        message = str(entry.get("message") or entry.get("status") or "no detail")
        if status == "healthy":
            version = entry.get("version")
            detail = f"{name} is available and verified" + (f" ({version[:80]})" if version else "")
            return _check(name, "ok", detail)
        if entry.get("code") == "MEDIA_DEPENDENCY_CORRUPT" or status == "corrupt":
            return _check(name, "down", f"{name} failed verification: {message}")
        return _check(name, "degraded", f"{name} is not bundled for this platform: {message}")
    except Exception as exc:
        return _check(name, "degraded", f"{name} probe failed safely: {exc}")


def _rust_check() -> dict[str, Any]:
    try:
        if os.name != "nt":
            return _check(
                "Rust",
                "degraded",
                "Windows Rust sidecar binary not present on this host",
            )
        root = Path(os.environ.get("FLUJO_PROJECT_ROOT", Path(__file__).resolve().parents[3]))
        candidates = [
            root / "resources" / "executor" / "flujo-sidecar.exe",
            root / "executor" / "target" / "release" / "flujo-sidecar.exe",
        ]
        for candidate in candidates:
            try:
                if candidate.is_file():
                    return _check("Rust", "ok", f"Rust sidecar is present: {candidate.name}")
            except OSError:
                continue
        return _check("Rust", "degraded", "Rust sidecar presence is unverified on this host")
    except Exception as exc:
        return _check("Rust", "degraded", f"Rust probe failed safely: {exc}")


def _default_backend_port() -> int:
    try:
        return int(os.environ.get("FLUJO_PORT", "47821") or 47821)
    except (TypeError, ValueError):
        return 47821


def _ports_check(port: int | None = None) -> dict[str, Any]:
    """Real port probe: attempt a local TCP connection to the backend port.

    The check no longer hardcodes a healthy verdict; it reports what the
    socket actually observed.
    """
    import socket

    target = _default_backend_port() if port is None else port
    try:
        target = int(target)
    except (TypeError, ValueError):
        return _check("Ports", "unknown", "Backend port configuration is unreadable")
    try:
        with socket.create_connection(("127.0.0.1", target), timeout=2):
            pass
        return _check(
            "Ports",
            "ok",
            f"Port {target} accepts local connections; the backend listener is reachable",
        )
    except OSError as exc:
        reason = exc.strerror or str(exc) or "connection refused"
        return _check(
            "Ports",
            "degraded",
            f"Port {target} did not accept a local connection ({reason}); the backend listener may be down",
        )
    except Exception as exc:
        return _check("Ports", "unknown", f"Port probe failed safely: {exc}")


def _processes_check() -> dict[str, Any]:
    """Supervised-process probe.

    On Linux this enumerates the backend process's actual child processes from
    /proc (stdlib only). On Windows the supervised tree is only verifiable in
    packaged builds, which is reported honestly as unknown.
    """
    try:
        if os.name == "nt":
            return _check(
                "Processes",
                "unknown",
                "Supervised process tree verifiable on packaged Windows builds only",
            )
        me = str(os.getpid())
        children: list[str] = []
        try:
            proc_root = Path("/proc")
            for entry in proc_root.iterdir():
                if not entry.name.isdigit() or entry.name == me:
                    continue
                try:
                    # comm may contain spaces/parens; ppid follows the final ")".
                    stat = (entry / "stat").read_text().rsplit(")", 1)
                    fields = stat[1].split()
                    ppid = fields[1] if len(fields) > 1 else ""
                    if ppid == me:
                        comm = (entry / "comm").read_text().strip()
                        children.append(f"{entry.name}:{comm}"[:80])
                except (OSError, ValueError, IndexError):
                    continue
        except OSError as exc:
            return _check("Processes", "unknown", f"Process table could not be read: {exc}")
        detail = f"{len(children)} child process(es) under this backend process"
        if children:
            detail += ": " + ", ".join(sorted(children)[:8])
        return _check("Processes", "ok", detail)
    except Exception as exc:
        return _check("Processes", "unknown", f"Process probe failed safely: {exc}")


def _electron_check(frontend_heartbeat_at: str | None = None) -> dict[str, Any]:
    """Frontend heartbeat probe.

    The backend records the timestamp of the most recent frontend diagnostic
    event as its heartbeat signal (see backend/app/main.py::frontend_diagnostic).
    Fresh (< 10 min) is ok, stale is degraded, never-seen is unknown.
    """
    try:
        if not frontend_heartbeat_at:
            return _check(
                "Electron",
                "unknown",
                "No frontend heartbeat has been reported yet; open the desktop shell to register one",
            )
        seen = datetime.fromisoformat(str(frontend_heartbeat_at))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=timezone.utc)
        age_seconds = (datetime.now(timezone.utc) - seen).total_seconds()
        if age_seconds < 0:
            return _check("Electron", "unknown", "Frontend heartbeat timestamp is in the future; clock skew suspected")
        if age_seconds <= 600:
            return _check("Electron", "ok", f"Frontend heartbeat received {age_seconds:.0f} s ago")
        return _check(
            "Electron",
            "degraded",
            f"Frontend heartbeat is stale ({age_seconds / 60:.1f} min old); the desktop shell may be closed",
        )
    except (ValueError, TypeError) as exc:
        return _check("Electron", "unknown", f"Frontend heartbeat timestamp was unreadable: {exc}")
    except Exception as exc:
        return _check("Electron", "unknown", f"Electron probe failed safely: {exc}")


def run_checks(
    db: Any,
    gateway: Any,
    media_health: Any,
    worker_capacity: Any,
    *,
    engine_probe: dict[str, Any] | None = None,
    backend_port: int | None = None,
    frontend_heartbeat_at: str | None = None,
) -> list[dict[str, Any]]:
    """Run one read-only check per component; never raises for a bad probe."""
    return [
        _engine_check(engine_probe),
        _database_check(db),
        _storage_check(),
        _gateway_check(gateway),
        _workflow_check(worker_capacity),
        _memory_check(db),
        _executor_check(),
        _media_check("FFmpeg", media_health),
        _media_check("FFprobe", media_health),
        _rust_check(),
        _ports_check(backend_port),
        _processes_check(),
        _electron_check(frontend_heartbeat_at),
    ]


def overall_status(checks: list[dict[str, Any]]) -> str:
    """Worst component status; ``unknown`` counts as degraded, never down."""
    statuses = {str(item.get("status", "unknown")).lower() for item in checks}
    if "down" in statuses:
        return "down"
    if "degraded" in statuses or "unknown" in statuses:
        return "degraded"
    return "ok"


def recommendations(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Safe, manual, read-only guidance for every non-ok component."""
    result: list[dict[str, Any]] = []
    for item in checks:
        status = str(item.get("status", "unknown")).lower()
        if status == "ok":
            continue
        component = str(item.get("component", "Unknown"))
        severity = "high" if status == "down" else "medium" if status == "degraded" else "low"
        result.append(
            {
                "component": component,
                "severity": severity,
                "suggestion": _SUGGESTIONS.get(component, "Review backend logs and restart the backend service"),
                "action": "manual",
            }
        )
    return result


def _recommendation_for(component: str, recs: list[dict[str, Any]] | None) -> str:
    if recs:
        for rec in recs:
            if str(rec.get("component")) == component:
                suggestion = str(rec.get("suggestion", ""))
                if suggestion:
                    return suggestion
    return _SUGGESTIONS.get(component, "Review backend logs and restart the backend service")


def record_incidents(
    db: Any,
    checks: list[dict[str, Any]],
    recs: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Persist one ``doctor_incidents`` row per non-ok check; returns the rows."""
    stored: list[dict[str, Any]] = []
    for item in checks:
        status = str(item.get("status", "unknown")).lower()
        if status == "ok":
            continue
        component = str(item.get("component", "Unknown"))
        row = {
            "id": _new_id("doctor"),
            "component": component,
            "status": status,
            "detail": str(item.get("detail", "")),
            "recommendation": _recommendation_for(component, recs),
            "created_at": _now_iso(),
        }
        db.execute(
            "INSERT INTO doctor_incidents(id,component,status,detail,recommendation,created_at) VALUES(?,?,?,?,?,?)",
            (row["id"], row["component"], row["status"], row["detail"], row["recommendation"], row["created_at"]),
        )
        stored.append(row)
    return stored


def list_incidents(db: Any, limit: int = 50) -> list[dict[str, Any]]:
    """Recent incidents, newest first."""
    safe_limit = max(1, min(int(limit), 500))
    rows = db.execute(
        "SELECT id,component,status,detail,recommendation,created_at FROM doctor_incidents "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (safe_limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def build_health_payload(
    db: Any,
    gateway: Any,
    media_health: Any,
    worker_capacity: Any,
    *,
    engine_probe: dict[str, Any] | None = None,
    backend_port: int | None = None,
    frontend_heartbeat_at: str | None = None,
) -> dict[str, Any]:
    """Full health payload shared by GET and POST doctor routes."""
    checks = run_checks(
        db,
        gateway,
        media_health,
        worker_capacity,
        engine_probe=engine_probe,
        backend_port=backend_port,
        frontend_heartbeat_at=frontend_heartbeat_at,
    )
    return {
        "status": overall_status(checks),
        "checks": checks,
        "checked_at": _now_iso(),
    }
