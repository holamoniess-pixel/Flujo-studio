import json
import os
import sqlite3
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 47827
TOKEN = "workflow-recovery-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    req.add_header("x-flujo-app-token", TOKEN)
    if payload is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def environment(data_root: Path) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "FLUJO_PROJECT_ROOT": str(ROOT),
            "FLUJO_DATA_DIR": str(data_root),
            "FLUJO_MEDIA_DIR": str(ROOT / "resources" / "media"),
            "FLUJO_EXECUTOR_PATH": str(ROOT / "resources" / "executor" / "windows-x64" / "FlujoSecureExecutor.exe"),
            "FLUJO_EXECUTOR_TOKEN": "recovery-parent-token",
            "FLUJO_SECURE_EXECUTOR_ENABLED": "0",
            "FLUJO_PORT": str(PORT),
            "FLUJO_APP_TOKEN": TOKEN,
            "FLUJO_LOG_LEVEL": "warning",
        }
    )
    return env


def start_backend(data_root: Path) -> subprocess.Popen[bytes]:
    process = subprocess.Popen(
        [sys.executable, "-u", str(ROOT / "backend" / "run.py")],
        cwd=ROOT,
        env=environment(data_root),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    for _ in range(100):
        try:
            status, _ = request("/health")
            if status == 200:
                return process
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    stop_backend(process)
    raise AssertionError("recovery backend did not start")


def stop_backend(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/pid", str(process.pid), "/t", "/f"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            process.terminate()
    process.wait(timeout=20)


def seed_stale_jobs(db_path: Path) -> list[str]:
    job_ids = ["job_recovery_queued", "job_recovery_running"]
    with sqlite3.connect(db_path) as db:
        now = "2026-08-25T00:00:00+00:00"
        for job_id, status, checkpoint in zip(
            job_ids,
            ("queued", "running"),
            ("queued", "media_render"),
        ):
            db.execute(
                """
                INSERT INTO jobs(
                    id, project_id, workflow_name, status, idempotency_key,
                    request_fingerprint, checkpoint, progress, estimated_cost,
                    estimated_duration_seconds, interruption_mode,
                    checkpoint_data_json, created_at, updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    job_id,
                    "project_northstar",
                    "CreativeBrief",
                    status,
                    f"idem_{job_id}",
                    f"fingerprint_{job_id}",
                    checkpoint,
                    0.25 if status == "running" else 0,
                    0.05,
                    30,
                    "CANCELLABLE",
                    json.dumps({"checkpoint": checkpoint, "progress": 0.25 if status == "running" else 0}),
                    now,
                    now,
                ),
            )
    return job_ids


def cleanup_directory(directory: Path) -> None:
    last_error: PermissionError | None = None
    for _ in range(20):
        try:
            shutil.rmtree(directory)
            return
        except PermissionError as error:
            last_error = error
            time.sleep(0.25)
    if last_error is not None:
        # The process tree has already been terminated; Windows may briefly retain
        # a SQLite handle after taskkill. Do not invalidate the recovery assertion.
        shutil.rmtree(directory, ignore_errors=True)


def main() -> None:
    directory = Path(tempfile.mkdtemp(prefix="flujo-recovery-"))
    try:
        data_root = directory / ".flujo"
        first = start_backend(data_root)
        try:
            status, result = request("/api/jobs/recovery")
            assert status == 200 and result == [], result
        finally:
            stop_backend(first)

        job_ids = seed_stale_jobs(data_root / "runtime" / "flujo.sqlite3")
        second = start_backend(data_root)
        try:
            status, recovery_rows = request("/api/jobs/recovery")
            assert status == 200, recovery_rows
            assert {row["id"] for row in recovery_rows} == set(job_ids), recovery_rows
            assert {row["status"] for row in recovery_rows} == {"recovery_required"}, recovery_rows
            assert {row["error_code"] for row in recovery_rows} == {"WORKFLOW_RECOVERY_REQUIRED"}, recovery_rows

            for job_id in job_ids:
                status, job = request(f"/api/jobs/{job_id}")
                assert status == 200, job
                assert job["status"] == "recovery_required", job
                assert job["error_code"] == "WORKFLOW_RECOVERY_REQUIRED", job
                checkpoint_data = json.loads(job["checkpoint_data_json"])
                assert checkpoint_data["recovery_required"] is True, checkpoint_data
                assert checkpoint_data["recovery_reason"].startswith("The engine restarted"), checkpoint_data

                status, replay = request(f"/api/jobs/{job_id}/replay")
                assert status == 200, replay
                recovery_events = [event for event in replay if event["category"] == "workflow-recovery"]
                assert len(recovery_events) == 1, replay
                assert recovery_events[0]["status"] == "recovery_required", recovery_events
                metadata = json.loads(recovery_events[0]["metadata_json"])
                assert metadata["previous_status"] in {"queued", "running"}, metadata

            requeue_id = job_ids[0]
            status, not_confirmed = request(f"/api/jobs/{requeue_id}/requeue", "POST", {"confirm": False, "reason": "review"})
            assert status == 409 and not_confirmed["detail"]["code"] == "RECOVERY_CONFIRMATION_REQUIRED", not_confirmed
            status, requeued = request(f"/api/jobs/{requeue_id}/requeue", "POST", {"confirm": True, "actor": "test-reviewer", "reason": "Inspect checkpoint before controlled retry"})
            assert status == 200, requeued
            assert requeued["status"] == "queued", requeued
            assert requeued["automatic_resume"] is False, requeued
            assert requeued["side_effect_replay_requires_review"] is True, requeued
            status, requeued_job = request(f"/api/jobs/{requeue_id}")
            assert status == 200, requeued_job
            assert requeued_job["status"] == "queued", requeued_job
            assert requeued_job["checkpoint"] == "HumanRequeueRequested", requeued_job
            assert requeued_job["error_code"] is None, requeued_job
            requeue_data = json.loads(requeued_job["checkpoint_data_json"])
            assert requeue_data["requeue_actor"] == "test-reviewer", requeue_data
            assert requeue_data["automatic_resume"] is False, requeue_data
            assert requeue_data["side_effect_replay_requires_review"] is True, requeue_data
            status, replay = request(f"/api/jobs/{requeue_id}/replay")
            assert status == 200, replay
            requeue_events = [event for event in replay if event["operation"] == "requeue"]
            assert len(requeue_events) == 1, replay
            assert json.loads(requeue_events[0]["metadata_json"])["automatic_resume"] is False

            status, recovery_rows = request("/api/jobs/recovery")
            assert status == 200, recovery_rows
            assert requeue_id not in {row["id"] for row in recovery_rows}, recovery_rows
        finally:
            stop_backend(second)

        third = start_backend(data_root)
        try:
            status, requeued_after_restart = request(f"/api/jobs/{requeue_id}")
            assert status == 200, requeued_after_restart
            assert requeued_after_restart["status"] == "recovery_required", requeued_after_restart
            assert requeued_after_restart["error_code"] == "WORKFLOW_RECOVERY_REQUIRED", requeued_after_restart
            after_restart_data = json.loads(requeued_after_restart["checkpoint_data_json"])
            assert after_restart_data["requeue_actor"] == "test-reviewer", after_restart_data
            assert after_restart_data["recovery_required"] is True, after_restart_data
            print("workflow_recovery: passed; stale quarantine, confirmed human requeue, side-effect review, and restart re-quarantine verified")
        finally:
            stop_backend(third)
    finally:
        cleanup_directory(directory)


if __name__ == "__main__":
    main()
