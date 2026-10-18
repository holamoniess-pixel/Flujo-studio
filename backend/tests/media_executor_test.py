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
PORT = 47824
TOKEN = "media-executor-contract-token"
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = ROOT / "references" / "executor-fixture.mp4"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    req.add_header("content-type", "application/json")
    req.add_header("x-flujo-app-token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> dict:
    for _ in range(60):
        try:
            status, result = request("/health")
            if status == 200:
                return result
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("secure media backend did not become healthy")


def main() -> None:
    if not FIXTURE.is_file():
        # Environment-limited, not a code bug: needs the verified executor fixture
        # plus Windows FFmpeg/executor binaries, none of which exist on Linux.
        print("media_executor: SKIPPED (needs verified executor fixture references/executor-fixture.mp4 and Windows FFmpeg/executor binaries)")
        return
    assert FIXTURE.is_file(), "The verified executor fixture must exist before this test."
    with tempfile.TemporaryDirectory(prefix="flujo-media-executor-") as directory:
        data_root = Path(directory)
        env = os.environ.copy()
        env.update(
            {
                "FLUJO_PROJECT_ROOT": str(ROOT),
                "FLUJO_DATA_DIR": str(data_root / ".flujo"),
                "FLUJO_MEDIA_DIR": str(ROOT / "resources" / "media"),
                "FLUJO_EXECUTOR_PATH": str(ROOT / "resources" / "executor" / "windows-x64" / "FlujoSecureExecutor.exe"),
                "FLUJO_EXECUTOR_TOKEN": "media-executor-parent-token",
                "FLUJO_SECURE_EXECUTOR_ENABLED": "1",
                "FLUJO_PORT": str(PORT),
                "FLUJO_APP_TOKEN": TOKEN,
                "FLUJO_LOG_LEVEL": "warning",
            }
        )
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        output = ROOT / "renders" / "media-executor-contract-thumb.png"
        try:
            health = wait_for_health()
            assert health["media"]["status"] == "healthy", health
            status, probe = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(FIXTURE)], "project_id": "project_northstar", "trace_id": "trace_media_executor_probe"})
            assert status == 200 and probe["status"] == "completed" and probe["resource_usage"]["executor"] == "rust" and probe["provenance"]["project"] == "project_northstar" and probe["provenance"]["operation"] == "probe"
            status, thumbnail = request("/api/media/operation", "POST", {"operation": "thumbnail", "inputs": [str(FIXTURE)], "project_id": "project_northstar", "output": str(output), "options": {"at": 0}, "trace_id": "trace_media_executor_thumbnail"})
            assert status == 200 and thumbnail["status"] == "completed" and output.is_file() and thumbnail["checksum"] and thumbnail["resource_usage"]["qa"]["exists"] and thumbnail["resource_usage"]["qa"]["non_empty"] and thumbnail["resource_usage"]["qa"]["checksum"] == thumbnail["checksum"] and thumbnail["resource_usage"]["output_size_bytes"] > 0 and thumbnail["provenance"]["output"] == [str(output)]
            status, forbidden = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(Path(directory) / "outside.mp4")], "project_id": "project_northstar"})
            assert status == 200 and forbidden["error_code"] == "MEDIA_PATH_FORBIDDEN"
            print(json.dumps({"media_executor": "passed", "probe": probe["status"], "thumbnail": thumbnail["status"], "thumbnail_checksum": thumbnail["checksum"]}))
        finally:
            if output.exists():
                output.unlink()
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
            process.wait(timeout=15)


if __name__ == "__main__":
    main()
