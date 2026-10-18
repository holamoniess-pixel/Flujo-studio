from __future__ import annotations

import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import SCHEMA_VERSION, backup_database, connect, integrity_check, migrate, restore_database, transaction
from backend.app.repositories import (
    ArtifactsRepository,
    CredentialsRepository,
    EvidenceRepository,
    IncidentsRepository,
    JobsRepository,
    MemoryRepository,
    ModelsRepository,
    ProjectsRepository,
    RecipesRepository,
    ReviewsRepository,
    SkillsRepository,
    SQLiteRepositoryBundle,
    UsageRepository,
)


def test_sqlite_pragmas_transactions_and_concurrency() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-db-") as directory_name:
        path = Path(directory_name) / "runtime" / "flujo.sqlite3"
        db = connect(path)
        migrate(db)
        assert db.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        try:
            with transaction(db):
                db.execute("INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES(?,?,?,?,?)", ("rollback", "rollback", "rollback", "now", "now"))
                raise RuntimeError("rollback fixture")
        except RuntimeError:
            pass
        assert db.execute("SELECT 1 FROM projects WHERE id='rollback'").fetchone() is None

        def write_project(index: int) -> None:
            with transaction(db):
                db.execute("INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES(?,?,?,?,?)", (f"project_{index}", f"Project {index}", f"project-{index}", "now", "now"))

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write_project, range(24)))
        assert db.execute("SELECT count(*) FROM projects WHERE id LIKE 'project_%'").fetchone()[0] == 24
        assert integrity_check(db)["ok"] is True
        db.close()


def test_migration_adds_new_columns_to_legacy_tables() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-migrate-") as directory_name:
        path = Path(directory_name) / "legacy.sqlite3"
        connection = sqlite3.connect(path)
        connection.executescript("""
        CREATE TABLE jobs (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, workflow_name TEXT NOT NULL, status TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE, checkpoint TEXT NOT NULL, progress REAL NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE audit_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, severity TEXT NOT NULL, category TEXT NOT NULL, message TEXT NOT NULL, trace_id TEXT, project_id TEXT, workflow_id TEXT, job_id TEXT, artifact_id TEXT, attempt_id TEXT, decision_id TEXT, route TEXT, operation TEXT, duration_ms INTEGER, status TEXT, input_summary TEXT, output_summary TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
        CREATE TABLE error_events (id TEXT PRIMARY KEY, timestamp TEXT NOT NULL, severity TEXT NOT NULL, source TEXT NOT NULL, message TEXT NOT NULL, stack TEXT, trace_id TEXT, project_id TEXT, workflow_id TEXT, job_id TEXT, route TEXT, metadata_json TEXT NOT NULL DEFAULT '{}');
        """)
        connection.commit()
        connection.close()
        db = connect(path)
        migrate(db)
        job_columns = {row["name"] for row in db.execute("PRAGMA table_info(jobs)").fetchall()}
        audit_columns = {row["name"] for row in db.execute("PRAGMA table_info(audit_events)").fetchall()}
        error_columns = {row["name"] for row in db.execute("PRAGMA table_info(error_events)").fetchall()}
        assert {"request_fingerprint", "estimated_cost", "checkpoint_data_json"}.issubset(job_columns)
        assert "request_id" in audit_columns and "request_id" in error_columns
        assert db.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == str(SCHEMA_VERSION)
        db.close()


def test_backup_restore_and_corruption_detection() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-backup-") as directory_name:
        root = Path(directory_name)
        source_path = root / "source.sqlite3"
        backup_path = root / "backups" / "source.sqlite3"
        db = connect(source_path)
        migrate(db)
        db.execute("INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES(?,?,?,?,?)", ("project_backup", "Before", "backup", "now", "now"))
        db.close()
        backup_database(source_path, backup_path)
        restored = sqlite3.connect(backup_path)
        assert restored.execute("SELECT name FROM projects WHERE id='project_backup'").fetchone()[0] == "Before"
        restored.close()
        mutated = sqlite3.connect(source_path)
        mutated.execute("UPDATE projects SET name='After' WHERE id='project_backup'")
        mutated.commit()
        mutated.close()
        restore_database(backup_path, source_path)
        restored_source = sqlite3.connect(source_path)
        assert restored_source.execute("SELECT name FROM projects WHERE id='project_backup'").fetchone()[0] == "Before"
        restored_source.close()
        corrupt = root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        bad = sqlite3.connect(corrupt)
        try:
            try:
                integrity_check(bad)
            except sqlite3.DatabaseError:
                detected = True
            else:
                detected = False
        finally:
            bad.close()
        assert detected is True


def test_repository_bundle_exposes_requested_stable_interfaces() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-repository-") as directory_name:
        db = connect(Path(directory_name) / "db.sqlite3")
        migrate(db)
        bundle = SQLiteRepositoryBundle.from_connection(db)
        assert isinstance(bundle.projects, ProjectsRepository)
        assert isinstance(bundle.jobs, JobsRepository)
        assert isinstance(bundle.evidence, EvidenceRepository)
        assert isinstance(bundle.artifacts, ArtifactsRepository)
        assert isinstance(bundle.models, ModelsRepository)
        assert isinstance(bundle.credentials, CredentialsRepository)
        assert isinstance(bundle.skills, SkillsRepository)
        assert isinstance(bundle.recipes, RecipesRepository)
        assert isinstance(bundle.memory, MemoryRepository)
        assert isinstance(bundle.incidents, IncidentsRepository)
        assert isinstance(bundle.usage, UsageRepository)
        assert isinstance(bundle.reviews, ReviewsRepository)
        assert bundle.projects.get("project_northstar") is None
        db.close()


if __name__ == "__main__":
    test_sqlite_pragmas_transactions_and_concurrency()
    test_migration_adds_new_columns_to_legacy_tables()
    test_backup_restore_and_corruption_detection()
    test_repository_bundle_exposes_requested_stable_interfaces()
    print("db_integrity_tests_passed=4")
