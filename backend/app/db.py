from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

SCHEMA_VERSION = 14

SCHEMA = """
CREATE TABLE IF NOT EXISTS publish_prep_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_id TEXT NOT NULL, platform TEXT NOT NULL, status TEXT NOT NULL, steps_json TEXT NOT NULL DEFAULT '[]', bundle_path TEXT, guard_decision_id TEXT, error_code TEXT, error_message TEXT, receipt_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(artifact_id) REFERENCES artifacts(id), FOREIGN KEY(guard_decision_id) REFERENCES guard_decisions(id));
CREATE INDEX IF NOT EXISTS idx_publish_prep_project ON publish_prep_runs(project_id, created_at DESC);
CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS installation_state (id INTEGER PRIMARY KEY CHECK (id = 1), installation_id TEXT NOT NULL, first_seen_at TEXT NOT NULL, onboarding_completed INTEGER NOT NULL DEFAULT 0, onboarding_skipped INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, name TEXT NOT NULL, slug TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS conversations (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, campaign_id TEXT, title TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0, archived INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS messages (id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(conversation_id) REFERENCES conversations(id));
CREATE TABLE IF NOT EXISTS "references" (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, conversation_id TEXT, label TEXT NOT NULL, kind TEXT NOT NULL, role TEXT, path TEXT, sha256 TEXT, status TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, reference_id TEXT, claim TEXT NOT NULL, source_type TEXT NOT NULL, source_locator TEXT, model_id TEXT, model_version TEXT, confidence REAL NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS decisions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, conversation_id TEXT, status TEXT NOT NULL, decision TEXT NOT NULL, rationale TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS governor_advisors (id TEXT PRIMARY KEY, name TEXT NOT NULL, role TEXT NOT NULL, capabilities_json TEXT NOT NULL DEFAULT '[]', task_affinity REAL NOT NULL DEFAULT 0.5, cost_per_consult REAL NOT NULL DEFAULT 0.1, expected_latency_ms INTEGER NOT NULL DEFAULT 100, enabled INTEGER NOT NULL DEFAULT 1, total_consultations INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0, total_accuracy REAL NOT NULL DEFAULT 0, total_cost REAL NOT NULL DEFAULT 0, total_latency_ms REAL NOT NULL DEFAULT 0, total_affinity REAL NOT NULL DEFAULT 0, total_agreement REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS governor_decisions (id TEXT PRIMARY KEY, idempotency_key TEXT NOT NULL UNIQUE, task TEXT NOT NULL, status TEXT NOT NULL, interpretation_json TEXT NOT NULL DEFAULT '{}', subtasks_json TEXT NOT NULL DEFAULT '[]', selected_advisors_json TEXT NOT NULL DEFAULT '[]', opinions_json TEXT NOT NULL DEFAULT '[]', evidence_json TEXT NOT NULL DEFAULT '[]', fused_answer TEXT, conflict_json TEXT NOT NULL DEFAULT '{}', model_selection_json TEXT NOT NULL DEFAULT '{}', tool_selection_json TEXT NOT NULL DEFAULT '{}', execution_plan_json TEXT NOT NULL DEFAULT '{}', questions_json TEXT NOT NULL DEFAULT '{}', cost REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS governor_consultations (id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, advisor_id TEXT NOT NULL, status TEXT NOT NULL, answer TEXT NOT NULL DEFAULT '', confidence REAL NOT NULL DEFAULT 0, evidence_json TEXT NOT NULL DEFAULT '[]', cost REAL NOT NULL DEFAULT 0, latency_ms INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, error_code TEXT, created_at TEXT NOT NULL, FOREIGN KEY(decision_id) REFERENCES governor_decisions(id), FOREIGN KEY(advisor_id) REFERENCES governor_advisors(id));
CREATE TABLE IF NOT EXISTS governor_outcomes (id TEXT PRIMARY KEY, decision_id TEXT NOT NULL, success INTEGER NOT NULL, accuracy REAL NOT NULL, task_affinity REAL NOT NULL, agreement REAL NOT NULL, note TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, FOREIGN KEY(decision_id) REFERENCES governor_decisions(id));
CREATE TABLE IF NOT EXISTS adaptation_experiences (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT, scope TEXT NOT NULL, task TEXT NOT NULL, feedback TEXT NOT NULL, outcome TEXT NOT NULL DEFAULT '', model_id TEXT, provider_id TEXT, tool_name TEXT, recipe_name TEXT, evidence_ids_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS adaptation_feedback (id TEXT PRIMARY KEY, experience_id TEXT NOT NULL, outcome TEXT NOT NULL, quality REAL NOT NULL, attribution TEXT NOT NULL, evidence_ids_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, FOREIGN KEY(experience_id) REFERENCES adaptation_experiences(id));
CREATE TABLE IF NOT EXISTS adaptation_candidates (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT, scope TEXT NOT NULL, candidate_type TEXT NOT NULL, task_key TEXT NOT NULL, proposal_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL DEFAULT '[]', status TEXT NOT NULL, current_version INTEGER NOT NULL DEFAULT 0, source_experience_id TEXT, consent_json TEXT NOT NULL DEFAULT '{}', compiled_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(source_experience_id) REFERENCES adaptation_experiences(id));
CREATE TABLE IF NOT EXISTS adaptation_evaluations (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, status TEXT NOT NULL, score REAL NOT NULL, cases_json TEXT NOT NULL DEFAULT '[]', missing_kinds_json TEXT NOT NULL DEFAULT '[]', regressions_json TEXT NOT NULL DEFAULT '[]', reason TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(candidate_id) REFERENCES adaptation_candidates(id));
CREATE TABLE IF NOT EXISTS adaptation_versions (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, version INTEGER NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, reviewer_id TEXT NOT NULL, review_reason TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(candidate_id, version), FOREIGN KEY(candidate_id) REFERENCES adaptation_candidates(id));
CREATE TABLE IF NOT EXISTS adaptation_drift (id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, observed_score REAL NOT NULL, threshold REAL NOT NULL, state TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '[]', reviewer_id TEXT, created_at TEXT NOT NULL, FOREIGN KEY(candidate_id) REFERENCES adaptation_candidates(id));
CREATE TABLE IF NOT EXISTS adaptation_model_affinity (model_id TEXT NOT NULL, task_key TEXT NOT NULL, trials INTEGER NOT NULL DEFAULT 0, successes INTEGER NOT NULL DEFAULT 0, total_quality REAL NOT NULL DEFAULT 0, total_latency_ms REAL NOT NULL DEFAULT 0, total_cost REAL NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY(model_id, task_key));
CREATE TABLE IF NOT EXISTS genomes (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, patterns_json TEXT NOT NULL, evidence_ids_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS blueprints (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, genome_id TEXT, name TEXT NOT NULL, scenes_json TEXT NOT NULL, constraints_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS compile_runs (id TEXT PRIMARY KEY, blueprint_id TEXT NOT NULL, provider_neutral_plan_json TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(blueprint_id) REFERENCES blueprints(id));
CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workflow_name TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, request_fingerprint TEXT NOT NULL DEFAULT '', checkpoint TEXT NOT NULL, progress REAL NOT NULL DEFAULT 0, error_code TEXT, error_message TEXT, estimated_cost REAL NOT NULL DEFAULT 0, estimated_duration_seconds INTEGER NOT NULL DEFAULT 30, interruption_mode TEXT NOT NULL DEFAULT 'CANCELLABLE', checkpoint_data_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS permission_checks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation TEXT NOT NULL, requested_permissions_json TEXT NOT NULL, decision TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS tool_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, operation TEXT NOT NULL, arguments_json TEXT NOT NULL, status TEXT NOT NULL, exit_code INTEGER, stdout TEXT, stderr TEXT, files_json TEXT NOT NULL DEFAULT '[]', duration_ms INTEGER, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS qa_runs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, job_id TEXT, status TEXT NOT NULL, findings_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, kind TEXT NOT NULL, name TEXT NOT NULL, path TEXT, status TEXT NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS lineage (id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, parent_type TEXT NOT NULL, parent_id TEXT NOT NULL, relationship TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(artifact_id) REFERENCES artifacts(id));
CREATE TABLE IF NOT EXISTS provenance_nodes (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, node_type TEXT NOT NULL, source_ref TEXT NOT NULL, label TEXT NOT NULL DEFAULT '', content_hash TEXT NOT NULL, hash_algorithm TEXT NOT NULL DEFAULT 'sha256', hash_scope TEXT NOT NULL, hash_material_json TEXT, metadata_json TEXT NOT NULL DEFAULT '{}', reproduction_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, UNIQUE(project_id,node_type,source_ref,content_hash), FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS provenance_edges (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, child_node_id TEXT NOT NULL, parent_node_id TEXT NOT NULL, relationship TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, UNIQUE(project_id,child_node_id,parent_node_id,relationship), FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(child_node_id) REFERENCES provenance_nodes(id), FOREIGN KEY(parent_node_id) REFERENCES provenance_nodes(id));
CREATE TABLE IF NOT EXISTS provenance_exports (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, root_node_id TEXT NOT NULL, bundle_hash TEXT NOT NULL, bundle_json TEXT NOT NULL, verification_json TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(root_node_id) REFERENCES provenance_nodes(id));
CREATE TABLE IF NOT EXISTS audit_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL, message TEXT NOT NULL, trace_id TEXT, request_id TEXT, project_id TEXT, workflow_id TEXT, job_id TEXT, artifact_id TEXT, attempt_id TEXT, decision_id TEXT, route TEXT, operation TEXT, duration_ms INTEGER, status TEXT, input_summary TEXT, output_summary TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS error_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, severity TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL, stack TEXT, trace_id TEXT, request_id TEXT, project_id TEXT, workflow_id TEXT, job_id TEXT, route TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS providers (id TEXT PRIMARY KEY, name TEXT NOT NULL, masked_key TEXT, status TEXT NOT NULL, last_tested_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS credentials (id TEXT PRIMARY KEY, provider TEXT NOT NULL, display_name TEXT NOT NULL, masked_value TEXT NOT NULL, scope TEXT NOT NULL, status TEXT NOT NULL, blob_path TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS models (id TEXT PRIMARY KEY, provider_id TEXT NOT NULL, model_id TEXT NOT NULL, display_name TEXT NOT NULL, version TEXT, capabilities_json TEXT NOT NULL, modalities_json TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', FOREIGN KEY(provider_id) REFERENCES providers(id));
CREATE TABLE IF NOT EXISTS task_routes (task TEXT PRIMARY KEY, primary_model_id TEXT, secondary_model_id TEXT, fallback_model_id TEXT, mode TEXT NOT NULL DEFAULT 'Auto', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS usage_events (id TEXT PRIMARY KEY, project_id TEXT, model_id TEXT, task TEXT, requests INTEGER NOT NULL DEFAULT 1, input_tokens INTEGER NOT NULL DEFAULT 0, output_tokens INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, estimated_cost REAL NOT NULL DEFAULT 0, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS memory_entries (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source TEXT NOT NULL, scope TEXT NOT NULL, title TEXT NOT NULL, content TEXT NOT NULL, confidence REAL NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS memory_records (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT, conversation_id TEXT, domain TEXT NOT NULL, scope TEXT NOT NULL, task_key TEXT, source TEXT NOT NULL, title TEXT NOT NULL DEFAULT '', content TEXT NOT NULL, confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1), provenance_json TEXT NOT NULL DEFAULT '{}', permission_json TEXT NOT NULL DEFAULT '{}', content_hash TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, status TEXT NOT NULL DEFAULT 'ACTIVE', expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(project_id,user_id,domain,content_hash), FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS guard_decisions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, job_id TEXT, phase TEXT NOT NULL, status TEXT NOT NULL, checks_json TEXT NOT NULL, reason TEXT NOT NULL, policy_version TEXT NOT NULL DEFAULT 'guard.policy.v2', modalities_json TEXT NOT NULL DEFAULT '[]', classifier_states_json TEXT NOT NULL DEFAULT '{}', evidence_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS guard_reviews (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, job_id TEXT, artifact_id TEXT, decision_id TEXT, reason TEXT NOT NULL, evidence_json TEXT NOT NULL DEFAULT '{}', policy_version TEXT NOT NULL, decision TEXT NOT NULL DEFAULT 'PENDING', reviewer_id TEXT, reviewer_note TEXT, status TEXT NOT NULL DEFAULT 'PENDING_HUMAN', created_at TEXT NOT NULL, decided_at TEXT, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(decision_id) REFERENCES guard_decisions(id), FOREIGN KEY(artifact_id) REFERENCES artifacts(id));
CREATE TABLE IF NOT EXISTS guard_publish_checks (id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, project_id TEXT NOT NULL, decision_id TEXT, status TEXT NOT NULL, policy_version TEXT NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id), FOREIGN KEY(decision_id) REFERENCES guard_decisions(id));
CREATE TABLE IF NOT EXISTS consent_policies (id TEXT PRIMARY KEY, project_id TEXT NOT NULL UNIQUE, learning_scope TEXT NOT NULL DEFAULT 'PROJECT_ONLY', face_consent INTEGER NOT NULL DEFAULT 0, voice_consent INTEGER NOT NULL DEFAULT 0, identity_source TEXT NOT NULL DEFAULT 'UNSPECIFIED', consent_state TEXT NOT NULL DEFAULT 'PENDING', project_scope TEXT NOT NULL DEFAULT 'PROJECT_ONLY', likeness_consent INTEGER NOT NULL DEFAULT 0, consent_version TEXT NOT NULL DEFAULT 'consent.v1', retention_days INTEGER NOT NULL DEFAULT 30, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS promotion_reviews (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, candidate_id TEXT NOT NULL, candidate_type TEXT NOT NULL, evaluation_outcome TEXT NOT NULL, status TEXT NOT NULL, reviewer_id TEXT, decision_reason TEXT, rollback_target TEXT, created_at TEXT NOT NULL, decided_at TEXT, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS monitor_state (id INTEGER PRIMARY KEY CHECK(id=1), status TEXT NOT NULL DEFAULT 'RUNNING', halt_reason TEXT, halted_at TEXT, resumed_at TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monitor_limits (id INTEGER PRIMARY KEY CHECK(id=1), session_limit REAL NOT NULL DEFAULT 10, day_limit REAL NOT NULL DEFAULT 50, project_limit REAL NOT NULL DEFAULT 250, month_limit REAL NOT NULL DEFAULT 500, model_limit REAL NOT NULL DEFAULT 0, provider_limit REAL NOT NULL DEFAULT 0, spend_rate_limit REAL NOT NULL DEFAULT 0, requests_per_minute INTEGER NOT NULL DEFAULT 60, max_retries INTEGER NOT NULL DEFAULT 3, max_concurrent_jobs INTEGER NOT NULL DEFAULT 2, max_adaptation_evaluations INTEGER NOT NULL DEFAULT 20, provider_failure_limit INTEGER NOT NULL DEFAULT 5, slow_provider_ms INTEGER NOT NULL DEFAULT 30000, consumed_session REAL NOT NULL DEFAULT 0, consumed_day REAL NOT NULL DEFAULT 0, consumed_project REAL NOT NULL DEFAULT 0, consumed_month REAL NOT NULL DEFAULT 0, adaptation_evaluations INTEGER NOT NULL DEFAULT 0, session_id TEXT NOT NULL DEFAULT 'bootstrap', session_started_at TEXT, day_key TEXT NOT NULL DEFAULT '', month_key TEXT NOT NULL DEFAULT '', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tool_lifecycle (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, name TEXT NOT NULL, version TEXT NOT NULL, state TEXT NOT NULL, scope TEXT NOT NULL, security_status TEXT NOT NULL DEFAULT 'UNREVIEWED', last_used_at TEXT, last_audited_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, FOREIGN KEY(project_id) REFERENCES projects(id));
CREATE TABLE IF NOT EXISTS slo_policies (id TEXT PRIMARY KEY, job_type TEXT NOT NULL UNIQUE, target_seconds INTEGER NOT NULL, alert_after_seconds INTEGER NOT NULL, enabled INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS monitor_scope_counters (scope_type TEXT NOT NULL, scope_id TEXT NOT NULL, consumed REAL NOT NULL DEFAULT 0, request_count INTEGER NOT NULL DEFAULT 0, retry_count INTEGER NOT NULL DEFAULT 0, adaptation_evaluations INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, PRIMARY KEY(scope_type, scope_id));
CREATE TABLE IF NOT EXISTS monitor_spend_ledger (event_id TEXT PRIMARY KEY, project_id TEXT, model_id TEXT, provider_id TEXT, job_id TEXT, session_id TEXT, operation TEXT NOT NULL, amount REAL NOT NULL CHECK(amount >= 0), occurred_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS monitor_admissions (admission_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, model_id TEXT, provider_id TEXT, job_id TEXT, operation TEXT NOT NULL, is_retry INTEGER NOT NULL DEFAULT 0, is_adaptation INTEGER NOT NULL DEFAULT 0, outcome TEXT, failure_class TEXT, duration_ms INTEGER, created_at TEXT NOT NULL, completed_at TEXT);
CREATE TABLE IF NOT EXISTS monitor_alerts (id TEXT PRIMARY KEY, alert_key TEXT NOT NULL UNIQUE, code TEXT NOT NULL, severity TEXT NOT NULL, scope TEXT NOT NULL, message TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'OPEN', created_at TEXT NOT NULL, acknowledged_at TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS monitor_reconciliations (id TEXT PRIMARY KEY, status TEXT NOT NULL, checked_at TEXT NOT NULL, repaired INTEGER NOT NULL DEFAULT 0, details_json TEXT NOT NULL DEFAULT '{}');
CREATE TABLE IF NOT EXISTS doctor_incidents (id TEXT PRIMARY KEY, component TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL DEFAULT '', recommendation TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_doctor_incidents_created ON doctor_incidents(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_monitor_spend_time ON monitor_spend_ledger(occurred_at);
CREATE INDEX IF NOT EXISTS idx_monitor_spend_project ON monitor_spend_ledger(project_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_monitor_spend_model ON monitor_spend_ledger(model_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_monitor_spend_provider ON monitor_spend_ledger(provider_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_monitor_alert_status ON monitor_alerts(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guard_decisions_project_created ON guard_decisions(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guard_reviews_status ON guard_reviews(project_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_guard_publish_artifact ON guard_publish_checks(artifact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_governor_decisions_created ON governor_decisions(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_governor_consultations_decision ON governor_consultations(decision_id, created_at);
CREATE INDEX IF NOT EXISTS idx_governor_outcomes_decision ON governor_outcomes(decision_id, created_at);
CREATE INDEX IF NOT EXISTS idx_adaptation_experiences_project ON adaptation_experiences(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_adaptation_candidates_project ON adaptation_candidates(project_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_adaptation_evaluations_candidate ON adaptation_evaluations(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_adaptation_versions_candidate ON adaptation_versions(candidate_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_adaptation_drift_candidate ON adaptation_drift(candidate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_error_timestamp ON error_events(timestamp);
CREATE INDEX IF NOT EXISTS idx_memory_records_project_scope ON memory_records(project_id, scope, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_records_project_user ON memory_records(project_id, user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_records_domain ON memory_records(project_id, domain, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_records_expiry ON memory_records(status, expires_at);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id, created_at);
CREATE INDEX IF NOT EXISTS idx_lineage_artifact ON lineage(artifact_id);
CREATE INDEX IF NOT EXISTS idx_provenance_nodes_project_type ON provenance_nodes(project_id, node_type, created_at);
CREATE INDEX IF NOT EXISTS idx_provenance_edges_child ON provenance_edges(project_id, child_node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provenance_edges_parent ON provenance_edges(project_id, parent_node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_provenance_exports_root ON provenance_exports(project_id, root_node_id, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_project_updated ON jobs(project_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status_updated ON jobs(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_references_project_created ON "references"(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_evidence_project_created ON evidence(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_artifacts_project_created ON artifacts(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_credentials_provider ON credentials(provider, status);
CREATE INDEX IF NOT EXISTS idx_models_provider_active ON models(provider_id, active);
CREATE INDEX IF NOT EXISTS idx_usage_project_created ON usage_events(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_route_operation ON audit_events(route, operation, timestamp DESC);
"""


