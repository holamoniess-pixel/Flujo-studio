from __future__ import annotations

import hashlib
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.db import migrate
from backend.app.provenance import ProvenanceBlocked, ProvenanceController, NODE_TYPES


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
    return db, ProvenanceController(db, events), events


def test_complete_lineage_hash_export_and_c2pa():
    db, provenance, events = make_controller()
    parent_ids = []
    for node_type in sorted(NODE_TYPES - {"FINAL_ARTIFACT"}):
        node = provenance.create_node(project_id="p1", node_type=node_type, source_ref=f"{node_type.lower()}-1", label=node_type.title(), metadata={"source_system": node_type.lower()}, reproduction={"runtime": "local-rc", "parameters": {"deterministic": True}})
        parent_ids.append(node["id"])
        assert node["immutable"] is True and node["hash_algorithm"] == "sha256" and node["reproduction"]["runtime"] == "local-rc"
        assert provenance.verify_node(node["id"], project_id="p1")["verified"] is True
    final_hash = hashlib.sha256(b"final-artifact-bytes").hexdigest()
    final = provenance.register_final_artifact(project_id="p1", source_ref="artifact-final-1", label="Final export", content_hash=final_hash, parent_ids=parent_ids, metadata={"qa": "PASS", "guard": "PASS"}, reproduction={"command": "local-render-v1", "environment": "simulator-only"})
    assert final["node_type"] == "FINAL_ARTIFACT" and final["lineage_complete"] is True and final["parent_count"] == len(parent_ids)
    graph = provenance.lineage(final["id"], project_id="p1")
    assert graph["complete_traversal"] is True and len(graph["nodes"]) == len(NODE_TYPES) and len(graph["edges"]) == len(NODE_TYPES) - 1
    assert provenance.verify_node(final["id"], project_id="p1")["verified"] is True
    exported = provenance.export(final["id"], project_id="p1")
    verified = provenance.verify_export(exported["payload"] | {"verification": exported["verification"]})
    assert verified["verified"] is True and verified["certification"] == "NOT_CERTIFIED"
    tampered = dict(exported["payload"] | {"verification": exported["verification"]})
    tampered["nodes"] = list(tampered["nodes"])
    tampered["nodes"][0] = dict(tampered["nodes"][0]) | {"label": "tampered"}
    assert provenance.verify_export(tampered)["verified"] is False
    c2pa = provenance.c2pa_manifest(final["id"], project_id="p1")
    assert c2pa["certification"] == "NOT_CERTIFIED" and c2pa["manifest"]["signature"]["status"] == "UNSIGNED_NOT_CERTIFIED"
    assert len(events.items) >= len(NODE_TYPES) + 2 and all(event["category"] == "provenance" for event in events.items)
    assert db.execute("SELECT COUNT(*) FROM provenance_nodes WHERE project_id='p1'").fetchone()[0] == len(NODE_TYPES)


def test_immutability_dedupe_project_isolation_and_cycle_block():
    _, provenance, _ = make_controller()
    first = provenance.create_node(project_id="p1", node_type="PROMPT", source_ref="prompt-1", label="Prompt", metadata={"v": 1})
    duplicate = provenance.create_node(project_id="p1", node_type="PROMPT", source_ref="prompt-1", label="Prompt", metadata={"v": 1})
    assert duplicate["id"] == first["id"] and duplicate["deduplicated"] is True
    changed = provenance.create_node(project_id="p1", node_type="PROMPT", source_ref="prompt-1", label="Prompt", metadata={"v": 2})
    assert changed["id"] != first["id"]
    try:
        provenance.get_node(first["id"], project_id="p2")
    except ProvenanceBlocked as exc:
        assert exc.code == "NODE_NOT_FOUND"
    else:
        raise AssertionError("cross-project provenance access must fail")
    child = provenance.create_node(project_id="p1", node_type="MODEL", source_ref="model-1")
    provenance.link(project_id="p1", child_id=child["id"], parent_id=first["id"], relationship="selected_for")
    try:
        provenance.link(project_id="p1", child_id=first["id"], parent_id=child["id"], relationship="feeds")
    except ProvenanceBlocked as exc:
        assert exc.code == "LINEAGE_CYCLE"
    else:
        raise AssertionError("lineage cycle must be rejected")
    try:
        provenance.register_final_artifact(project_id="p1", source_ref="incomplete", label="bad", content_hash=hashlib.sha256(b"bad").hexdigest(), parent_ids=[first["id"]])
    except ProvenanceBlocked as exc:
        assert exc.code == "COMPLETE_LINEAGE_REQUIRED"
    else:
        raise AssertionError("incomplete final lineage must be rejected")


def main():
    test_complete_lineage_hash_export_and_c2pa()
    test_immutability_dedupe_project_isolation_and_cycle_block()
    print("provenance: passed; complete lineage, immutable records, graph traversal, hashes, export tamper verification, reproduction metadata, C2PA-compatible non-certifying manifest, isolation, and cycle blocking verified")


if __name__ == "__main__":
    main()
