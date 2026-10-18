from __future__ import annotations

import hashlib
import json
import math
import uuid
from dataclasses import dataclass
from typing import Any

from ..db import transaction
from ..observability.events import now_iso


@dataclass(frozen=True)
class MonitorBlocked(Exception):
    code: str
    message: str


class MonitorController:
    """Durable, independent operational authority.

    Monitor decisions are persisted in SQLite and do not consult Governor state.
    Governor may request work, but it cannot clear a Monitor halt or bypass an
    admission decision.
    """

    DASHBOARD_CONTRACT_VERSION = "monitor.dashboard.v1"
    ACTIVE_JOB_STATUSES = ("queued", "running", "cancelling")
    LEAK_AFTER_SECONDS = 900

    def __init__(self, db: Any, events: Any):
        self.db = db
        self.events = events
        self.session_id = f"session-{uuid.uuid4().hex}"
        self._start_session()

    def _start_session(self) -> None:
        row = self.db.execute("SELECT id FROM monitor_limits WHERE id=1").fetchone()
        if row is None:
            return
        now = now_iso()
        self.db.execute(
            "UPDATE monitor_limits SET session_id=?, session_started_at=?, consumed_session=0, updated_at=? WHERE id=1",
            (self.session_id, now, now),
        )

    def state(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM monitor_state WHERE id=1").fetchone()
        return dict(row) if row else {"id": 1, "status": "RUNNING", "halt_reason": None}

    def limits(self) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM monitor_limits WHERE id=1").fetchone()
        return dict(row) if row else {}

    def _active_jobs(self) -> int:
        placeholders = ",".join("?" for _ in self.ACTIVE_JOB_STATUSES)
        return int(
            self.db.execute(
                f"SELECT COUNT(*) AS count FROM jobs WHERE status IN ({placeholders})",
                self.ACTIVE_JOB_STATUSES,
            ).fetchone()["count"]
        )

    def _scope(self, scope_type: str, scope_id: str) -> dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM monitor_scope_counters WHERE scope_type=? AND scope_id=?",
            (scope_type, scope_id),
        ).fetchone()
        return dict(row) if row else {"scope_type": scope_type, "scope_id": scope_id, "consumed": 0.0, "request_count": 0, "retry_count": 0, "adaptation_evaluations": 0}

    def _upsert_scope(self, scope_type: str, scope_id: str, *, consumed: float = 0.0, request_count: int = 0, retry_count: int = 0, adaptation_evaluations: int = 0) -> None:
        now = now_iso()
        self.db.execute(
            """INSERT INTO monitor_scope_counters(scope_type,scope_id,consumed,request_count,retry_count,adaptation_evaluations,updated_at)
            VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(scope_type,scope_id) DO UPDATE SET
            consumed=consumed+excluded.consumed,
            request_count=request_count+excluded.request_count,
            retry_count=retry_count+excluded.retry_count,
            adaptation_evaluations=adaptation_evaluations+excluded.adaptation_evaluations,
            updated_at=excluded.updated_at""",
            (scope_type, scope_id, consumed, request_count, retry_count, adaptation_evaluations, now),
        )

    def _alert(self, *, code: str, severity: str, scope: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        alert_key = hashlib.sha256(f"{code}|{scope}".encode()).hexdigest()
        now = now_iso()
        self.db.execute(
            "INSERT OR IGNORE INTO monitor_alerts(id,alert_key,code,severity,scope,message,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)",
            (f"alert-{uuid.uuid4().hex}", alert_key, code, severity, scope, message, "OPEN", now, json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))),
        )

    def _audit(self, *, operation: str, status: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        self.events.audit(category="monitor", message=message, route="monitor", operation=operation, status=status, metadata=metadata or {})

    def _roll_windows(self) -> None:
        limits = self.limits()
        if not limits:
            return
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        updates: list[str] = []
        values: list[Any] = []
        if limits.get("day_key") != now.date().isoformat():
            updates.extend(["consumed_day=0", "day_key=?"])
            values.append(now.date().isoformat())
        if limits.get("month_key") != now.strftime("%Y-%m"):
            updates.extend(["consumed_month=0", "month_key=?"])
            values.append(now.strftime("%Y-%m"))
        if updates:
            values.append(now_iso())
            self.db.execute(f"UPDATE monitor_limits SET {','.join(updates)}, updated_at=? WHERE id=1", tuple(values))

    def _recent_spend(self, seconds: int = 60) -> float:
        row = self.db.execute(
            "SELECT COALESCE(SUM(amount),0) AS amount FROM monitor_spend_ledger WHERE julianday(occurred_at) >= julianday('now', ?)",
            (f"-{seconds} seconds",),
        ).fetchone()
        return float(row["amount"] or 0)

    def _recent_failures(self) -> int:
        audit_row = self.db.execute(
            "SELECT COUNT(*) AS count FROM audit_events WHERE category='model-gateway' AND status='error' AND julianday(timestamp) >= julianday('now','-1 minute')"
        ).fetchone()
        admission_row = self.db.execute(
            "SELECT COUNT(*) AS count FROM monitor_admissions WHERE outcome='FAILED' AND julianday(created_at) >= julianday('now','-1 minute')"
        ).fetchone()
        return int(audit_row["count"] or 0) + int(admission_row["count"] or 0)

    def reconcile(self) -> dict[str, Any]:
        self._roll_windows()
        limits = self.limits()
        repaired = False
        details: dict[str, Any] = {}
        if limits:
            session_sum = self.db.execute("SELECT COALESCE(SUM(amount),0) AS amount FROM monitor_spend_ledger WHERE session_id=?", (limits.get("session_id", self.session_id),)).fetchone()["amount"]
            day_sum = self.db.execute("SELECT COALESCE(SUM(amount),0) AS amount FROM monitor_spend_ledger WHERE julianday(occurred_at) >= julianday('now','start of day')").fetchone()["amount"]
            month_sum = self.db.execute("SELECT COALESCE(SUM(amount),0) AS amount FROM monitor_spend_ledger WHERE strftime('%Y-%m', occurred_at)=strftime('%Y-%m','now')").fetchone()["amount"]
            current = self.limits()
            adaptation_sum = int(self.db.execute("SELECT COALESCE(SUM(is_adaptation),0) AS count FROM monitor_admissions").fetchone()["count"] or 0)
            if abs(float(current.get("consumed_session", 0)) - float(session_sum)) > 1e-9 or abs(float(current.get("consumed_day", 0)) - float(day_sum)) > 1e-9 or abs(float(current.get("consumed_month", 0)) - float(month_sum)) > 1e-9 or int(current.get("adaptation_evaluations", 0)) != adaptation_sum:
                self.db.execute("UPDATE monitor_limits SET consumed_session=?, consumed_day=?, consumed_month=?, adaptation_evaluations=?, updated_at=? WHERE id=1", (session_sum, day_sum, month_sum, adaptation_sum, now_iso()))
                repaired = True
            details.update({"session_spend": float(session_sum), "day_spend": float(day_sum), "month_spend": float(month_sum), "adaptation_evaluations": adaptation_sum})

        # Scope counters are projections; rebuild their durable values from the
        # spend ledger and admission records so a partial write cannot silently
        # change a limit decision.
        for scope_type, column in (("project", "project_id"), ("model", "model_id"), ("provider", "provider_id")):
            ids = {row["scope_id"] for row in self.db.execute("SELECT scope_id FROM monitor_scope_counters WHERE scope_type=?", (scope_type,)).fetchall()}
            ids.update(row[column] for row in self.db.execute(f"SELECT DISTINCT {column} FROM monitor_spend_ledger WHERE {column} IS NOT NULL").fetchall())
            if scope_type == "project":
                ids.update(row[column] for row in self.db.execute(f"SELECT DISTINCT {column} FROM monitor_admissions WHERE {column} IS NOT NULL").fetchall())
            for scope_id in ids:
                spend = self.db.execute(f"SELECT COALESCE(SUM(amount),0) AS amount FROM monitor_spend_ledger WHERE {column}=?", (scope_id,)).fetchone()["amount"]
                request_count = int(self.db.execute(f"SELECT COUNT(*) AS count FROM monitor_admissions WHERE {column}=?", (scope_id,)).fetchone()["count"] or 0)
                retry_count = int(self.db.execute(f"SELECT COALESCE(SUM(is_retry),0) AS count FROM monitor_admissions WHERE {column}=?", (scope_id,)).fetchone()["count"] or 0) if scope_type == "project" else 0
                previous = self._scope(scope_type, scope_id)
                if abs(float(previous.get("consumed", 0)) - float(spend)) > 1e-9 or int(previous.get("request_count", 0)) != request_count or int(previous.get("retry_count", 0)) != retry_count:
                    self.db.execute("INSERT INTO monitor_scope_counters(scope_type,scope_id,consumed,request_count,retry_count,adaptation_evaluations,updated_at) VALUES(?,?,?,?,?,?,?) ON CONFLICT(scope_type,scope_id) DO UPDATE SET consumed=excluded.consumed,request_count=excluded.request_count,retry_count=excluded.retry_count,updated_at=excluded.updated_at", (scope_type, scope_id, spend, request_count, retry_count, 0, now_iso()))
                    repaired = True
        active_jobs = self._active_jobs()
        details["active_jobs"] = active_jobs
        stale_jobs = int(self.db.execute("SELECT COUNT(*) AS count FROM jobs WHERE status IN ('queued','running','cancelling') AND julianday(updated_at) < julianday('now', '-15 minutes')").fetchone()["count"])
        details["stale_jobs"] = stale_jobs
        if stale_jobs:
            self._alert(code="MONITOR_JOB_LEAK", severity="critical", scope="jobs", message="Active jobs exceeded the Monitor heartbeat reconciliation window.", metadata={"stale_jobs": stale_jobs})
            self.halt("Monitor detected active jobs without a recent heartbeat.", actor="monitor")
        checked = now_iso()
        record_id = f"reconcile-{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO monitor_reconciliations(id,status,checked_at,repaired,details_json) VALUES(?,?,?,?,?)", (record_id, "REPAIRED" if repaired else "OK", checked, int(repaired), json.dumps(details, separators=(",", ":"))))
        return {"id": record_id, "status": "REPAIRED" if repaired else "OK", "checked_at": checked, "repaired": repaired, "details": details}

    def _computed_alerts(self, limits: dict[str, Any], active_jobs: int) -> list[dict[str, Any]]:
        alerts: list[dict[str, Any]] = []
        for name in ("session", "day", "project", "month"):
            consumed = float(limits.get(f"consumed_{name}", 0))
            ceiling = float(limits.get(f"{name}_limit", 0))
            if ceiling > 0 and consumed >= ceiling * 0.8:
                alerts.append({"code": "MONITOR_SPEND_APPROACHING_LIMIT", "severity": "warning" if consumed < ceiling else "critical", "scope": name, "consumed": consumed, "limit": ceiling})
        rate = self._recent_spend()
        rate_limit = float(limits.get("spend_rate_limit", 0) or 0)
        if rate_limit > 0 and rate >= rate_limit * 0.8:
            alerts.append({"code": "MONITOR_RATE_OF_SPEND_APPROACHING_LIMIT", "severity": "warning" if rate < rate_limit else "critical", "scope": "minute", "rate": rate, "limit": rate_limit})
        max_concurrent = int(limits.get("max_concurrent_jobs", 0) or 0)
        if max_concurrent > 0 and active_jobs >= max_concurrent:
            alerts.append({"code": "MONITOR_CONCURRENCY_AT_LIMIT", "severity": "warning", "active_jobs": active_jobs, "limit": max_concurrent})
        failure_limit = int(limits.get("provider_failure_limit", 0) or 0)
        failures = self._recent_failures()
        if failure_limit > 0 and failures >= failure_limit:
            alerts.append({"code": "MONITOR_PROVIDER_FAILURE_STORM", "severity": "critical", "failures": failures, "limit": failure_limit})
        slow_limit = int(limits.get("slow_provider_ms", 0) or 0)
        if slow_limit > 0:
            slow = int(self.db.execute("SELECT COUNT(*) AS count FROM monitor_admissions WHERE duration_ms>? AND julianday(created_at) >= julianday('now','-1 minute')", (slow_limit,)).fetchone()["count"] or 0)
            if slow:
                alerts.append({"code": "MONITOR_SLOW_PROVIDER", "severity": "warning", "slow_requests": slow, "limit_ms": slow_limit})
        return alerts

    def snapshot(self) -> dict[str, Any]:
        reconciliation = self.reconcile()
        state = self.state()
        limits = self.limits()
        active_jobs = self._active_jobs()
        persisted = [dict(row) for row in self.db.execute("SELECT * FROM monitor_alerts WHERE status='OPEN' ORDER BY created_at DESC LIMIT 50").fetchall()]
        alerts = self._computed_alerts(limits, active_jobs)
        return {
            "contract_version": self.DASHBOARD_CONTRACT_VERSION,
            "independent": True,
            "human_resume_required": True,
            "governor_override": False,
            "state": state,
            "limits": limits,
            "counters": {scope: self._scope(scope, "global") for scope in ("model", "provider", "project")},
            "active_jobs": active_jobs,
            "rate_of_spend": self._recent_spend(),
            "provider_failures_last_minute": self._recent_failures(),
            "alerts": persisted + alerts,
            "reconciliation": reconciliation,
            "reconciled_at": reconciliation["checked_at"],
        }

    def dashboard(self) -> dict[str, Any]:
        return self.snapshot()

    def authorize(self, *, project_id: str, operation: str, model_id: str | None = None, provider_id: str | None = None, is_retry: bool = False, is_adaptation: bool = False, job_id: str | None = None) -> dict[str, Any]:
        self._roll_windows()
        state = self.state()
        if state.get("status") == "HALTED":
            raise MonitorBlocked("MONITOR_HALTED", "Flujo Monitor has halted new work; a human must manually resume the system.")
        limits = self.limits()
        active = self._active_jobs()
        max_concurrent = int(limits.get("max_concurrent_jobs", 2) or 2)
        if max_concurrent > 0 and active >= max_concurrent:
            self.halt(f"Maximum concurrent jobs exceeded while authorizing {operation}.", actor="monitor")
            raise MonitorBlocked("MONITOR_CONCURRENCY_LIMIT", "Flujo Monitor halted work because the concurrent-job ceiling was reached.")
        recent = int(self.db.execute("SELECT COUNT(*) AS count FROM monitor_admissions WHERE julianday(created_at) >= julianday('now','-1 minute')").fetchone()["count"])
        request_limit = int(limits.get("requests_per_minute", 60) or 60)
        if recent >= request_limit:
            self.halt(f"Request-rate ceiling exceeded while authorizing {operation}.", actor="monitor")
            raise MonitorBlocked("MONITOR_RATE_LIMIT", "Flujo Monitor halted work because the request-rate ceiling was reached.")
        if is_retry:
            retries = int(self._scope("project", project_id).get("retry_count", 0))
            if retries >= int(limits.get("max_retries", 3) or 3):
                self.halt(f"Retry ceiling exceeded for project {project_id}.", actor="monitor")
                raise MonitorBlocked("MONITOR_RETRY_LIMIT", "Flujo Monitor halted work because the retry ceiling was reached.")
        if is_adaptation:
            evaluations = int(limits.get("adaptation_evaluations", 0) or 0)
            if evaluations >= int(limits.get("max_adaptation_evaluations", 20) or 20):
                self.halt(f"Adaptation evaluation ceiling exceeded for {operation}.", actor="monitor")
                raise MonitorBlocked("MONITOR_ADAPTATION_LIMIT", "Flujo Monitor halted work because the adaptation ceiling was reached.")
        for scope_type, scope_id, ceiling in (("project", project_id, limits.get("project_limit", 0)), ("model", model_id, limits.get("model_limit", 0)), ("provider", provider_id, limits.get("provider_limit", 0))):
            if scope_id and float(ceiling or 0) > 0 and float(self._scope(scope_type, scope_id).get("consumed", 0)) >= float(ceiling):
                self.halt(f"{scope_type.capitalize()} spend ceiling reached for {scope_id}.", actor="monitor")
                raise MonitorBlocked("MONITOR_SCOPE_LIMIT", f"Flujo Monitor halted work because the {scope_type} limit was reached.")
        admission_id = f"admission-{uuid.uuid4().hex}"
        now = now_iso()
        self.db.execute("INSERT INTO monitor_admissions(admission_id,project_id,model_id,provider_id,job_id,operation,is_retry,is_adaptation,outcome,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)", (admission_id, project_id, model_id, provider_id, job_id, operation, int(is_retry), int(is_adaptation), "AUTHORIZED", now))
        self._upsert_scope("project", project_id, request_count=1)
        if model_id:
            self._upsert_scope("model", model_id, request_count=1)
        if provider_id:
            self._upsert_scope("provider", provider_id, request_count=1)
        if is_retry:
            self._upsert_scope("project", project_id, retry_count=1)
        if is_adaptation:
            self.db.execute("UPDATE monitor_limits SET adaptation_evaluations=adaptation_evaluations+1, updated_at=? WHERE id=1", (now,))
        self._audit(operation="authorize", status="AUTHORIZED", message="Monitor authorized work independently", metadata={"admission_id": admission_id, "project_id": project_id, "operation": operation, "is_retry": is_retry, "is_adaptation": is_adaptation})
        return {"admission_id": admission_id, "status": "AUTHORIZED", "monitor_authority": True}

    def record_outcome(self, *, admission_id: str, outcome: str, duration_ms: int | None = None, failure_class: str | None = None) -> dict[str, Any]:
        if outcome not in {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT"}:
            raise MonitorBlocked("MONITOR_OUTCOME_INVALID", "Monitor outcome is not recognized.")
        now = now_iso()
        updated = self.db.execute("UPDATE monitor_admissions SET outcome=?, failure_class=?, duration_ms=?, completed_at=? WHERE admission_id=? AND outcome='AUTHORIZED'", (outcome, failure_class, duration_ms, now, admission_id)).rowcount
        if not updated:
            raise MonitorBlocked("MONITOR_ADMISSION_NOT_ACTIVE", "The Monitor admission is missing or already finalized.")
        if outcome in {"FAILED", "TIMEOUT"} and failure_class == "provider":
            self._audit(operation="provider-failure", status="error", message="Monitor recorded provider failure", metadata={"admission_id": admission_id, "failure_class": failure_class})
            failure_limit = int(self.limits().get("provider_failure_limit", 0) or 0)
            failures = self._recent_failures()
            if failure_limit > 0 and failures >= failure_limit:
                self._alert(code="MONITOR_PROVIDER_FAILURE_STORM", severity="critical", scope="provider", message="Provider failures exceeded the Monitor storm threshold; new work is halted.", metadata={"failures": failures, "limit": failure_limit})
                self.halt("Provider failure storm exceeded the Monitor threshold.", actor="monitor")
        return {"admission_id": admission_id, "outcome": outcome, "duration_ms": duration_ms, "failure_class": failure_class, "recorded_at": now}

    def record_spend(self, *, project_id: str, amount: float, operation: str, event_id: str | None = None, model_id: str | None = None, provider_id: str | None = None, job_id: str | None = None, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if not math.isfinite(amount) or amount < 0:
            raise MonitorBlocked("MONITOR_SPEND_INVALID", "Spend amount must be a finite non-negative number.")
        event_id = event_id or f"spend-{uuid.uuid4().hex}"
        now = now_iso()
        limits = self.limits()
        with transaction(self.db):
            inserted = self.db.execute("INSERT OR IGNORE INTO monitor_spend_ledger(event_id,project_id,model_id,provider_id,job_id,session_id,operation,amount,occurred_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?)", (event_id, project_id, model_id, provider_id, job_id, limits.get("session_id", self.session_id), operation, amount, now, json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":")))).rowcount
            if inserted:
                self.db.execute("UPDATE monitor_limits SET consumed_session=consumed_session+?, consumed_day=consumed_day+?, consumed_project=consumed_project+?, consumed_month=consumed_month+?, updated_at=? WHERE id=1", (amount, amount, amount, amount, now))
                self._upsert_scope("project", project_id, consumed=amount)
                if model_id:
                    self._upsert_scope("model", model_id, consumed=amount)
                if provider_id:
                    self._upsert_scope("provider", provider_id, consumed=amount)
            updated = self.limits()
        if not inserted:
            return updated
        ceilings = [("session", updated.get("consumed_session", 0), updated.get("session_limit", 0)), ("day", updated.get("consumed_day", 0), updated.get("day_limit", 0)), ("project", self._scope("project", project_id).get("consumed", 0), updated.get("project_limit", 0)), ("month", updated.get("consumed_month", 0), updated.get("month_limit", 0))]
        if model_id:
            ceilings.append(("model", self._scope("model", model_id).get("consumed", 0), updated.get("model_limit", 0)))
        if provider_id:
            ceilings.append(("provider", self._scope("provider", provider_id).get("consumed", 0), updated.get("provider_limit", 0)))
        rate_limit = float(updated.get("spend_rate_limit", 0) or 0)
        recent_rate = self._recent_spend()
        if rate_limit > 0 and recent_rate > rate_limit:
            self._alert(code="MONITOR_RATE_OF_SPEND_LIMIT", severity="critical", scope="minute", message="Rate of spend exceeded the Monitor ceiling.", metadata={"rate": recent_rate, "limit": rate_limit})
            self.halt(f"Rate-of-spend ceiling exceeded during {operation}.", actor="monitor")
            raise MonitorBlocked("MONITOR_RATE_OF_SPEND_LIMIT", "Flujo Monitor halted work because the spend-rate ceiling was exceeded.")
        for name, consumed, limit in ceilings:
            if float(limit or 0) > 0 and float(consumed) > float(limit):
                self._alert(code="MONITOR_SPEND_LIMIT", severity="critical", scope=name, message=f"{name.capitalize()} spend ceiling exceeded.", metadata={"consumed": consumed, "limit": limit})
                self.halt(f"{name.capitalize()} spend ceiling exceeded during {operation}.", actor="monitor")
                raise MonitorBlocked("MONITOR_SPEND_LIMIT", "Flujo Monitor halted work because a hard spend ceiling was exceeded.")
        return self.limits()

    def halt(self, reason: str, actor: str) -> dict[str, Any]:
        now = now_iso()
        self.db.execute("UPDATE monitor_state SET status='HALTED', halt_reason=?, halted_at=?, updated_at=? WHERE id=1", (reason, reason and now, now))
        self._alert(code="MONITOR_HALTED", severity="critical", scope="global", message="New work is halted until a named human resumes Monitor.", metadata={"reason": reason, "actor": actor, "human_required": True})
        self._audit(operation="halt", status="HALTED", message="Independent Monitor halt engaged", metadata={"reason": reason, "actor": actor, "human_required": True, "governor_override": False})
        return self.state()

    def resume(self, *, actor: str, human_confirmed: bool) -> dict[str, Any]:
        if not human_confirmed or not actor.strip():
            raise MonitorBlocked("HUMAN_RESUME_REQUIRED", "Only a named human confirmation can resume a Monitor halt.")
        now = now_iso()
        self.db.execute("UPDATE monitor_state SET status='RUNNING', halt_reason=NULL, resumed_at=?, updated_at=? WHERE id=1", (now, now))
        self.db.execute("UPDATE monitor_alerts SET status='ACKNOWLEDGED', acknowledged_at=? WHERE status='OPEN' AND code='MONITOR_HALTED'", (now,))
        self._audit(operation="resume", status="RUNNING", message="Independent Monitor halt manually resumed", metadata={"actor": actor, "human_confirmed": True, "governor_override": False})
        return self.state()
