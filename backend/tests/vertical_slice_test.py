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
PORT = 47824
TOKEN = "slice-contract-token"
BASE = f"http://127.0.0.1:{PORT}"

BRIEF = "A 30 second ad for a mountain bike: open on a rainy forest trail, quick cuts of mud and gears, end on the logo at sunrise."

# Strings from the old hardcoded slice; none may appear in outputs anymore.
BANNED_FIXTURES = (
    "Gesture-first reveal",
    "Warm directional lighting",
    "Direct-response opening hook",
    "Short tactile pacing",
    "Keep tactile opening; protect identity continuity.",
    "Cinematic Food / Tactile Response",
    "Citrus at Dusk / Scene 01",
    "The local vertical slice reached Artifact Lineage",
)


def request(path: str, method: str = "GET", payload: dict | None = None, token: str | None = TOKEN) -> tuple[int, dict]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("content-type", "application/json")
    if token:
        req.add_header("x-flujo-app-token", token)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> dict:
    for _ in range(60):
        try:
            status, body = request("/health", token=None)
            if status == 200:
                return body
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("isolated backend did not become healthy")


def wait_for_job(job_id: str) -> dict:
    for _ in range(150):
        status, job = request(f"/api/jobs/{job_id}")
        assert status == 200, job
        if job["status"] not in {"queued", "running"}:
            return job
        time.sleep(0.2)
    raise AssertionError(f"job {job_id} did not finish")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-slice-contract-") as directory:
        temp_root = Path(directory)
        env = os.environ.copy()
        env.update({
            "FLUJO_PROJECT_ROOT": str(temp_root),
            "FLUJO_DATA_DIR": str(temp_root / ".flujo"),
            "FLUJO_PORT": str(PORT),
            "FLUJO_APP_TOKEN": TOKEN,
            "FLUJO_LOG_LEVEL": "warning",
        })
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            health = wait_for_health()
            assert health["status"] == "ok"
            gateway = health["gateway"]
            assert gateway["simulator_only"] is True, gateway
            assert gateway["default_provider"] == "simulator", gateway
            assert gateway["reason"] == "no provider key configured", gateway

            # 1. Send a real brief through Creative Chat.
            status, queued = request("/api/conversations/conversation_citrus/messages", "POST", {"content": BRIEF, "attachments": [], "idempotency_key": "slice-brief-001"})
            assert status == 202 and queued["cached"] is False, queued
            job = wait_for_job(queued["job_id"])
            # No media binaries in this environment: the honest media gate blocks.
            assert job["status"] == "blocked", job
            assert job["error_code"] == "MEDIA_DEPENDENCY_MISSING", job

            # 2. The assistant reply must be honest and brief-responsive.
            status, conversation = request("/api/conversations/conversation_citrus")
            assert status == 200, conversation
            assistant_messages = [m for m in conversation["messages"] if m["role"] == "assistant" and "mountain bike" in m["content"]]
            assert assistant_messages, "no assistant reply responded to the brief"
            reply = assistant_messages[-1]["content"]
            assert "no provider key is configured" in reply, reply
            assert "labeled simulator fallback" in reply, reply
            for banned in BANNED_FIXTURES:
                assert banned not in reply, f"hardcoded fixture leaked into reply: {banned!r}"
            metadata = json.loads(assistant_messages[-1]["metadata_json"] or "{}")
            assert metadata.get("simulated") is True, metadata

            # 3. Inspect the database directly: nothing fabricated.
            db_path = temp_root / ".flujo" / "runtime" / "flujo.sqlite3"
            db = sqlite3.connect(db_path)
            db.row_factory = sqlite3.Row
            try:
                evidence = [dict(row) for row in db.execute("SELECT claim, model_id, confidence FROM evidence WHERE project_id='project_northstar'").fetchall()]
                # Zero fabricated claims: the simulator path records none.
                assert evidence == [], evidence
                decisions = [dict(row) for row in db.execute("SELECT status, decision, confidence, metadata_json FROM decisions WHERE project_id='project_northstar'").fetchall()]
                assert decisions, "no decision row was recorded"
                meta = json.loads(decisions[-1]["metadata_json"] or "{}")
                assert meta.get("simulated") is True, meta
                assert meta.get("governor_decision_id"), meta
                # The Governor's own ledger must hold the real decision.
                gov = db.execute("SELECT id, status, fused_answer FROM governor_decisions WHERE id=?", (meta["governor_decision_id"],)).fetchone()
                assert gov is not None, "governor decision ledger has no row for the slice decision"
                assert decisions[-1]["status"] == gov["status"], (decisions[-1]["status"], gov["status"])
                consults = db.execute("SELECT COUNT(*) AS n FROM governor_consultations WHERE decision_id=?", (meta["governor_decision_id"],)).fetchone()["n"]
                assert consults >= 1, "no advisor consultations were recorded"
                genomes = [dict(row) for row in db.execute("SELECT name, patterns_json FROM genomes WHERE project_id='project_northstar'").fetchall()]
                assert genomes, "no genome row was recorded"
                patterns = json.loads(genomes[-1]["patterns_json"] or "{}")
                assert all(not items for items in patterns.values()), patterns
                assert "mountain bike" in genomes[-1]["name"].lower() or "bike" in genomes[-1]["name"].lower(), genomes[-1]["name"]
                blueprints = [dict(row) for row in db.execute("SELECT name, scenes_json FROM blueprints WHERE project_id='project_northstar'").fetchall()]
                assert blueprints, "no blueprint row was recorded"
                scenes = json.loads(blueprints[-1]["scenes_json"] or "[]")
                assert scenes and scenes[0]["source"] == "brief-excerpt", scenes
                assert "mountain bike" in scenes[0]["purpose"].lower() or "bike" in scenes[0]["purpose"].lower(), scenes[0]
                for banned in BANNED_FIXTURES:
                    assert banned not in json.dumps(decisions[-1]), f"hardcoded fixture in decisions: {banned!r}"
            finally:
                db.close()

            # 4. Audit trail labels the simulator stages.
            status, replay = request(f"/api/jobs/{queued['job_id']}/replay")
            assert status == 200, replay
            planning = [e for e in replay if e.get("route") == "single-scene" and e.get("operation") == "plan"]
            assert planning and any(e.get("status") == "simulator_only" for e in planning), planning
            analyses = [e for e in replay if e.get("operation") == "analyze"]
            assert analyses and any(e.get("status") == "simulator_only" for e in analyses), analyses

            print("vertical_slice: passed; honest simulator fallback, real governor/memory/lineage, zero fabricated claims")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    main()