class ThreadLocalConnection:
    def __init__(self, path: Path):
        self.path = path
        self.local = threading.local()
        self.connections: list[sqlite3.Connection] = []
        self.connections_lock = threading.Lock()

    def _connection(self) -> sqlite3.Connection:
        connection = getattr(self.local, 'connection', None)
        if connection is None:
            connection = sqlite3.connect(self.path, timeout=30, isolation_level=None, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=30000')
            with self.connections_lock:
                self.connections.append(connection)
            self.local.connection = connection
        return connection

    def execute(self, *args, **kwargs):
        return self._connection().execute(*args, **kwargs)

    def executescript(self, *args, **kwargs):
        return self._connection().executescript(*args, **kwargs)

    def close(self) -> None:
        with self.connections_lock:
            connections = list(self.connections)
            self.connections.clear()
        for connection in connections:
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                pass

    def __getattr__(self, name):
        return getattr(self._connection(), name)


def connect(path: Path) -> ThreadLocalConnection:
    path.parent.mkdir(parents=True, exist_ok=True)
    return ThreadLocalConnection(path)


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    connection.execute("BEGIN IMMEDIATE")
    try:
        yield connection
        connection.execute("COMMIT")
    except Exception:
        connection.execute("ROLLBACK")
        raise


def migrate(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA)
    columns = {row["name"] for row in connection.execute("PRAGMA table_info(jobs)").fetchall()}
    audit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(audit_events)").fetchall()}
    error_columns = {row["name"] for row in connection.execute("PRAGMA table_info(error_events)").fetchall()}
    with transaction(connection):
        if "request_fingerprint" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN request_fingerprint TEXT NOT NULL DEFAULT ''")
        if "estimated_cost" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN estimated_cost REAL NOT NULL DEFAULT 0")
        if "estimated_duration_seconds" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN estimated_duration_seconds INTEGER NOT NULL DEFAULT 30")
        if "interruption_mode" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN interruption_mode TEXT NOT NULL DEFAULT 'CANCELLABLE'")
        if "checkpoint_data_json" not in columns:
            connection.execute("ALTER TABLE jobs ADD COLUMN checkpoint_data_json TEXT NOT NULL DEFAULT '{}'")
        guard_decision_columns = {row["name"] for row in connection.execute("PRAGMA table_info(guard_decisions)").fetchall()}
        for name, definition in {
            "policy_version": "TEXT NOT NULL DEFAULT 'guard.policy.v2'",
            "modalities_json": "TEXT NOT NULL DEFAULT '[]'",
            "classifier_states_json": "TEXT NOT NULL DEFAULT '{}'",
            "evidence_json": "TEXT NOT NULL DEFAULT '{}'",
        }.items():
            if name not in guard_decision_columns:
                connection.execute(f"ALTER TABLE guard_decisions ADD COLUMN {name} {definition}")
        review_columns = {row["name"] for row in connection.execute("PRAGMA table_info(guard_reviews)").fetchall()}
        if "artifact_id" not in review_columns:
            connection.execute("ALTER TABLE guard_reviews ADD COLUMN artifact_id TEXT")
        consent_columns = {row["name"] for row in connection.execute("PRAGMA table_info(consent_policies)").fetchall()}
        for name, definition in {
            "identity_source": "TEXT NOT NULL DEFAULT 'UNSPECIFIED'",
            "consent_state": "TEXT NOT NULL DEFAULT 'PENDING'",
            "project_scope": "TEXT NOT NULL DEFAULT 'PROJECT_ONLY'",
            "likeness_consent": "INTEGER NOT NULL DEFAULT 0",
            "consent_version": "TEXT NOT NULL DEFAULT 'consent.v1'",
        }.items():
            if name not in consent_columns:
                connection.execute(f"ALTER TABLE consent_policies ADD COLUMN {name} {definition}")
        monitor_limit_columns = {row["name"] for row in connection.execute("PRAGMA table_info(monitor_limits)").fetchall()}
        for name, definition in {
            "model_limit": "REAL NOT NULL DEFAULT 0",
            "provider_limit": "REAL NOT NULL DEFAULT 0",
            "spend_rate_limit": "REAL NOT NULL DEFAULT 0",
            "provider_failure_limit": "INTEGER NOT NULL DEFAULT 5",
            "slow_provider_ms": "INTEGER NOT NULL DEFAULT 30000",
            "session_id": "TEXT NOT NULL DEFAULT 'bootstrap'",
            "session_started_at": "TEXT",
            "day_key": "TEXT NOT NULL DEFAULT ''",
            "month_key": "TEXT NOT NULL DEFAULT ''",
        }.items():
            if name not in monitor_limit_columns:
                connection.execute(f"ALTER TABLE monitor_limits ADD COLUMN {name} {definition}")
        spend_columns = {row["name"] for row in connection.execute("PRAGMA table_info(monitor_spend_ledger)").fetchall()}
        if "session_id" not in spend_columns:
            connection.execute("ALTER TABLE monitor_spend_ledger ADD COLUMN session_id TEXT")
        admission_columns = {row["name"] for row in connection.execute("PRAGMA table_info(monitor_admissions)").fetchall()}
        for name, definition in {
            "job_id": "TEXT",
            "outcome": "TEXT",
            "failure_class": "TEXT",
            "duration_ms": "INTEGER",
            "completed_at": "TEXT",
        }.items():
            if name not in admission_columns:
                connection.execute(f"ALTER TABLE monitor_admissions ADD COLUMN {name} {definition}")
        if "request_id" not in audit_columns:
            connection.execute("ALTER TABLE audit_events ADD COLUMN request_id TEXT")
        if "request_id" not in error_columns:
            connection.execute("ALTER TABLE error_events ADD COLUMN request_id TEXT")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_audit_request ON audit_events(request_id, timestamp DESC)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_errors_request ON error_events(request_id, timestamp DESC)")
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        connection.execute("INSERT OR IGNORE INTO monitor_state(id, status, updated_at) VALUES(1, 'RUNNING', ?)", (now_iso,))
        connection.execute("INSERT OR IGNORE INTO monitor_limits(id, session_id, session_started_at, day_key, month_key, updated_at) VALUES(1, ?, ?, ?, ?, ?)", (f"session-{now_iso}", now_iso, now.date().isoformat(), now.strftime('%Y-%m'), now_iso))
        connection.execute("INSERT OR REPLACE INTO schema_meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))


def integrity_check(connection: sqlite3.Connection) -> dict[str, Any]:
    result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
    return {"integrity_check": result, "foreign_key_errors": len(foreign_keys), "ok": result == "ok" and not foreign_keys}


def backup_database(source_path: Path, backup_path: Path) -> None:
    source_path = Path(source_path)
    backup_path = Path(backup_path)
    if not source_path.exists():
        raise FileNotFoundError(source_path)
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = backup_path.with_suffix(backup_path.suffix + ".tmp")
    temporary_path.unlink(missing_ok=True)
    source = sqlite3.connect(source_path, timeout=30)
    target = sqlite3.connect(temporary_path, timeout=30)
    try:
        source.execute("PRAGMA query_only=ON")
        source.backup(target)
        target.commit()
        check = integrity_check(target)
        if not check["ok"]:
            raise sqlite3.DatabaseError(f"Backup integrity check failed: {check}")
    finally:
        target.close()
        source.close()
    temporary_path.replace(backup_path)


def restore_database(backup_path: Path, target_path: Path) -> None:
    backup_path = Path(backup_path)
    target_path = Path(target_path)
    if not backup_path.exists():
        raise FileNotFoundError(backup_path)
    source = sqlite3.connect(backup_path, timeout=30)
    temporary_path = target_path.with_suffix(target_path.suffix + ".restore.tmp")
    temporary_path.unlink(missing_ok=True)
    target = sqlite3.connect(temporary_path, timeout=30)
    try:
        source.execute("PRAGMA query_only=ON")
        source_check = integrity_check(source)
        if not source_check["ok"]:
            raise sqlite3.DatabaseError(f"Restore source integrity check failed: {source_check}")
        source.backup(target)
        target.commit()
        target_check = integrity_check(target)
        if not target_check["ok"]:
            raise sqlite3.DatabaseError(f"Restore target integrity check failed: {target_check}")
    finally:
        target.close()
        source.close()
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path.replace(target_path)


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row else None


def rows_to_dicts(rows: list[sqlite3.Row]) -> list[dict[str, Any]]:
    return [dict(row) for row in rows]


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def load_json(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback
