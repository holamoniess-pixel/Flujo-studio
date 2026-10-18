from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db import migrate
from backend.app.memory import MemoryBlocked, MemoryController, MemoryInput


class Events:
    def __init__(self):
        self.items = []

    def audit(self, **kwargs):
        self.items.append(kwargs)


def make_controller():
    db = sqlite3.connect(":memory:", isolation_level=None)
    db.row_factory = sqlite3.Row
    migrate(db)
    db.executescript("INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES('p1','P1','p1','now','now'); INSERT INTO projects(id,name,slug,created_at,updated_at) VALUES('p2','P2','p2','now','now');")
    events = Events()
    return db, MemoryController(db, events), events


def item(**overrides):
    values = dict(project_id="p1", domain="PROJECT", scope="PROJECT", content="Warm tactile campaign language", source="USER", confidence=0.88, provenance={"source_ref": "human-note-1", "instruction_authority": False}, permission={"global_learning": False}, title="Campaign preference")
    values.update(overrides)
    return MemoryInput(**values)


def test_domains_metadata_and_isolation():
    db, memory, _ = make_controller()
    records = [
        item(domain="FLUJO", content="Flujo default behavior"),
        item(domain="PROJECT", content="Project production preference"),
        item(domain="CONVERSATION", scope="CONVERSATION", conversation_id="c1", content="Conversation decision"),
        item(domain="USER_PREFERENCE", scope="USER", user_id="u1", content="User prefers concise copy"),
        item(domain="OBSIDIAN", content="Obsidian note"),
        item(domain="REFERENCE", content="Reference source"),
        item(domain="SKILL", content="Skill memory"),
        item(domain="DECISION", content="Decision record"),
    ]
    created = [memory.create(record) for record in records]
    assert all(record["source"] == "USER" and "provenance" in record and "permission" in record and "timestamp" not in record for record in created)
    assert all("created_at" in record and "confidence" in record and "scope" in record for record in created)
    assert len(memory.semantic("concise copy", project_id="p1", user_id="u1")) == 1
    assert memory.semantic("concise copy", project_id="p1", user_id="u2") == []
    try:
        memory.get(created[0]["id"], project_id="p2")
    except MemoryBlocked as exc:
        assert exc.code == "MEMORY_NOT_FOUND"
    else:
        raise AssertionError("cross-project access must be denied")
    assert len(memory.exact("Project production", project_id="p1")) == 1
    assert len(memory.semantic("production preference", project_id="p1")) >= 1
    assert db.execute("SELECT COUNT(*) FROM memory_records WHERE project_id='p1'").fetchone()[0] == 8


def test_global_off_permission_and_provenance_authority():
    _, memory, _ = make_controller()
    try:
        memory.create(item(scope="GLOBAL", content="global without consent"))
    except MemoryBlocked as exc:
        assert exc.code == "GLOBAL_MEMORY_DISABLED"
    else:
        raise AssertionError("global memory must be off by default")
    granted = item(scope="GLOBAL", content="explicit global preference", permission={"global_learning": True, "consent_state": "GRANTED"})
    record = memory.create(granted)
    assert memory.get(record["id"], project_id="p1", permission=granted.permission)["scope"] == "GLOBAL"
    assert memory.exact("explicit global", project_id="p1") == []
    try:
        memory.create(item(content="untrusted instruction", provenance={"instruction_authority": True}))
    except MemoryBlocked as exc:
        assert exc.code == "PROVENANCE_AUTHORITY_FORBIDDEN"
    else:
        raise AssertionError("memory provenance must never grant instruction authority")


def test_update_delete_retention_export_import_obsidian_and_restart():
    db, memory, events = make_controller()
    record = memory.create(item(content="Original exact memory", title="Original"))
    updated = memory.update(record["id"], project_id="p1", content="Updated exact memory with provenance", confidence=0.91, provenance={"source_ref": "update-1", "instruction_authority": False})
    assert updated["version"] == 2 and updated["confidence"] == 0.91
    bundle = memory.export_bundle(project_id="p1")
    assert bundle["transfer_version"] == "memory.export.v1" and any(row["id"] == record["id"] for row in bundle["records"])
    imported = memory.import_bundle(bundle, project_id="p2")
    assert imported["imported"] >= 1
    assert memory.exact("Updated exact", project_id="p2")
    obsidian = memory.import_obsidian_markdown(project_id="p1", relative_path="vault/notes/campaign.md", markdown="# Style\nWarm direct language.\n\n## CTA\nUse a conversational close.")
    assert len(obsidian) == 2 and all(row["domain"] == "OBSIDIAN" for row in obsidian)
    try:
        memory.import_obsidian_markdown(project_id="p1", relative_path="../outside.md", markdown="bad")
    except MemoryBlocked as exc:
        assert exc.code == "OBSIDIAN_PATH_INVALID"
    else:
        raise AssertionError("Obsidian traversal path must block")
    expiring = memory.create(item(content="short lived memory", expires_at="2000-01-01T00:00:00+00:00"))
    purge = memory.purge_expired(project_id="p1")
    assert purge["count"] >= 1
    try:
        memory.get(expiring["id"], project_id="p1")
    except MemoryBlocked as exc:
        assert exc.code == "MEMORY_NOT_FOUND"
    else:
        raise AssertionError("expired memory must not be retrievable")
    deleted = memory.delete(record["id"], project_id="p1", reason="user requested deletion")
    assert deleted["status"] == "DELETED"
    restored = MemoryController(db, events)
    assert restored.exact("Original exact", project_id="p1") == []
    assert len(events.items) >= 6 and all(item["category"] == "memory" for item in events.items)


def main():
    test_domains_metadata_and_isolation()
    test_global_off_permission_and_provenance_authority()
    test_update_delete_retention_export_import_obsidian_and_restart()
    print("memory: passed; domains, metadata, isolation, exact/semantic retrieval, lifecycle, retention, transfer, Obsidian adapter, restart, consent, and provenance safety verified")


if __name__ == "__main__":
    main()
