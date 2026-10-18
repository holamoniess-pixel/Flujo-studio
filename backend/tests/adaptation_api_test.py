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
PORT = 47827
TOKEN = "adaptation-api-token"
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


def start(root: Path):
    env = os.environ.copy()
    env.update({"FLUJO_PROJECT_ROOT": str(root), "FLUJO_DATA_DIR": str(root / ".flujo"), "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
    return subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def stop(process):
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def cases(passed: bool = True):
    return [{"kind": kind, "case_id": kind, "passed": passed} for kind in ("golden", "regression", "adversarial", "fresh_session")]


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-adaptation-api-") as directory:
        root = Path(directory)
        process = start(root)
        try:
            wait_for_health()
            base = {"project_id": "project_northstar", "task": "improve scene intro", "feedback": "Retention improved", "candidate_type": "prompt", "scope": "PROJECT", "proposal": {"template": "intro-v2"}, "evidence_ids": ["eval-1", "eval-2"]}
            status, experience = request("/api/adaptation/experiences", "POST", base)
            assert status == 200 and experience["scope"] == "PROJECT" and experience["evidence_backed"] is True
            status, feedback = request(f"/api/adaptation/experiences/{experience['experience_id']}/feedback", "POST", {"outcome": "accepted", "quality": 0.9, "attribution": "task:scene-intro", "evidence_ids": ["eval-3"]})
            assert status == 200 and feedback["status"] == "ATTRIBUTED"
            status, global_denied = request("/api/adaptation/experiences", "POST", {**base, "scope": "GLOBAL"})
            assert status == 422 and global_denied["detail"]["code"] == "GLOBAL_CONSENT_REQUIRED"
            status, global_allowed = request("/api/adaptation/experiences", "POST", {**base, "scope": "GLOBAL", "consent": {"consent_state": "GRANTED", "project_scope": "GLOBAL_ALLOWED"}})
            assert status == 200 and global_allowed["scope"] == "GLOBAL"
            status, candidate = request("/api/adaptation/candidates", "POST", {**base, "experience_id": experience["experience_id"]})
            assert status == 200 and candidate["status"] == "PROPOSED" and candidate["current_version"] == 0
            candidate_id = candidate["id"]
            status, compiled = request(f"/api/adaptation/candidates/{candidate_id}/compile", "POST", {})
            assert status == 200 and compiled["status"] == "COMPILED" and compiled["compiled"]["weights_modified"] is False
            status, evaluation = request(f"/api/adaptation/candidates/{candidate_id}/evaluate", "POST", {"cases": cases()})
            assert status == 200 and evaluation["status"] == "PASS" and evaluation["missing_kinds"] == []
            status, promoted = request(f"/api/adaptation/candidates/{candidate_id}/promote", "POST", {"reviewer_id": "human-adaptation", "reason": "All required evaluation categories passed."})
            assert status == 200 and promoted["status"] == "ACTIVE" and promoted["current_version"] == 1
            status, reeval = request(f"/api/adaptation/candidates/{candidate_id}/evaluate", "POST", {"cases": cases()})
            assert status == 200 and reeval["status"] == "PASS"
            status, promoted2 = request(f"/api/adaptation/candidates/{candidate_id}/promote", "POST", {"reviewer_id": "human-adaptation-2", "reason": "Second version approved."})
            assert status == 200 and promoted2["current_version"] == 2
            status, drift = request(f"/api/adaptation/candidates/{candidate_id}/drift", "POST", {"observed_score": 0.3, "threshold": 0.7, "evidence": ["drift-1"]})
            assert status == 200 and drift["state"] == "DRIFTED" and drift["automatic_rollback"] is False
            status, rolled = request(f"/api/adaptation/candidates/{candidate_id}/rollback", "POST", {"reviewer_id": "human-adaptation-3", "reason": "Measured drift requires reversal.", "target_version": 1})
            assert status == 200 and rolled["status"] == "ROLLED_BACK" and rolled["current_version"] == 1
            status, retired = request(f"/api/adaptation/candidates/{candidate_id}/retire", "POST", {"reviewer_id": "human-adaptation-4", "reason": "Superseded by a safer recipe."})
            assert status == 200 and retired["status"] == "RETIRED"
            status, affinity = request("/api/adaptation/model-affinity/outcome", "POST", {"model_id": "model-a", "task_key": "scene-intro", "success": True, "quality": 0.9, "latency_ms": 100, "cost": 0.02})
            assert status == 200 and affinity[0]["success_rate"] == 1.0
            status, unauth = request("/api/adaptation/candidates", token=None)
            assert status == 401
            print("adaptation_api: passed; authenticated evidence, consent, lifecycle, evaluation, human promotion, rollback, drift, retirement, model affinity, and auth verified")
        finally:
            stop(process)


if __name__ == "__main__":
    main()
