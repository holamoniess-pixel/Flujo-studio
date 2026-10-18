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
PORT = 47828
TOKEN = "adaptation-restart-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method, headers={"content-type": "application/json", "x-flujo-app-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> None:
    for _ in range(40):
        try:
            if request("/health")[0] == 200:
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


def main() -> None:
    cases = [{"kind": kind, "case_id": kind, "passed": True} for kind in ("golden", "regression", "adversarial", "fresh_session")]
    with tempfile.TemporaryDirectory(prefix="flujo-adaptation-restart-") as directory:
        root = Path(directory)
        first = start(root)
        try:
            wait_for_health()
            payload = {"project_id": "project_northstar", "task": "restart-safe learning", "feedback": "quality improved", "candidate_type": "skill", "scope": "PROJECT", "proposal": {"rule": "retain verified hook"}, "evidence_ids": ["restart-e1"]}
            status, candidate = request("/api/adaptation/candidates", "POST", payload)
            assert status == 200
            candidate_id = candidate["id"]
            assert request(f"/api/adaptation/candidates/{candidate_id}/compile", "POST", {})[0] == 200
            assert request(f"/api/adaptation/candidates/{candidate_id}/evaluate", "POST", {"cases": cases})[0] == 200
            status, promoted = request(f"/api/adaptation/candidates/{candidate_id}/promote", "POST", {"reviewer_id": "restart-human", "reason": "All restart fixture gates passed."})
            assert status == 200 and promoted["current_version"] == 1
        finally:
            stop(first)
        second = start(root)
        try:
            wait_for_health()
            status, restored = request(f"/api/adaptation/candidates/{candidate_id}")
            assert status == 200 and restored["status"] == "ACTIVE" and restored["current_version"] == 1
            print("adaptation_restart: passed; candidate compilation, evaluation, human promotion, version, and active state survived backend restart")
        finally:
            stop(second)


if __name__ == "__main__":
    main()
