from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

MEMORY_CONTRACT_VERSION = "memory.record.v1"
TRANSFER_VERSION = "memory.export.v1"
DOMAINS = {"FLUJO", "PROJECT", "CONVERSATION", "USER_PREFERENCE", "OBSIDIAN", "REFERENCE", "SKILL", "DECISION"}
SCOPES = {"TASK", "PROJECT", "CONVERSATION", "USER", "GLOBAL"}
SOURCES = {"USER", "SYSTEM", "REFERENCE", "OBSIDIAN", "SKILL", "DECISION", "MODEL", "TOOL", "IMPORT"}


class MemoryBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class MemoryInput:
    project_id: str
    domain: str
    scope: str
    content: str
    source: str
    confidence: float
    provenance: dict[str, Any]
    permission: dict[str, Any]
    title: str = ""
    user_id: str | None = None
    conversation_id: str | None = None
    task_key: str | None = None
    expires_at: str | None = None


class MemoryController:
    def __init__(self, db: Any, events: Any | None = None) -> None:
        self.db = db
        self.events = events

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _audit(self, operation: str, status: str, message: str, project_id: str | None = None, memory_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        if self.events is not None:
            self.events.audit(category="memory", message=message, project_id=project_id, decision_id=memory_id, route="memory", operation=operation, status=status, metadata=metadata or {})

    def _json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)

    def _global_enabled(self, permission: dict[str, Any]) -> bool:
        return bool(permission.get("global_learning", False)) and str(permission.get("consent_state", "")).upper() == "GRANTED"

    def _validate_input(self, item: MemoryInput) -> tuple[str, str]:
        domain = item.domain.upper()
        scope = item.scope.upper()
        source = item.source.upper()
        if domain not in DOMAINS:
            raise MemoryBlocked("INVALID_MEMORY_DOMAIN", "Memory domain is not supported.")
        if scope not in SCOPES:
            raise MemoryBlocked("INVALID_MEMORY_SCOPE", "Memory scope is not supported.")
        if source not in SOURCES:
            raise MemoryBlocked("INVALID_MEMORY_SOURCE", "Memory source is not supported.")
        if not item.project_id.strip() or not item.content.strip():
            raise MemoryBlocked("MEMORY_CONTENT_REQUIRED", "Memory project and content are required.")
        if not 0.0 <= float(item.confidence) <= 1.0:
            raise MemoryBlocked("INVALID_MEMORY_CONFIDENCE", "Memory confidence must be between 0 and 1.")
        if scope == "USER" and not item.user_id:
            raise MemoryBlocked("USER_SCOPE_REQUIRED", "User-scoped memory requires a user identifier.")
        if scope == "CONVERSATION" and not item.conversation_id:
            raise MemoryBlocked("CONVERSATION_SCOPE_REQUIRED", "Conversation-scoped memory requires a conversation identifier.")
        if scope == "TASK" and not item.task_key:
            raise MemoryBlocked("TASK_SCOPE_REQUIRED", "Task-scoped memory requires a task key.")
        if scope == "GLOBAL" and not self._global_enabled(item.permission):
            raise MemoryBlocked("GLOBAL_MEMORY_DISABLED", "Global learning is off by default and requires explicit granted permission.")
        if item.provenance.get("instruction_authority") is True:
            raise MemoryBlocked("PROVENANCE_AUTHORITY_FORBIDDEN", "Memory provenance cannot grant imported or external content instruction authority.")
        return domain, scope

    def _visible(self, row: Any, *, project_id: str, user_id: str | None, conversation_id: str | None, permission: dict[str, Any]) -> bool:
        if row["project_id"] != project_id:
            return False
        scope = row["scope"]
        if scope == "GLOBAL" and not self._global_enabled(permission):
            return False
        if row["user_id"] is not None and row["user_id"] != user_id:
            return False
        if scope == "USER" and row["user_id"] != user_id:
            return False
        if scope == "CONVERSATION" and row["conversation_id"] != conversation_id:
            return False
        return True

    def _record(self, row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in ("provenance_json", "permission_json"):
            result[key[:-5]] = json.loads(result.pop(key) or "{}")
        result["contract_version"] = MEMORY_CONTRACT_VERSION
        return result

    def create(self, item: MemoryInput) -> dict[str, Any]:
        domain, scope = self._validate_input(item)
        now = self._now()
        memory_id = f"memory-{uuid.uuid4().hex}"
        content = item.content.strip()[:50000]
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        provenance = dict(item.provenance)
        provenance.setdefault("recorded_at", now)
        permission = dict(item.permission)
        permission.setdefault("global_learning", False)
        try:
            self.db.execute("INSERT INTO memory_records(id,project_id,user_id,conversation_id,domain,scope,task_key,source,title,content,confidence,provenance_json,permission_json,content_hash,version,status,expires_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (memory_id, item.project_id, item.user_id, item.conversation_id, domain, scope, item.task_key, item.source.upper(), item.title[:500], content, float(item.confidence), self._json(provenance), self._json(permission), content_hash, 1, "ACTIVE", item.expires_at, now, now))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                existing = self.db.execute("SELECT * FROM memory_records WHERE project_id=? AND user_id IS ? AND domain=? AND content_hash=? AND status='ACTIVE'", (item.project_id, item.user_id, domain, content_hash)).fetchone()
                if existing is not None:
                    return self._record(existing) | {"deduplicated": True}
            raise
        self._audit("create", "RECORDED", "Memory record created with source, scope, confidence, provenance, and permission.", item.project_id, memory_id, {"domain": domain, "scope": scope, "source": item.source.upper()})
        return self.get(memory_id, project_id=item.project_id, user_id=item.user_id, conversation_id=item.conversation_id, permission=permission)

    def get(self, memory_id: str, *, project_id: str, user_id: str | None = None, conversation_id: str | None = None, permission: dict[str, Any] | None = None) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM memory_records WHERE id=? AND status='ACTIVE'", (memory_id,)).fetchone()
        if row is None or not self._visible(row, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {}):
            raise MemoryBlocked("MEMORY_NOT_FOUND", "Memory was not found within the permitted scope.")
        return self._record(row)

    def _query_rows(self, *, project_id: str, user_id: str | None, conversation_id: str | None, permission: dict[str, Any], domain: str | None = None, scope: str | None = None) -> list[Any]:
        clauses = ["project_id=?", "status='ACTIVE'", "(expires_at IS NULL OR expires_at > ?)"]
        params: list[Any] = [project_id, self._now()]
        if domain:
            clauses.append("domain=?"); params.append(domain.upper())
        if scope:
            clauses.append("scope=?"); params.append(scope.upper())
        rows = self.db.execute("SELECT * FROM memory_records WHERE " + " AND ".join(clauses) + " ORDER BY updated_at DESC", tuple(params)).fetchall()
        return [row for row in rows if self._visible(row, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission)]

    def exact(self, query: str, *, project_id: str, user_id: str | None = None, conversation_id: str | None = None, permission: dict[str, Any] | None = None, domain: str | None = None, scope: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        query = query.strip()
        if not query:
            return []
        results = []
        for row in self._query_rows(project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {}, domain=domain, scope=scope):
            if query.casefold() == row["content"].casefold() or query.casefold() == row["title"].casefold() or query.casefold() in row["content"].casefold():
                results.append(self._record(row))
        return results[:max(1, min(int(limit), 100))]

    def semantic(self, query: str, *, project_id: str, user_id: str | None = None, conversation_id: str | None = None, permission: dict[str, Any] | None = None, domain: str | None = None, scope: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        tokens = set(re.findall(r"[\w]{2,}", query.casefold()))
        if not tokens:
            return []
        ranked: list[tuple[float, dict[str, Any]]] = []
        for row in self._query_rows(project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {}, domain=domain, scope=scope):
            row_tokens = set(re.findall(r"[\w]{2,}", f"{row['title']} {row['content']}".casefold()))
            overlap = len(tokens & row_tokens) / max(len(tokens | row_tokens), 1)
            exact_bonus = 0.25 if query.casefold() in row["content"].casefold() else 0.0
            if overlap <= 0 and exact_bonus <= 0:
                continue
            score = min(1.0, overlap + exact_bonus) * 0.75 + float(row["confidence"]) * 0.25
            if score > 0:
                result = self._record(row)
                result["retrieval_score"] = round(score, 6)
                result["retrieval_method"] = "deterministic_token_semantic"
                ranked.append((score, result))
        ranked.sort(key=lambda pair: (pair[0], pair[1]["updated_at"]), reverse=True)
        return [item for _, item in ranked[:max(1, min(int(limit), 100))]]

    def update(self, memory_id: str, *, project_id: str, content: str | None = None, title: str | None = None, confidence: float | None = None, provenance: dict[str, Any] | None = None, permission: dict[str, Any] | None = None, user_id: str | None = None, conversation_id: str | None = None) -> dict[str, Any]:
        current = self.get(memory_id, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {})
        new_content = (content if content is not None else current["content"]).strip()[:50000]
        new_title = title if title is not None else current["title"]
        new_confidence = float(confidence if confidence is not None else current["confidence"])
        if not 0 <= new_confidence <= 1:
            raise MemoryBlocked("INVALID_MEMORY_CONFIDENCE", "Memory confidence must be between 0 and 1.")
        new_provenance = provenance if provenance is not None else current["provenance"]
        if new_provenance.get("instruction_authority") is True:
            raise MemoryBlocked("PROVENANCE_AUTHORITY_FORBIDDEN", "Memory provenance cannot grant instruction authority.")
        now = self._now()
        self.db.execute("UPDATE memory_records SET title=?,content=?,confidence=?,provenance_json=?,content_hash=?,version=version+1,updated_at=? WHERE id=? AND project_id=? AND status='ACTIVE'", (new_title[:500], new_content, new_confidence, self._json(new_provenance), hashlib.sha256(new_content.encode()).hexdigest(), now, memory_id, project_id))
        self._audit("update", "UPDATED", "Memory record updated with incremented version.", project_id, memory_id, {"version": current["version"] + 1})
        return self.get(memory_id, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {})

    def delete(self, memory_id: str, *, project_id: str, user_id: str | None = None, conversation_id: str | None = None, permission: dict[str, Any] | None = None, reason: str = "") -> dict[str, Any]:
        current = self.get(memory_id, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {})
        now = self._now()
        self.db.execute("UPDATE memory_records SET status='DELETED',updated_at=? WHERE id=? AND project_id=?", (now, memory_id, project_id))
        self._audit("delete", "DELETED", "Memory record explicitly deleted.", project_id, memory_id, {"reason": reason[:500]})
        return {"memory_id": memory_id, "status": "DELETED", "deleted_at": now, "previous_version": current["version"]}

    def purge_expired(self, *, project_id: str | None = None) -> dict[str, Any]:
        now = self._now()
        if project_id:
            result = self.db.execute("UPDATE memory_records SET status='EXPIRED',updated_at=? WHERE project_id=? AND status='ACTIVE' AND expires_at IS NOT NULL AND expires_at <= ?", (now, project_id, now))
        else:
            result = self.db.execute("UPDATE memory_records SET status='EXPIRED',updated_at=? WHERE status='ACTIVE' AND expires_at IS NOT NULL AND expires_at <= ?", (now, now))
        count = result.rowcount
        self._audit("retention", "PURGED", "Expired memory records were marked expired.", project_id, metadata={"count": count})
        return {"status": "PURGED", "count": count, "purged_at": now}

    def export_bundle(self, *, project_id: str, user_id: str | None = None, conversation_id: str | None = None, permission: dict[str, Any] | None = None, domain: str | None = None) -> dict[str, Any]:
        records = [self._record(row) for row in self._query_rows(project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission or {}, domain=domain)]
        return {"transfer_version": TRANSFER_VERSION, "project_id": project_id, "exported_at": self._now(), "global_learning": self._global_enabled(permission or {}), "records": records}

    def import_bundle(self, bundle: dict[str, Any], *, project_id: str, user_id: str | None = None, permission: dict[str, Any] | None = None) -> dict[str, Any]:
        if bundle.get("transfer_version") != TRANSFER_VERSION or not isinstance(bundle.get("records"), list):
            raise MemoryBlocked("INVALID_MEMORY_EXPORT", "Memory import bundle does not match the versioned transfer contract.")
        imported = 0
        skipped = 0
        for raw in bundle["records"][:500]:
            if not isinstance(raw, dict):
                skipped += 1; continue
            record_scope = str(raw.get("scope", "")).upper()
            if record_scope == "GLOBAL" and not self._global_enabled(permission or {}):
                raise MemoryBlocked("GLOBAL_MEMORY_DISABLED", "Global memory import requires explicit granted permission.")
            source = str(raw.get("source", "IMPORT")).upper()
            item = MemoryInput(project_id=project_id, domain=str(raw.get("domain", "REFERENCE")), scope=record_scope, content=str(raw.get("content", "")), source="IMPORT" if source not in SOURCES else source, confidence=float(raw.get("confidence", 0)), provenance={"imported_from": raw.get("memory_id"), "original_provenance": raw.get("provenance", {}), "instruction_authority": False}, permission=permission or {"global_learning": False}, title=str(raw.get("title", "")), user_id=raw.get("user_id") if user_id is None else user_id, conversation_id=raw.get("conversation_id"), task_key=raw.get("task_key"), expires_at=raw.get("expires_at"))
            try:
                self.create(item)
                imported += 1
            except MemoryBlocked:
                skipped += 1
        self._audit("import", "IMPORTED", "Memory transfer bundle imported with validation and deduplication.", project_id, metadata={"imported": imported, "skipped": skipped})
        return {"transfer_version": TRANSFER_VERSION, "status": "IMPORTED", "imported": imported, "skipped": skipped}

    def import_obsidian_markdown(self, *, markdown: str, project_id: str, relative_path: str, user_id: str | None = None, permission: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        if len(markdown.encode("utf-8")) > 2 * 1024 * 1024:
            raise MemoryBlocked("OBSIDIAN_INPUT_TOO_LARGE", "Obsidian markdown input exceeds the bounded import size.")
        if not relative_path or relative_path.startswith(("/", "\\")) or ".." in relative_path.replace("\\", "/").split("/"):
            raise MemoryBlocked("OBSIDIAN_PATH_INVALID", "Obsidian import requires a relative, traversal-free path label.")
        headings = list(re.finditer(r"(?m)^#{1,6}\s+(.+?)\s*$", markdown))
        chunks: list[tuple[str, str]] = []
        if headings:
            for index, heading in enumerate(headings):
                start = heading.end()
                end = headings[index + 1].start() if index + 1 < len(headings) else len(markdown)
                body = markdown[start:end].strip()
                if body:
                    chunks.append((heading.group(1).strip(), body))
        elif markdown.strip():
            chunks.append((relative_path.rsplit("/", 1)[-1], markdown.strip()))
        result = []
        for title, body in chunks[:200]:
            result.append(self.create(MemoryInput(project_id=project_id, domain="OBSIDIAN", scope="PROJECT", content=body, source="OBSIDIAN", confidence=0.75, provenance={"adapter": "obsidian", "relative_path": relative_path, "instruction_authority": False}, permission=permission or {"global_learning": False}, title=title, user_id=user_id)))
        return result
