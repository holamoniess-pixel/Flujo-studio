from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import connect, migrate
from backend.app.doctor import (
    COMPONENTS,
    list_incidents,
    overall_status,
    recommendations,
    record_incidents,
    run_checks,
)
from backend.app.doctor.checks import _electron_check, _engine_check, _ports_check, _processes_check

REQUIRED_COMPONENTS = [
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

BANNED_VERBS = ("delete", "remove", "wipe", "kill", "terminate", "uninstall")


class FakeGateway:
    def health(self) -> dict:
        return {
            "status": "healthy",
            "provider_count": 1,
            "providers": ["simulator"],
            "model_count": 2,
            "capabilities": ["text_generation"],
            "diversity": {},
            "simulator_only": True,
            "provider_source": "simulator",
        }


def fake_media() -> dict:
    return {
        "status": "missing",
        "ffmpeg": {
            "status": "missing",
            "code": "MEDIA_DEPENDENCY_MISSING",
            "message": "ffmpeg is not bundled for this platform.",
        },
        "ffprobe": {
            "status": "missing",
            "code": "MEDIA_DEPENDENCY_MISSING",
            "message": "ffprobe is not bundled for this platform.",
        },
    }


def fresh_db() -> tuple:
    directory = tempfile.TemporaryDirectory(prefix="flujo-doctor-")
    path = Path(directory.name) / "runtime" / "flujo.sqlite3"
    db = connect(path)
    migrate(db)
    return directory, db


def test_component_matrix() -> None:
    assert COMPONENTS == REQUIRED_COMPONENTS
    _directory, db = fresh_db()
    try:
        # Bind a real local listener so the Ports probe has something to find.
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        live_port = listener.getsockname()[1]
        try:
            engine_probe = {"worker_threads_ok": True, "event_loop_lag_ms": 12.5, "schema_ok": True, "active_jobs": 0}
            checks = run_checks(db, FakeGateway(), fake_media(), 2, engine_probe=engine_probe, backend_port=live_port)
            assert [item["component"] for item in checks] == REQUIRED_COMPONENTS
            for item in checks:
                assert set(item.keys()) >= {"component", "status", "detail", "checked_at"}
                assert item["status"] in {"ok", "degraded", "down", "unknown"}
                assert isinstance(item["detail"], str) and item["detail"]
                assert isinstance(item["checked_at"], str) and item["checked_at"]
            by_component = {item["component"]: item for item in checks}
            assert by_component["Engine"]["status"] == "ok"
            assert "lag" in by_component["Engine"]["detail"]
            assert by_component["Database"]["status"] == "ok"
            assert by_component["Workflow Engine"]["status"] == "ok"
            assert by_component["Memory"]["status"] == "ok"
            assert by_component["Model Gateway"]["status"] == "ok"
            assert "simulator" in by_component["Model Gateway"]["detail"].lower()
            assert by_component["FFmpeg"]["status"] == "degraded"
            assert by_component["FFprobe"]["status"] == "degraded"
            assert by_component["Ports"]["status"] == "ok"
            assert str(live_port) in by_component["Ports"]["detail"]
            if os.name == "nt":
                assert by_component["Processes"]["status"] == "unknown"
                assert "packaged Windows builds only" in by_component["Processes"]["detail"]
            else:
                assert by_component["Processes"]["status"] == "ok"
                assert "child process" in by_component["Processes"]["detail"]
            assert by_component["Electron"]["status"] == "unknown"
            assert "frontend heartbeat" in by_component["Electron"]["detail"]
            if os.name != "nt":
                assert by_component["Executor"]["status"] == "degraded"
                assert "not present on this host" in by_component["Executor"]["detail"]
                assert by_component["Rust"]["status"] == "degraded"
                assert "not present on this host" in by_component["Rust"]["detail"]
        finally:
            listener.close()
    finally:
        db.close()
        _directory.cleanup()


def test_forced_database_failure_is_down_not_exception() -> None:
    # Missing database handle must surface as down, never raise.
    checks = run_checks(None, FakeGateway(), fake_media(), 2)
    database = next(item for item in checks if item["component"] == "Database")
    assert database["status"] == "down"

    class BrokenDb:
        def execute(self, *args, **kwargs):
            raise sqlite3.DatabaseError("no such file or directory")

    checks = run_checks(BrokenDb(), FakeGateway(), fake_media(), 2)
    database = next(item for item in checks if item["component"] == "Database")
    assert database["status"] == "down"

    # A corrupt integrity signal is also down, not an exception.
    class CorruptDb:
        def execute(self, *args, **kwargs):
            class Row:
                def __getitem__(self, index):
                    return " FAILED " if index == 0 else 0

            class Cursor:
                def fetchone(self):
                    return Row()

                def fetchall(self):
                    return [Row()]

            return Cursor()

    checks = run_checks(CorruptDb(), FakeGateway(), fake_media(), 2)
    database = next(item for item in checks if item["component"] == "Database")
    assert database["status"] == "down"


def test_recommendations_are_safe_and_manual() -> None:
    _directory, db = fresh_db()
    try:
        checks = run_checks(db, FakeGateway(), fake_media(), 2)
        recs = recommendations(checks)
        non_ok = [item for item in checks if item["status"] != "ok"]
        assert len(recs) == len(non_ok) and non_ok
        for rec in recs:
            assert set(rec.keys()) >= {"component", "severity", "suggestion", "action"}
            assert rec["action"] == "manual"
            lowered = rec["suggestion"].lower()
            assert "automatic" not in lowered
            for verb in BANNED_VERBS:
                assert verb not in lowered, f"destructive verb {verb!r} in {rec['component']!r}"
        # Fully healthy matrix yields no recommendations.
        healthy = [
            {"component": name, "status": "ok", "detail": "fine", "checked_at": "now"}
            for name in REQUIRED_COMPONENTS
        ]
        assert recommendations(healthy) == []
    finally:
        db.close()
        _directory.cleanup()


def test_overall_status_unknown_is_not_down() -> None:
    assert overall_status([]) == "ok"
    healthy = [
        {"component": name, "status": "ok", "detail": "", "checked_at": ""}
        for name in REQUIRED_COMPONENTS
    ]
    assert overall_status(healthy) == "ok"
    unknowns = [dict(item, status="unknown") for item in healthy]
    assert overall_status(unknowns) == "degraded"
    degraded = [dict(item, status="degraded") if item["component"] == "Storage" else item for item in healthy]
    assert overall_status(degraded) == "degraded"
    down = [dict(item, status="down") if item["component"] == "Database" else item for item in healthy]
    assert overall_status(down) == "down"


def test_incident_recording_newest_first() -> None:
    _directory, db = fresh_db()
    try:
        checks = run_checks(db, FakeGateway(), fake_media(), 2)
        stored = record_incidents(db, checks, recommendations(checks))
        non_ok = [item for item in checks if item["status"] != "ok"]
        assert len(stored) == len(non_ok) and stored
        for row in stored:
            assert set(row.keys()) >= {"id", "component", "status", "detail", "recommendation", "created_at"}
        listed = list_incidents(db)
        assert len(listed) == len(stored)
        assert {row["id"] for row in listed} == {row["id"] for row in stored}
        created = [row["created_at"] for row in listed]
        assert created == sorted(created, reverse=True)
        # Explicit newest-first proof with fixed timestamps.
        db.execute(
            "INSERT INTO doctor_incidents(id,component,status,detail,recommendation,created_at) VALUES(?,?,?,?,?,?)",
            ("doctor_old", "Storage", "degraded", "old", "Free disk space", "2026-01-01T00:00:00+00:00"),
        )
        db.execute(
            "INSERT INTO doctor_incidents(id,component,status,detail,recommendation,created_at) VALUES(?,?,?,?,?,?)",
            ("doctor_new", "Storage", "degraded", "new", "Free disk space", "2026-06-01T00:00:00+00:00"),
        )
        limited = list_incidents(db, limit=500)
        ids = [row["id"] for row in limited]
        assert ids.index("doctor_new") < ids.index("doctor_old")
    finally:
        db.close()
        _directory.cleanup()


def test_engine_probes_are_real() -> None:
    # No probe -> unknown, never self-attested ok.
    check = _engine_check()
    assert check["status"] == "unknown"
    assert "self-attestation" in check["detail"]
    # Dead workers -> down.
    check = _engine_check({"worker_threads_ok": False, "event_loop_lag_ms": 1.0, "schema_ok": True, "active_jobs": 0})
    assert check["status"] == "down"
    # Failed schema sanity -> down.
    check = _engine_check({"worker_threads_ok": True, "event_loop_lag_ms": 1.0, "schema_ok": False, "active_jobs": 0})
    assert check["status"] == "down"
    # High event-loop lag -> degraded.
    check = _engine_check({"worker_threads_ok": True, "event_loop_lag_ms": 2500.0, "schema_ok": True, "active_jobs": 1})
    assert check["status"] == "degraded"
    assert "2500" in check["detail"]
    # Healthy probe -> ok with measured signals.
    check = _engine_check({"worker_threads_ok": True, "event_loop_lag_ms": 3.0, "schema_ok": True, "active_jobs": 2})
    assert check["status"] == "ok"
    assert "3 ms" in check["detail"] and "2 active job(s)" in check["detail"]


def test_ports_probe_is_real() -> None:
    # Closed port -> degraded, never hardcoded ok.
    closed = _ports_check(1)
    assert closed["status"] == "degraded"
    assert "1" in closed["detail"]
    # Open listener -> ok naming the probed port.
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    try:
        live_port = listener.getsockname()[1]
        check = _ports_check(live_port)
        assert check["status"] == "ok"
        assert str(live_port) in check["detail"]
    finally:
        listener.close()


def test_electron_heartbeat_states() -> None:
    check = _electron_check()
    assert check["status"] == "unknown"
    assert "No frontend heartbeat" in check["detail"]
    fresh = datetime.now(timezone.utc).isoformat()
    check = _electron_check(fresh)
    assert check["status"] == "ok"
    assert "heartbeat received" in check["detail"]
    stale = (datetime.now(timezone.utc) - timedelta(minutes=30)).isoformat()
    check = _electron_check(stale)
    assert check["status"] == "degraded"
    assert "stale" in check["detail"]
    check = _electron_check("not-a-timestamp")
    assert check["status"] == "unknown"


def test_processes_probe_lists_real_children() -> None:
    check = _processes_check()
    if os.name == "nt":
        assert check["status"] == "unknown"
    else:
        assert check["status"] == "ok"
        assert "child process" in check["detail"]


def main() -> None:
    test_component_matrix()
    test_engine_probes_are_real()
    test_ports_probe_is_real()
    test_electron_heartbeat_states()
    test_processes_probe_lists_real_children()
    test_forced_database_failure_is_down_not_exception()
    test_recommendations_are_safe_and_manual()
    test_overall_status_unknown_is_not_down()
    test_incident_recording_newest_first()
    print("doctor: passed")


if __name__ == "__main__":
    main()
