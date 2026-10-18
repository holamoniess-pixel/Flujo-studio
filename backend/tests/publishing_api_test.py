from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PORT = 47823
TOKEN = "publish-contract-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("x-flujo-app-token", token)
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> dict:
    for _ in range(30):
        try:
            status, body = request("/health", token=None)
            if status == 200:
                return body
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("isolated backend did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-publish-contract-") as directory:
        temp_root = Path(directory)
        env = os.environ.copy()
        env.update({
            "FLUJO_PROJECT_ROOT": str(temp_root),
            "FLUJO_DATA_DIR": str(temp_root / ".flujo"),
            "FLUJO_PORT": str(PORT),
            "FLUJO_APP_TOKEN": TOKEN,
            "FLUJO_LOG_LEVEL": "warning",
        })
        process = subprocess.Popen([os.environ.get("M10_TEST_PYTHON", sys.executable), "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            health = wait_for_health()
            assert health["status"] == "ok"

            fixture = temp_root / "generated" / "prep_fixture.mp4"
            fixture.parent.mkdir(parents=True, exist_ok=True)
            fixture.write_bytes(b"fake-video-bytes" * 100)

            db_path = temp_root / ".flujo" / "runtime" / "flujo.sqlite3"
            connection = sqlite3.connect(db_path)
            try:
                now = "2026-10-12T00:00:00+00:00"
                connection.execute(
                    "INSERT INTO artifacts(id,project_id,kind,name,path,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                    ("artifact_ready_1", "project_northstar", "scene-plan", "Citrus at Dusk / Cut 01", str(fixture), "ready", now, "{}"),
                )
                connection.execute(
                    "INSERT INTO artifacts(id,project_id,kind,name,path,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)",
                    ("artifact_blocked_1", "project_northstar", "scene-plan", "Unrendered Cut", str(fixture), "blocked_dependency", now, "{}"),
                )
                connection.commit()
            finally:
                connection.close()

            # 1. Adapter surface: youtube reference adapter, honestly not configured.
            status, adapters = request("/api/publish/adapters")
            assert status == 200 and isinstance(adapters, list), adapters
            youtube = next((item for item in adapters if item["platform_id"] == "youtube"), None)
            assert youtube is not None, adapters
            assert youtube["configured"] is False and youtube["credential_reference"] == "youtube"
            assert "secret" not in json.dumps(youtube).lower()

            # 2. Unknown platform and missing artifact fail explicitly.
            status, unknown_platform = request("/api/projects/project_northstar/publish/prep", "POST", {"artifact_id": "artifact_ready_1", "platform": "vimeo"})
            assert status == 404 and unknown_platform["detail"]["code"] == "ADAPTER_NOT_FOUND", unknown_platform
            status, missing_artifact = request("/api/projects/project_northstar/publish/prep", "POST", {"artifact_id": "artifact_missing", "platform": "youtube"})
            assert status == 404 and missing_artifact["detail"]["code"] == "ARTIFACT_NOT_FOUND", missing_artifact

            # 3. Unpublishable artifact is stopped at the Guard gate; run recorded as blocked.
            status, gated = request("/api/projects/project_northstar/publish/prep", "POST", {"artifact_id": "artifact_blocked_1", "platform": "youtube"})
            assert status == 423 and gated["detail"]["code"] == "GUARD_PUBLISH_BLOCKED", gated
            run_id_blocked = gated["detail"]["run_id"]
            status, blocked_run = request(f"/api/publish/prep/{run_id_blocked}")
            assert status == 200 and blocked_run["status"] == "blocked" and blocked_run["steps"][0]["status"] == "blocked"

            # 4. Video modality honestly requires human review under the Guard gate.
            status, video_gated = request("/api/projects/project_northstar/publish/prep", "POST", {"artifact_id": "artifact_ready_1", "platform": "youtube", "modality": "video"})
            assert status == 423 and video_gated["detail"]["code"] == "GUARD_PUBLISH_REVIEW_REQUIRED", video_gated

            # 5. Publishable artifact completes the full prep pipeline (document modality passes the gate).
            status, prep = request("/api/projects/project_northstar/publish/prep", "POST", {"artifact_id": "artifact_ready_1", "platform": "youtube"})
            assert status == 200 and prep["status"] == "completed", prep
            assert [step["name"] for step in prep["steps"]] == ["guard_gate", "metadata_package", "captions", "thumbnail", "render_finalize", "bundle"]
            assert all(step["status"] in {"passed", "skipped"} for step in prep["steps"]), prep["steps"]
            assert prep["steps"][0]["status"] == "passed" and prep["steps"][-1]["status"] == "passed"
            bundle_path = Path(prep["bundle_path"])
            assert bundle_path.is_file()
            run_id = prep["run_id"]

            # 6. Run detail and project listing read back the recorded state.
            status, fetched = request(f"/api/publish/prep/{run_id}")
            assert status == 200 and fetched["run_id"] == run_id and fetched["guard_decision_id"]
            status, listing = request("/api/projects/project_northstar/publish/prep?limit=10")
            assert status == 200 and any(item["run_id"] == run_id for item in listing)

            # 7. Guard publish checks were recorded for both runs.
            connection = sqlite3.connect(db_path)
            try:
                rows = connection.execute("SELECT status FROM guard_publish_checks WHERE artifact_id IN ('artifact_ready_1','artifact_blocked_1')").fetchall()
            finally:
                connection.close()
            assert sorted(item[0] for item in rows) == ["BLOCK", "PASS", "REVIEW"], rows

            # 8. Upload without a credential is refused honestly; blocked runs cannot upload.
            status, refused = request(f"/api/publish/prep/{run_id}/upload", "POST")
            assert status == 423 and refused["detail"]["code"] == "ADAPTER_CREDENTIAL_UNAVAILABLE", refused
            assert refused["detail"]["retryable"] is False
            status, not_ready = request(f"/api/publish/prep/{run_id_blocked}/upload", "POST")
            assert status == 409 and not_ready["detail"]["code"] == "PUBLISH_PREP_NOT_READY", not_ready

            print(json.dumps({"adapters": len(adapters), "prep_completed": True, "upload_refused_honestly": True}))
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            if process.returncode not in (0, 1, -15, 15):
                raise AssertionError(f"isolated backend exited unexpectedly: {process.returncode}")


if __name__ == "__main__":
    main()
