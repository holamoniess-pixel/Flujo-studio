from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .db import rows_to_dicts, transaction


class RepositoryError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


class RepositoryUnavailable(RepositoryError):
    pass


@runtime_checkable
class ProjectsRepository(Protocol):
    def get(self, project_id: str) -> dict[str, Any] | None: ...
    def list(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class JobsRepository(Protocol):
    def get(self, job_id: str) -> dict[str, Any] | None: ...
    def list(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]: ...
    def find_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None: ...
    def create_message_job(self, conversation_id: str, project_id: str, content: str, attachments: list[dict[str, Any]], idempotency_key: str, request_fingerprint: str, now: str) -> str: ...


@runtime_checkable
class EvidenceRepository(Protocol):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class ArtifactsRepository(Protocol):
    def get_with_lineage(self, artifact_id: str) -> dict[str, Any] | None: ...


@runtime_checkable
class ModelsRepository(Protocol):
    def list(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class CredentialsRepository(Protocol):
    def get(self, credential_id: str) -> dict[str, Any] | None: ...
    def list(self) -> list[dict[str, Any]]: ...


@runtime_checkable
class SkillsRepository(Protocol):
    def list(self, project_id: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class RecipesRepository(Protocol):
    def list(self, project_id: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class MemoryRepository(Protocol):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]: ...


@runtime_checkable
class IncidentsRepository(Protocol):
    def list(self, limit: int = 100) -> list[dict[str, Any]]: ...


@runtime_checkable
class UsageRepository(Protocol):
    def list_for_project(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]: ...


@runtime_checkable
class ReviewsRepository(Protocol):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]: ...


class _SQLiteRepository:
    def __init__(self, connection):
        self.connection = connection

    def _one(self, query: str, parameters: tuple[Any, ...]) -> dict[str, Any] | None:
        row = self.connection.execute(query, parameters).fetchone()
        return dict(row) if row else None

    def _many(self, query: str, parameters: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        return rows_to_dicts(self.connection.execute(query, parameters).fetchall())


class SQLiteProjectsRepository(_SQLiteRepository, ProjectsRepository):
    def get(self, project_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM projects WHERE id=?", (project_id,))

    def list(self) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM projects ORDER BY updated_at DESC")


class SQLiteJobsRepository(_SQLiteRepository, JobsRepository):
    def get(self, job_id: str) -> dict[str, Any] | None:
        return self._one("SELECT * FROM jobs WHERE id=?", (job_id,))

    def list(self, project_id: str, limit: int = 50) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        return self._many("SELECT * FROM jobs WHERE project_id=? ORDER BY updated_at DESC LIMIT ?", (project_id, safe_limit))

    def find_by_idempotency(self, idempotency_key: str) -> dict[str, Any] | None:
        return self._one("SELECT id,request_fingerprint,status,checkpoint,progress FROM jobs WHERE idempotency_key=?", (idempotency_key,))

    def create_message_job(self, conversation_id: str, project_id: str, content: str, attachments: list[dict[str, Any]], idempotency_key: str, request_fingerprint: str, now: str) -> str:
        import json
        from .observability.events import new_id
        job_id = new_id("job")
        with transaction(self.connection):
            self.connection.execute("INSERT INTO messages(id,conversation_id,role,content,created_at,metadata_json) VALUES(?,?,?,?,?,?)", (new_id("msg"), conversation_id, "user", content, now, json.dumps({"attachments": attachments}, ensure_ascii=False, separators=(",", ":"))))
            self.connection.execute("INSERT INTO jobs(id,project_id,workflow_name,status,idempotency_key,request_fingerprint,checkpoint,progress,estimated_cost,estimated_duration_seconds,interruption_mode,checkpoint_data_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (job_id, project_id, "CreativeBrief", "queued", idempotency_key, request_fingerprint, "queued", 0, 0.05, 30, "CANCELLABLE", json.dumps({"checkpoint": "queued", "progress": 0}, separators=(",", ":")), now, now))
        return job_id


class SQLiteEvidenceRepository(_SQLiteRepository, EvidenceRepository):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM evidence WHERE project_id=? ORDER BY created_at DESC", (project_id,))


class SQLiteArtifactsRepository(_SQLiteRepository, ArtifactsRepository):
    def get_with_lineage(self, artifact_id: str) -> dict[str, Any] | None:
        artifact = self._one("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if artifact is None:
            return None
        artifact["lineage"] = self._many("SELECT * FROM lineage WHERE artifact_id=? ORDER BY created_at", (artifact_id,))
        return artifact


class SQLiteModelsRepository(_SQLiteRepository, ModelsRepository):
    def list(self) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM models ORDER BY display_name")


class SQLiteCredentialsRepository(_SQLiteRepository, CredentialsRepository):
    def get(self, credential_id: str) -> dict[str, Any] | None:
        return self._one("SELECT id,provider,display_name,masked_value,scope,status,created_at,updated_at FROM credentials WHERE id=?", (credential_id,))

    def list(self) -> list[dict[str, Any]]:
        return self._many("SELECT id,provider,display_name,masked_value,scope,status,created_at,updated_at FROM credentials ORDER BY provider,display_name")


class SQLiteMemoryRepository(_SQLiteRepository, MemoryRepository):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM memory_entries WHERE project_id=? ORDER BY created_at DESC", (project_id,))


class SQLiteIncidentsRepository(_SQLiteRepository, IncidentsRepository):
    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        return self._many("SELECT * FROM error_events ORDER BY timestamp DESC LIMIT ?", (safe_limit,))


class SQLiteUsageRepository(_SQLiteRepository, UsageRepository):
    def list_for_project(self, project_id: str, limit: int = 100) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        return self._many("SELECT * FROM usage_events WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, safe_limit))


class SQLiteReviewsRepository(_SQLiteRepository, ReviewsRepository):
    def list_for_project(self, project_id: str) -> list[dict[str, Any]]:
        return self._many("SELECT * FROM promotion_reviews WHERE project_id=? ORDER BY created_at DESC", (project_id,))


@dataclass(frozen=True)
class UnsupportedDomainRepository:
    domain: str

    def list(self, project_id: str) -> list[dict[str, Any]]:
        raise RepositoryUnavailable("DOMAIN_REPOSITORY_UNAVAILABLE", f"The {self.domain} repository is not implemented in this local control-plane schema.")


@dataclass(frozen=True)
class SQLiteRepositoryBundle:
    projects: ProjectsRepository
    jobs: JobsRepository
    evidence: EvidenceRepository
    artifacts: ArtifactsRepository
    models: ModelsRepository
    credentials: CredentialsRepository
    skills: SkillsRepository
    recipes: RecipesRepository
    memory: MemoryRepository
    incidents: IncidentsRepository
    usage: UsageRepository
    reviews: ReviewsRepository

    @classmethod
    def from_connection(cls, connection) -> "SQLiteRepositoryBundle":
        return cls(
            projects=SQLiteProjectsRepository(connection),
            jobs=SQLiteJobsRepository(connection),
            evidence=SQLiteEvidenceRepository(connection),
            artifacts=SQLiteArtifactsRepository(connection),
            models=SQLiteModelsRepository(connection),
            credentials=SQLiteCredentialsRepository(connection),
            skills=UnsupportedDomainRepository("skills"),
            recipes=UnsupportedDomainRepository("recipes"),
            memory=SQLiteMemoryRepository(connection),
            incidents=SQLiteIncidentsRepository(connection),
            usage=SQLiteUsageRepository(connection),
            reviews=SQLiteReviewsRepository(connection),
        )
