from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 47830
TOKEN = "provenance-api-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method, headers={"content-type": "application/json"})
    if token:
        req.add_header("x-flujo-app-token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> None:
    for _ in range(40):
        try:
            if request("/health", token=None)[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("backend did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-provenance-api-") as directory:
        root = Path(directory)
        env = os.environ.copy()
        env.update({"FLUJO_PROJECT_ROOT": str(root), "FLUJO_DATA_DIR": str(root / ".flujo"), "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for_health()
            node_ids = []
            for node_type in ("USER_REQUEST", "CONVERSATION", "REFERENCE", "ENTITY", "MEMORY", "GENOME", "BLUEPRINT", "PROMPT", "MODEL", "ADVISOR", "TOOL", "MEDIA_OPERATION", "QA", "GUARD", "RENDER"):
                status, node = request("/api/provenance/nodes", "POST", {"project_id": "project_northstar", "node_type": node_type, "source_ref": f"api-{node_type.lower()}", "label": node_type, "metadata": {"api": True}, "reproduction": {"runtime": "local-api"}})
                assert status == 200 and node["immutable"] is True and node["contract_version"] == "provenance.graph.v1"
                node_ids.append(node["id"])
            final_hash = hashlib.sha256(b"api-final").hexdigest()
            status, incomplete = request("/api/provenance/final-artifacts", "POST", {"project_id": "project_northstar", "source_ref": "api-final", "label": "API final", "content_hash": final_hash, "parent_ids": node_ids[:1]})
            assert status == 422 and incomplete["detail"]["code"] == "COMPLETE_LINEAGE_REQUIRED"
            status, final = request("/api/provenance/final-artifacts", "POST", {"project_id": "project_northstar", "source_ref": "api-final", "label": "API final", "content_hash": final_hash, "parent_ids": node_ids, "metadata": {"qa": "PASS", "guard": "PASS"}, "reproduction": {"runtime": "local-api", "command": "render-v1"}})
            assert status == 200 and final["lineage_complete"] is True
            final_id = final["id"]
            status, graph = request(f"/api/provenance/nodes/{final_id}/lineage?project_id=project_northstar")
            assert status == 200 and len(graph["nodes"]) == 16 and len(graph["edges"]) == 15
            status, verified = request(f"/api/provenance/nodes/{final_id}/verify?project_id=project_northstar")
            assert status == 200 and verified["verified"] is True
            status, exported = request("/api/provenance/exports", "POST", {"project_id": "project_northstar", "node_id": final_id})
            assert status == 200 and exported["verification"]["certification"] == "NOT_CERTIFIED"
            bundle = exported["payload"] | {"verification": exported["verification"]}
            status, export_verified = request("/api/provenance/exports/verify", "POST", {"bundle": bundle})
            assert status == 200 and export_verified["verified"] is True
            tampered = dict(bundle)
            tampered["nodes"] = list(tampered["nodes"])
            tampered["nodes"][0] = dict(tampered["nodes"][0]) | {"label": "tampered"}
            status, tamper_result = request("/api/provenance/exports/verify", "POST", {"bundle": tampered})
            assert status == 200 and tamper_result["verified"] is False
            status, c2pa = request(f"/api/provenance/nodes/{final_id}/c2pa?project_id=project_northstar")
            assert status == 200 and c2pa["certification"] == "NOT_CERTIFIED" and c2pa["verified"] is False
            status, hidden = request(f"/api/provenance/nodes/{final_id}?project_id=project_other")
            assert status == 404
            status, unauth = request("/api/provenance/nodes", token=None)
            assert status == 401
            print("provenance_api: passed; authenticated immutable nodes, complete lineage, graph, hashes, export verification, tamper detection, C2PA non-certification, isolation, and auth verified")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
