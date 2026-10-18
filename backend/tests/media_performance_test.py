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
TOKEN = "media-performance-token"
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = ROOT / "references" / "executor-fixture.mp4"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method, headers={"content-type": "application/json", "x-flujo-app-token": TOKEN})
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> None:
    for _ in range(80):
        try:
            if request("/health")[0] == 200:
                return
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("performance backend did not become healthy")


def main() -> None:
    if not FIXTURE.is_file():
        # Environment-limited, not a code bug: needs the deterministic MP4 fixture
        # plus Windows FFmpeg/executor binaries, none of which exist on Linux.
        print("media_performance: SKIPPED (needs deterministic fixture references/executor-fixture.mp4 and Windows FFmpeg/executor binaries)")
        return
    assert FIXTURE.is_file()
    with tempfile.TemporaryDirectory(prefix="flujo-media-performance-") as directory:
        data_root = Path(directory)
        env = os.environ.copy()
        env.update({"FLUJO_PROJECT_ROOT": str(ROOT), "FLUJO_DATA_DIR": str(data_root / ".flujo"), "FLUJO_MEDIA_DIR": str(ROOT / "resources" / "media"), "FLUJO_EXECUTOR_PATH": str(ROOT / "resources" / "executor" / "windows-x64" / "FlujoSecureExecutor.exe"), "FLUJO_EXECUTOR_TOKEN": "media-performance-parent-token", "FLUJO_SECURE_EXECUTOR_ENABLED": "1", "FLUJO_PORT": str(PORT), "FLUJO_APP_TOKEN": TOKEN, "FLUJO_LOG_LEVEL": "warning"})
        process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        outputs = [ROOT / "renders" / "performance-thumb.png", ROOT / "renders" / "performance-frame.png", ROOT / "renders" / "performance-transcode.mp4"]
        try:
            wait_for_health()
            cases = [
                ("probe_latency_ms", {"operation": "probe", "inputs": [str(FIXTURE)]}),
                ("thumbnail_latency_ms", {"operation": "thumbnail", "inputs": [str(FIXTURE)], "output": str(outputs[0])}),
                ("frame_extraction_latency_ms", {"operation": "extract_frames", "inputs": [str(FIXTURE)], "output": str(outputs[1]), "options": {"fps": 1}}),
                ("transcode_latency_ms", {"operation": "transcode", "inputs": [str(FIXTURE)], "output": str(outputs[2]), "options": {"video_codec": "mpeg4", "audio_codec": "aac"}}),
            ]
            measurements = {}
            for name, payload in cases:
                started = time.perf_counter()
                status, result = request("/api/media/operation", "POST", {**payload, "project_id": "project_northstar", "trace_id": f"trace-performance-{name}"})
                elapsed = round((time.perf_counter() - started) * 1000, 2)
                assert status == 200 and result.get("status") == "completed", (name, status, result)
                assert result.get("duration_ms", 0) >= 0 and result.get("provenance")
                measurements[name] = {"wall_ms": elapsed, "engine_ms": result["duration_ms"], "checksum": result.get("checksum"), "output_size_bytes": result.get("resource_usage", {}).get("output_size_bytes")}
            render_started = time.perf_counter()
            render_status, render_result = request("/api/media/operation", "POST", {"operation": "render", "inputs": [str(FIXTURE)], "project_id": "project_northstar"})
            measurements["render_latency_ms"] = {"wall_ms": round((time.perf_counter() - render_started) * 1000, 2), "status": render_result.get("status"), "error_code": render_result.get("error_code"), "note": "render is not a typed MediaEngine operation; rejection is expected"}
            assert render_status == 200 and render_result.get("error_code") == "MEDIA_OPERATION_FORBIDDEN"
            print(json.dumps({"media_performance": "passed", "measurements": measurements}, sort_keys=True))
        finally:
            for output in outputs:
                output.unlink(missing_ok=True)
            if process.poll() is None:
                subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if os.name == "nt" else process.terminate()
            process.wait(timeout=20)


if __name__ == "__main__":
    main()
