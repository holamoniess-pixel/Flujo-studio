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
TOKEN = "monitor-restart-token"
BASE = f"http://127.0.0.1:{PORT}"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    req.add_header("x-flujo-app-token", TOKEN)
    if payload is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.status, json.loads(response.read().decode())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode())


def start(data_dir: Path) -> subprocess.Popen[bytes]:
    env = os.environ.copy()
    env.update({"FLUJO_PROJECT_ROOT": str(ROOT), "FLUJO_DATA_DIR": str(data_dir), "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(60):
        try:
            if request("/health")[0] == 200:
                return process
        except (OSError, urllib.error.URLError):
            time.sleep(0.2)
    process.kill()
    process.wait(timeout=10)
    raise AssertionError("Monitor restart backend did not start")


def stop(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=15)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-monitor-restart-") as directory:
        data_dir = Path(directory) / ".flujo"
        first = start(data_dir)
        try:
            status, halted = request("/api/monitor/halt", "POST", {"reason": "restart authority check", "actor": "monitor-test-human"})
            assert status == 200 and halted["status"] == "HALTED", halted
            status, denied = request("/api/monitor/resume", "POST", {"actor": "governor", "human_confirmed": False})
            assert status == 403 and denied["detail"]["code"] == "HUMAN_RESUME_REQUIRED", denied
        finally:
            stop(first)
        second = start(data_dir)
        try:
            status, persisted = request("/api/monitor/dashboard")
            assert status == 200 and persisted["state"]["status"] == "HALTED", persisted
            assert persisted["governor_override"] is False and persisted["human_resume_required"] is True
            status, blocked = request("/api/monitor/resume", "POST", {"actor": "governor", "human_confirmed": False})
            assert status == 403 and blocked["detail"]["code"] == "HUMAN_RESUME_REQUIRED", blocked
            status, resumed = request("/api/monitor/resume", "POST", {"actor": "named-human", "human_confirmed": True})
            assert status == 200 and resumed["status"] == "RUNNING", resumed
            print("monitor_restart: passed; halt persistence, dashboard authority, Governor override resistance, and human resume verified")
        finally:
            stop(second)


if __name__ == "__main__":
    main()
