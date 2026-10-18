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
PORT = 47826
TOKEN = "governor-restart-token"
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
    with tempfile.TemporaryDirectory(prefix="flujo-governor-restart-") as directory:
        root = Path(directory)
        first = start(root)
        try:
            wait_for_health()
            status, decision = request("/api/governor/decide", "POST", {"task": "persist a restart-safe decision", "idempotency_key": "restart-decision-001"})
            assert status == 200 and decision["status"] == "CONFIDENT"
            status, outcome = request(f"/api/governor/decisions/{decision['decision_id']}/outcome", "POST", {"success": True, "accuracy": 0.95, "task_affinity": 0.9, "agreement": 0.9})
            assert status == 200
        finally:
            stop(first)
        second = start(root)
        try:
            wait_for_health()
            status, restored = request(f"/api/governor/decisions/{decision['decision_id']}")
            assert status == 200 and restored["status"] == "CONFIDENT"
            status, repeated = request("/api/governor/decide", "POST", {"task": "persist a restart-safe decision", "idempotency_key": "restart-decision-001"})
            assert status == 200 and repeated["decision_id"] == decision["decision_id"]
            status, conflict = request("/api/governor/decide", "POST", {"task": "different task cannot reuse persisted key", "idempotency_key": "restart-decision-001"})
            assert status == 422 and conflict["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
            status, catalog = request("/api/governor/advisors")
            assert status == 200 and any(advisor["reputation"]["consultations"] >= 1 for advisor in catalog)
            print("governor_restart: passed; decision ledger, idempotency, advisor reputation, and API health survived backend restart")
        finally:
            stop(second)


if __name__ == "__main__":
    main()
