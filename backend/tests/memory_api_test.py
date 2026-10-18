from __future__ import annotations

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
PORT = 47829
TOKEN = "memory-api-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict | list]:
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
    with tempfile.TemporaryDirectory(prefix="flujo-memory-api-") as directory:
        root = Path(directory)
        env = os.environ.copy()
        env.update({"FLUJO_PROJECT_ROOT": str(root), "FLUJO_DATA_DIR": str(root / ".flujo"), "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for_health()
            base = {"project_id": "project_northstar", "domain": "REFERENCE", "scope": "PROJECT", "content": "Warm direct production reference language", "source": "REFERENCE", "confidence": 0.9, "provenance": {"source_ref": "api-ref-1", "instruction_authority": False}, "permission": {"global_learning": False}, "title": "Reference note"}
            status, created = request("/api/memory/records", "POST", base)
            assert status == 200 and created["contract_version"] == "memory.record.v1" and created["source"] == "REFERENCE"
            memory_id = created["id"]
            status, exact = request("/api/memory/search/exact", "POST", {"project_id": "project_northstar", "query": "Warm direct", "limit": 10})
            assert status == 200 and len(exact) == 1 and exact[0]["id"] == memory_id
            status, semantic = request("/api/memory/search/semantic", "POST", {"project_id": "project_northstar", "query": "production reference", "limit": 10})
            assert status == 200 and semantic and semantic[0]["retrieval_method"] == "deterministic_token_semantic"
            status, updated = request(f"/api/memory/records/{memory_id}", "PATCH", {"project_id": "project_northstar", "content": "Updated production reference language", "confidence": 0.95, "provenance": {"source_ref": "api-update", "instruction_authority": False}})
            assert status == 200 and updated["version"] == 2
            status, global_denied = request("/api/memory/records", "POST", {**base, "scope": "GLOBAL", "content": "global denied"})
            assert status == 422 and global_denied["detail"]["code"] == "GLOBAL_MEMORY_DISABLED"
            status, user_record = request("/api/memory/records", "POST", {**base, "scope": "USER", "user_id": "user-a", "content": "private user preference"})
            assert status == 200
            status, hidden = request("/api/memory/search/exact", "POST", {"project_id": "project_northstar", "user_id": "user-b", "query": "private user preference"})
            assert status == 200 and hidden == []
            status, obsidian = request("/api/memory/obsidian/import", "POST", {"project_id": "project_northstar", "relative_path": "vault/notes/api.md", "markdown": "# Style\nUse warm light.\n\n## Voice\nKeep the close direct."})
            assert status == 200 and len(obsidian) == 2 and all(record["domain"] == "OBSIDIAN" for record in obsidian)
            status, bundle = request("/api/memory/export", "POST", {"project_id": "project_northstar", "query": "all"})
            assert status == 200 and bundle["transfer_version"] == "memory.export.v1" and bundle["records"]
            status, imported = request("/api/memory/import", "POST", {"project_id": "project_northstar", "bundle": bundle})
            assert status == 200 and imported["status"] == "IMPORTED"
            status, deleted = request(f"/api/memory/records/{memory_id}", "DELETE", {"project_id": "project_northstar", "reason": "user request"})
            assert status == 200 and deleted["status"] == "DELETED"
            status, absent = request(f"/api/memory/records/{memory_id}?project_id=project_northstar")
            assert status == 404
            status, unauth = request("/api/memory/records", token=None)
            assert status == 401
            print("memory_api: passed; authenticated records, exact/semantic retrieval, update/delete, isolation, global-off, Obsidian, export/import, retention surface, and auth verified")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
