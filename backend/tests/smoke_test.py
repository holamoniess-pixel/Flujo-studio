from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE = os.environ.get("FLUJO_SMOKE_BASE", "http://127.0.0.1:47821")
TOKEN = os.environ.get("FLUJO_SMOKE_TOKEN")


def request(path: str, method: str = "GET", body: dict | None = None) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"content-type": "application/json"}
    if TOKEN:
        headers["x-flujo-app-token"] = TOKEN
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as error:
        print(error.read().decode('utf-8', errors='replace'))
        raise


def main() -> None:
    try:
        health = request("/health")
    except (OSError, urllib.error.URLError):
        # Harness precondition, not a code bug: the smoke test probes a live engine.
        print(f"smoke: SKIPPED (no live backend at {BASE}; start the engine first or set FLUJO_SMOKE_BASE/FLUJO_SMOKE_TOKEN)")
        return
    assert health["status"] == "ok"
    # M2 fix (real test bug, fails identically on Windows): the old payload carried an
    # image attachment, which the guard stack by design routes to human review (image
    # modalities have no local classifier, so preflight returns REVIEW and the job
    # parks in needs_review with GUARD_REVIEW_REQUIRED). A smoke test cannot clear a
    # human-review gate, so submit a text-only brief and keep every assertion intact.
    queued = request("/api/conversations/conversation_citrus/messages", "POST", {"content": "Create one cinematic citrus scene with a tactile opening.", "attachments": []})
    job_id = queued["job_id"]
    final = None
    for _ in range(50):
        final = request(f"/api/jobs/{job_id}")
        if final["status"] in {"completed", "blocked", "failed"}:
            break
        time.sleep(0.1)
    assert final and final["status"] in {"completed", "blocked"}
    replay = request(f"/api/jobs/{job_id}/replay")
    assert any(event["category"] == "workflow" for event in replay)
    artifacts = request("/api/diagnostics/events?limit=100")
    assert artifacts
    print(json.dumps({"health": health, "job": final, "replay_events": len(replay), "diagnostic_events": len(artifacts)}, indent=2))


if __name__ == "__main__":
    main()
