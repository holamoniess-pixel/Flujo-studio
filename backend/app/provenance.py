from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

PROVENANCE_CONTRACT_VERSION = "provenance.graph.v1"
EXPORT_VERSION = "provenance.export.v1"
HASH_ALGORITHM = "sha256"
NODE_TYPES = {
    "USER_REQUEST", "CONVERSATION", "REFERENCE", "ENTITY", "MEMORY", "GENOME",
    "BLUEPRINT", "PROMPT", "MODEL", "ADVISOR", "TOOL", "MEDIA_OPERATION",
    "QA", "GUARD", "RENDER", "FINAL_ARTIFACT",
}
REQUIRED_FINAL_PARENTS = NODE_TYPES - {"FINAL_ARTIFACT"}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class ProvenanceBlocked(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ProvenanceController:
    def __init__(self, db: Any, events: Any | None = None) -> None:
        self.db = db
        self.events = events

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _json(self, value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _hash(self, value: Any) -> str:
        return hashlib.sha256(self._json(value).encode("utf-8")).hexdigest()

    def _audit(self, operation: str, status: str, message: str, project_id: str | None = None, node_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
        if self.events is not None:
            self.events.audit(category="provenance", message=message, project_id=project_id, artifact_id=node_id, route="provenance", operation=operation, status=status, metadata=metadata or {})

    def _validate_type(self, node_type: str) -> str:
        normalized = node_type.upper()
        if normalized not in NODE_TYPES:
            raise ProvenanceBlocked("INVALID_NODE_TYPE", "Provenance node type is not supported.")
        return normalized

    def _validate_hash(self, content_hash: str) -> str:
        value = content_hash.casefold()
        if not HASH_RE.fullmatch(value):
            raise ProvenanceBlocked("INVALID_CHECKSUM", "Provenance content_hash must be a lowercase or uppercase SHA-256 hexadecimal digest.")
        return value

    def create_node(self, *, project_id: str, node_type: str, source_ref: str, label: str = "", content_hash: str | None = None, hash_material: Any | None = None, metadata: dict[str, Any] | None = None, reproduction: dict[str, Any] | None = None) -> dict[str, Any]:
        normalized_type = self._validate_type(node_type)
        if not project_id.strip() or not source_ref.strip():
            raise ProvenanceBlocked("NODE_IDENTITY_REQUIRED", "Provenance project_id and source_ref are required.")
        metadata = dict(metadata or {})
        reproduction = dict(reproduction or {})
        hash_scope = "content" if content_hash else "canonical_metadata"
        if content_hash:
            digest = self._validate_hash(content_hash)
        else:
            digest = self._hash({"project_id": project_id, "node_type": normalized_type, "source_ref": source_ref, "label": label, "metadata": metadata, "reproduction": reproduction, "hash_material": hash_material})
        material_json = self._json(hash_material) if hash_material is not None else None
        node_id = f"prov-{uuid.uuid4().hex}"
        now = self._now()
        existing = self.db.execute("SELECT * FROM provenance_nodes WHERE project_id=? AND node_type=? AND source_ref=? AND content_hash=?", (project_id, normalized_type, source_ref, digest)).fetchone()
        if existing is not None:
            return self._record(existing) | {"deduplicated": True}
        self.db.execute("INSERT INTO provenance_nodes(id,project_id,node_type,source_ref,label,content_hash,hash_algorithm,hash_scope,hash_material_json,metadata_json,reproduction_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (node_id, project_id, normalized_type, source_ref[:500], label[:500], digest, HASH_ALGORITHM, hash_scope, material_json, self._json(metadata), self._json(reproduction), now))
        self._audit("create_node", "RECORDED", "Immutable provenance node created.", project_id, node_id, {"node_type": normalized_type, "hash": digest, "hash_scope": hash_scope})
        return self.get_node(node_id, project_id=project_id)

    def _record(self, row: Any) -> dict[str, Any]:
        result = dict(row)
        for key in ("metadata_json", "reproduction_json", "hash_material_json"):
            target = key[:-5]
            value = result.pop(key)
            result[target] = json.loads(value) if value else None
        result["contract_version"] = PROVENANCE_CONTRACT_VERSION
        result["immutable"] = True
        return result

    def get_node(self, node_id: str, *, project_id: str) -> dict[str, Any]:
        row = self.db.execute("SELECT * FROM provenance_nodes WHERE id=? AND project_id=?", (node_id, project_id)).fetchone()
        if row is None:
            raise ProvenanceBlocked("NODE_NOT_FOUND", "Provenance node was not found in this project.")
        return self._record(row)

    def link(self, *, project_id: str, child_id: str, parent_id: str, relationship: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        if child_id == parent_id:
            raise ProvenanceBlocked("LINEAGE_CYCLE", "A provenance node cannot be its own parent.")
        child = self.get_node(child_id, project_id=project_id)
        parent = self.get_node(parent_id, project_id=project_id)
        if self._reachable(parent_id, child_id, project_id=project_id):
            raise ProvenanceBlocked("LINEAGE_CYCLE", "The requested provenance edge would create a cycle.")
        edge_id = f"edge-{uuid.uuid4().hex}"
        try:
            self.db.execute("INSERT INTO provenance_edges(id,project_id,child_node_id,parent_node_id,relationship,metadata_json,created_at) VALUES(?,?,?,?,?,?,?)", (edge_id, project_id, child_id, parent_id, relationship[:200], self._json(metadata or {}), self._now()))
        except Exception as exc:
            if "UNIQUE" in str(exc).upper():
                existing = self.db.execute("SELECT * FROM provenance_edges WHERE project_id=? AND child_node_id=? AND parent_node_id=? AND relationship=?", (project_id, child_id, parent_id, relationship)).fetchone()
                if existing is not None:
                    return dict(existing) | {"deduplicated": True}
            raise
        self._audit("link", "RECORDED", "Append-only provenance lineage edge created.", project_id, child_id, {"parent_id": parent_id, "relationship": relationship, "parent_type": parent["node_type"], "child_type": child["node_type"]})
        return dict(self.db.execute("SELECT * FROM provenance_edges WHERE id=?", (edge_id,)).fetchone())

    def _reachable(self, start_id: str, target_id: str, *, project_id: str, limit: int = 1000) -> bool:
        pending = [start_id]
        visited: set[str] = set()
        while pending and len(visited) < limit:
            current = pending.pop()
            if current in visited:
                continue
            visited.add(current)
            if current == target_id:
                return True
            rows = self.db.execute("SELECT parent_node_id FROM provenance_edges WHERE project_id=? AND child_node_id=?", (project_id, current)).fetchall()
            pending.extend(row["parent_node_id"] for row in rows)
        return False

    def register_final_artifact(self, *, project_id: str, source_ref: str, label: str, content_hash: str, parent_ids: list[str], metadata: dict[str, Any] | None = None, reproduction: dict[str, Any] | None = None) -> dict[str, Any]:
        if len(set(parent_ids)) != len(parent_ids):
            raise ProvenanceBlocked("DUPLICATE_PARENT", "Final artifact lineage cannot contain duplicate parent IDs.")
        if not parent_ids:
            raise ProvenanceBlocked("COMPLETE_LINEAGE_REQUIRED", "A final artifact requires complete lineage parents.")
        parents = [self.get_node(parent_id, project_id=project_id) for parent_id in parent_ids]
        present = {parent["node_type"] for parent in parents}
        missing = sorted(REQUIRED_FINAL_PARENTS - present)
        if missing:
            raise ProvenanceBlocked("COMPLETE_LINEAGE_REQUIRED", "Final artifact lineage is incomplete: " + ", ".join(missing))
        final = self.create_node(project_id=project_id, node_type="FINAL_ARTIFACT", source_ref=source_ref, label=label, content_hash=content_hash, metadata=metadata, reproduction=reproduction)
        for parent_id in parent_ids:
            self.link(project_id=project_id, child_id=final["id"], parent_id=parent_id, relationship="derived_from")
        self._audit("register_final", "COMPLETE", "Final artifact registered with complete immutable lineage.", project_id, final["id"], {"parent_count": len(parent_ids), "final_checksum": final["content_hash"]})
        return self.get_node(final["id"], project_id=project_id) | {"lineage_complete": True, "parent_count": len(parent_ids)}

    def lineage(self, node_id: str, *, project_id: str, max_nodes: int = 500) -> dict[str, Any]:
        root = self.get_node(node_id, project_id=project_id)
        nodes: dict[str, dict[str, Any]] = {root["id"]: root}
        edges: list[dict[str, Any]] = []
        pending = [node_id]
        while pending and len(nodes) < max_nodes:
            child_id = pending.pop(0)
            for edge in self.db.execute("SELECT * FROM provenance_edges WHERE project_id=? AND child_node_id=? ORDER BY created_at", (project_id, child_id)).fetchall():
                edge_data = dict(edge)
                edges.append(edge_data)
                parent = self.get_node(edge["parent_node_id"], project_id=project_id)
                if parent["id"] not in nodes:
                    nodes[parent["id"]] = parent
                    pending.append(parent["id"])
        return {"contract_version": PROVENANCE_CONTRACT_VERSION, "root": root, "nodes": list(nodes.values()), "edges": edges, "complete_traversal": not pending}

    def verify_node(self, node_id: str, *, project_id: str) -> dict[str, Any]:
        node = self.get_node(node_id, project_id=project_id)
        recomputed = None
        if node["hash_scope"] == "canonical_metadata":
            recomputed = self._hash({"project_id": node["project_id"], "node_type": node["node_type"], "source_ref": node["source_ref"], "label": node["label"], "metadata": node["metadata"], "reproduction": node["reproduction"], "hash_material": node["hash_material"]})
        hash_format_valid = bool(HASH_RE.fullmatch(node["content_hash"]))
        verified = hash_format_valid and (recomputed is None or recomputed == node["content_hash"])
        return {"node_id": node_id, "verified": verified, "hash_algorithm": node["hash_algorithm"], "hash_scope": node["hash_scope"], "claimed_hash": node["content_hash"], "recomputed_hash": recomputed, "immutable": True, "contract_version": PROVENANCE_CONTRACT_VERSION}

    def export(self, node_id: str, *, project_id: str) -> dict[str, Any]:
        graph = self.lineage(node_id, project_id=project_id)
        payload = {"transfer_version": EXPORT_VERSION, "project_id": project_id, "root_node_id": node_id, "nodes": graph["nodes"], "edges": graph["edges"]}
        bundle_hash = self._hash(payload)
        verification = {"bundle_hash": bundle_hash, "hash_algorithm": HASH_ALGORITHM, "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]), "all_nodes_immutable": all(node.get("immutable") for node in graph["nodes"]), "certification": "NOT_CERTIFIED"}
        export_id = f"export-{uuid.uuid4().hex}"
        self.db.execute("INSERT INTO provenance_exports(id,project_id,root_node_id,bundle_hash,bundle_json,verification_json,created_at) VALUES(?,?,?,?,?,?,?)", (export_id, project_id, node_id, bundle_hash, self._json(payload), self._json(verification), self._now()))
        self._audit("export", "VERIFIED", "Immutable provenance graph exported with verification metadata.", project_id, node_id, verification)
        return {"export_id": export_id, "payload": payload, "verification": verification, "contract_version": PROVENANCE_CONTRACT_VERSION}

    def verify_export(self, bundle: dict[str, Any]) -> dict[str, Any]:
        if bundle.get("transfer_version") != EXPORT_VERSION or not isinstance(bundle.get("nodes"), list) or not isinstance(bundle.get("edges"), list):
            raise ProvenanceBlocked("INVALID_PROVENANCE_EXPORT", "Provenance export does not match its versioned transfer contract.")
        payload = {"transfer_version": bundle["transfer_version"], "project_id": bundle.get("project_id"), "root_node_id": bundle.get("root_node_id"), "nodes": bundle["nodes"], "edges": bundle["edges"]}
        recomputed = self._hash(payload)
        claimed = str(bundle.get("bundle_hash", bundle.get("verification", {}).get("bundle_hash", ""))).casefold()
        return {"verified": bool(HASH_RE.fullmatch(claimed)) and claimed == recomputed, "claimed_bundle_hash": claimed, "recomputed_bundle_hash": recomputed, "node_count": len(bundle["nodes"]), "edge_count": len(bundle["edges"]), "certification": "NOT_CERTIFIED", "contract_version": PROVENANCE_CONTRACT_VERSION}

    def c2pa_manifest(self, node_id: str, *, project_id: str) -> dict[str, Any]:
        graph = self.lineage(node_id, project_id=project_id)
        manifest = {"manifest_version": "c2pa-compatible-local.v1", "claim_generator": "Flujo Studio local provenance", "instance_id": node_id, "assertions": [{"label": "org.flujo.provenance", "data": {"contract_version": PROVENANCE_CONTRACT_VERSION, "root_node_id": node_id, "node_count": len(graph["nodes"]), "edge_count": len(graph["edges"]), "final_checksum": graph["root"]["content_hash"]}}], "signature": {"status": "UNSIGNED_NOT_CERTIFIED", "note": "This is a local C2PA-compatible abstraction and is not a C2PA certification or cryptographic claim signature."}}
        return {"manifest": manifest, "certification": "NOT_CERTIFIED", "verified": False, "contract_version": PROVENANCE_CONTRACT_VERSION}
