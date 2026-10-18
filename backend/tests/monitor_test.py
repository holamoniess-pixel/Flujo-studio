import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.monitor import MonitorBlocked, MonitorController


class Events:
    def __init__(self):
        self.items = []

    def audit(self, **kwargs):
        self.items.append(kwargs)


def make_db() -> sqlite3.Connection:
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE monitor_state (id INTEGER PRIMARY KEY, status TEXT, halt_reason TEXT, halted_at TEXT, resumed_at TEXT, updated_at TEXT);
        CREATE TABLE monitor_limits (id INTEGER PRIMARY KEY, session_limit REAL, day_limit REAL, project_limit REAL, month_limit REAL, model_limit REAL, provider_limit REAL, spend_rate_limit REAL, requests_per_minute INTEGER, max_retries INTEGER, max_concurrent_jobs INTEGER, max_adaptation_evaluations INTEGER, provider_failure_limit INTEGER, slow_provider_ms INTEGER, consumed_session REAL, consumed_day REAL, consumed_project REAL, consumed_month REAL, adaptation_evaluations INTEGER, session_id TEXT, session_started_at TEXT, day_key TEXT, month_key TEXT, updated_at TEXT);
        CREATE TABLE monitor_scope_counters (scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, consumed REAL NOT NULL DEFAULT 0, request_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, adaptation_evaluations INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY(scope_type, scope_id));
        CREATE TABLE monitor_spend_ledger (event_id TEXT PRIMARY KEY, project_id TEXT, model_id TEXT, provider_id TEXT, job_id TEXT, session_id TEXT, operation TEXT NOT NULL, amount REAL NOT NULL, occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE monitor_admissions (admission_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, model_id TEXT, provider_id TEXT, job_id TEXT, operation TEXT NOT NULL, is_retry INTEGER NOT NULL DEFAULT 0, is_adaptation INTEGER NOT NULL DEFAULT 0, outcome TEXT, failure_class TEXT, duration_ms INTEGER, created_at TEXT NOT NULL, completed_at TEXT);
        CREATE TABLE monitor_alerts (id TEXT PRIMARY KEY, alert_key TEXT NOT NULL UNIQUE, code TEXT NOT NULL, severity TEXT NOT NULL, scope TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL, acknowledged_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE monitor_reconciliations (id TEXT PRIMARY KEY, status TEXT NOT NULL, checked_at TEXT NOT NULL, repaired INTEGER NOT NULL DEFAULT 0, details_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE jobs (status TEXT, updated_at TEXT);
        CREATE TABLE audit_events (category TEXT, timestamp TEXT, status TEXT);
        INSERT INTO monitor_state VALUES (1, 'RUNNING', NULL, NULL, NULL, '2026-08-25T00:00:00Z');
        INSERT INTO monitor_limits VALUES (1, 1, 10, 10, 10, 2, 3, 0, 60, 3, 2, 20, 3, 1000, 0, 0, 0, 0, 0, 'old-session', '2026-08-25T00:00:00Z', '2026-08-25', '2026-08', '2026-08-25T00:00:00Z');
        """
    )
    return db


def main() -> None:
    db = make_db()
    events = Events()
    monitor = MonitorController(db, events)

    first = monitor.authorize(project_id="project_northstar", model_id="model_a", provider_id="provider_a", operation="test")
    assert first["monitor_authority"] is True
    monitor.record_outcome(admission_id=first["admission_id"], outcome="COMPLETED", duration_ms=10)
    monitor.record_spend(project_id="project_northstar", model_id="model_a", provider_id="provider_a", amount=0.5, operation="test", event_id="spend-1")
    monitor.record_spend(project_id="project_northstar", model_id="model_a", provider_id="provider_a", amount=0.5, operation="test", event_id="spend-1")
    assert db.execute("SELECT COUNT(*) AS count FROM monitor_spend_ledger").fetchone()["count"] == 1
    assert monitor.limits()["consumed_day"] == 0.5

    try:
        monitor.record_spend(project_id="project_northstar", model_id="model_a", provider_id="provider_a", amount=0.6, operation="burst", event_id="spend-2")
    except MonitorBlocked as error:
        assert error.code == "MONITOR_SPEND_LIMIT"
    else:
        raise AssertionError("spend ceiling did not halt")
    assert monitor.state()["status"] == "HALTED"
    assert db.execute("SELECT COUNT(*) AS count FROM monitor_alerts WHERE code='MONITOR_HALTED'").fetchone()["count"] == 1

    try:
        monitor.authorize(project_id="project_northstar", operation="governor-override")
    except MonitorBlocked as error:
        assert error.code == "MONITOR_HALTED"
    else:
        raise AssertionError("Governor-shaped request bypassed Monitor halt")

    try:
        monitor.resume(actor="", human_confirmed=False)
    except MonitorBlocked as error:
        assert error.code == "HUMAN_RESUME_REQUIRED"
    else:
        raise AssertionError("unsafe resume was accepted")
    assert monitor.resume(actor="human-reviewer", human_confirmed=True)["status"] == "RUNNING"

    db.execute("UPDATE monitor_limits SET max_retries=1, max_adaptation_evaluations=1, provider_failure_limit=2, slow_provider_ms=10, spend_rate_limit=100 WHERE id=1")
    retry = monitor.authorize(project_id="project_retry", operation="retry", is_retry=True)
    monitor.record_outcome(admission_id=retry["admission_id"], outcome="FAILED", failure_class="provider", duration_ms=20)
    try:
        monitor.authorize(project_id="project_retry", operation="retry", is_retry=True)
    except MonitorBlocked as error:
        assert error.code == "MONITOR_RETRY_LIMIT"
    else:
        raise AssertionError("retry limit did not block")
    assert monitor.resume(actor="retry-reviewer", human_confirmed=True)["status"] == "RUNNING"

    db.execute("UPDATE monitor_limits SET max_retries=3, max_adaptation_evaluations=1 WHERE id=1")
    adaptation = monitor.authorize(project_id="project_adapt", operation="adapt", is_adaptation=True)
    monitor.record_outcome(admission_id=adaptation["admission_id"], outcome="COMPLETED", duration_ms=20)
    try:
        monitor.authorize(project_id="project_adapt", operation="adapt", is_adaptation=True)
    except MonitorBlocked as error:
        assert error.code == "MONITOR_ADAPTATION_LIMIT"
    else:
        raise AssertionError("adaptation limit did not block")

    db.execute("UPDATE monitor_limits SET provider_failure_limit=1 WHERE id=1")
    db.execute("INSERT INTO jobs(status, updated_at) VALUES('running', datetime('now'))")
    snapshot = monitor.dashboard()
    codes = {alert["code"] for alert in snapshot["alerts"]}
    assert snapshot["contract_version"] == "monitor.dashboard.v1"
    assert snapshot["independent"] is True
    assert snapshot["governor_override"] is False
    assert "MONITOR_PROVIDER_FAILURE_STORM" in codes
    assert "MONITOR_SLOW_PROVIDER" in codes
    assert snapshot["rate_of_spend"] >= 0
    assert snapshot["reconciliation"]["status"] in {"OK", "REPAIRED"}

    db.execute("UPDATE monitor_limits SET consumed_day=999 WHERE id=1")
    db.execute("UPDATE monitor_scope_counters SET consumed=999 WHERE scope_type='project' AND scope_id='project_northstar'")
    repaired = monitor.reconcile()
    assert repaired["repaired"] is True
    assert monitor.limits()["consumed_day"] == 1.1
    assert monitor._scope("project", "project_northstar")["consumed"] == 1.1

    second = MonitorController(db, events)
    second.halt("restart-check", actor="monitor")
    third = MonitorController(db, events)
    assert third.state()["status"] == "HALTED"
    assert any(item["operation"] == "halt" for item in events.items)
    print("monitor: passed; durable counters, idempotent spend, scope limits, alerts, reconciliation, restart persistence, human resume, and Governor override resistance verified")


def test_spend_dimension(scope_name: str, *, project_id: str = "project-dimension", model_id: str | None = None, provider_id: str | None = None) -> None:
    db = make_db()
    monitor = MonitorController(db, Events())
    db.execute("UPDATE monitor_limits SET session_limit=100, day_limit=100, project_limit=100, month_limit=100, model_limit=100, provider_limit=100, spend_rate_limit=100 WHERE id=1")
    db.execute(f"UPDATE monitor_limits SET {scope_name}_limit=1 WHERE id=1")
    monitor.record_spend(project_id=project_id, model_id=model_id, provider_id=provider_id, amount=0.6, operation=scope_name, event_id=f"{scope_name}-1")
    try:
        monitor.record_spend(project_id=project_id, model_id=model_id, provider_id=provider_id, amount=0.5, operation=scope_name, event_id=f"{scope_name}-2")
    except MonitorBlocked as error:
        assert error.code == "MONITOR_SPEND_LIMIT"
    else:
        raise AssertionError(f"{scope_name} spend limit did not halt")


def dimensions() -> None:
    test_spend_dimension("session")
    test_spend_dimension("day")
    test_spend_dimension("project", project_id="project-limit")
    test_spend_dimension("model", model_id="model-limit")
    test_spend_dimension("provider", provider_id="provider-limit")


def adversarial() -> None:
    db = make_db()
    events = Events()
    monitor = MonitorController(db, events)
    db.execute("UPDATE monitor_limits SET session_limit=100, day_limit=100, project_limit=100, month_limit=100, spend_rate_limit=0.5, provider_failure_limit=2, max_concurrent_jobs=1 WHERE id=1")
    try:
        monitor.record_spend(project_id="burst-project", amount=0.6, operation="burst", event_id="burst-1")
    except MonitorBlocked as error:
        assert error.code == "MONITOR_RATE_OF_SPEND_LIMIT"
    else:
        raise AssertionError("cost burst did not halt")
    assert monitor.state()["status"] == "HALTED"
    assert monitor.resume(actor="burst-reviewer", human_confirmed=True)["status"] == "RUNNING"

    db.execute("INSERT INTO jobs(status, updated_at) VALUES('running', datetime('now'))")
    try:
        monitor.authorize(project_id="parallel-project", operation="parallel")
    except MonitorBlocked as error:
        assert error.code == "MONITOR_CONCURRENCY_LIMIT"
    else:
        raise AssertionError("parallel-job explosion did not halt")
    assert monitor.resume(actor="parallel-reviewer", human_confirmed=True)["status"] == "RUNNING"
    db.execute("DELETE FROM jobs")

    for index in range(2):
        admission = monitor.authorize(project_id=f"provider-project-{index}", operation="provider")
        monitor.record_outcome(admission_id=admission["admission_id"], outcome="FAILED", failure_class="provider", duration_ms=2000)
    assert "MONITOR_PROVIDER_FAILURE_STORM" in {alert["code"] for alert in monitor.dashboard()["alerts"]}
    assert monitor.state()["status"] == "HALTED"
    db.execute("DELETE FROM jobs")
    monitor.resume(actor="storm-reviewer", human_confirmed=True)
    db.execute("INSERT INTO jobs(status, updated_at) VALUES('running', '2000-01-01T00:00:00+00:00')")
    reconciliation = monitor.reconcile()
    assert reconciliation["details"]["stale_jobs"] == 1
    assert monitor.state()["status"] == "HALTED"
    assert "MONITOR_JOB_LEAK" in {alert["code"] for alert in monitor.dashboard()["alerts"]}


if __name__ == "__main__":
    main()
    dimensions()
    adversarial()
