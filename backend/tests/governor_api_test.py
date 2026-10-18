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
PORT = 47825
TOKEN = "governor-contract-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    req.add_header("content-type", "application/json")
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
    raise AssertionError("isolated backend did not become healthy")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-governor-api-") as directory:
        root = Path(directory)
        env = os.environ.copy()
        env.update({"FLUJO_PROJECT_ROOT": str(root), "FLUJO_DATA_DIR": str(root / ".flujo"), "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            wait_for_health()
            status, catalog = request("/api/governor/advisors")
            assert status == 200 and len(catalog) >= 2 and all("reputation" in advisor for advisor in catalog)
            status, decision = request("/api/governor/decide", "POST", {"task": "plan campaign and validate evidence", "budget": 1.0, "max_advisors": 3, "idempotency_key": "api-governor-001"})
            assert status == 200 and decision["contract_version"] == "governor.decision.v1" and decision["status"] == "CONFIDENT"
            assert all(opinion["simulator_only"] is True for opinion in decision["opinions"])
            assert decision["questions"]["should_stop"] is False and decision["model_selection"]["status"] == "SELECTED"
            status, repeated = request("/api/governor/decide", "POST", {"task": "different task must not replace the first", "budget": 1.0, "idempotency_key": "api-governor-001"})
            assert status == 422 and repeated["detail"]["code"] == "IDEMPOTENCY_CONFLICT"
            status, repeated = request("/api/governor/decide", "POST", {"task": "plan campaign and validate evidence", "budget": 1.0, "idempotency_key": "api-governor-001"})
            assert status == 200 and repeated["decision_id"] == decision["decision_id"]
            status, stored = request(f"/api/governor/decisions/{decision['decision_id']}")
            assert status == 200 and stored["status"] == "CONFIDENT" and len(stored["opinions"]) == 3
            status, outcome = request(f"/api/governor/decisions/{decision['decision_id']}/outcome", "POST", {"success": True, "accuracy": 0.9, "task_affinity": 0.8, "agreement": 0.95, "note": "Accepted after human spot-check."})
            assert status == 200 and outcome["success"] is True
            status, critical = request("/api/governor/decide", "POST", {"task": "approve critical operation", "risk_level": "critical", "idempotency_key": "api-governor-critical"})
            assert status == 200 and critical["status"] == "BLOCKED" and critical["questions"]["should_stop"] is True
            status, tool = request("/api/governor/decide", "POST", {"task": "inspect database", "needs_tool": True, "idempotency_key": "api-governor-tool"})
            assert status == 200 and tool["status"] == "BLOCKED" and tool["tool_selection"]["status"] == "BLOCKED"
            status, unauthorized = request("/api/governor/advisors", token=None)
            assert status == 401
            print("governor_api: passed; authenticated contract, simulation labeling, idempotency, decision ledger, outcome, blocked risk/tool states, and auth verified")
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    main()
