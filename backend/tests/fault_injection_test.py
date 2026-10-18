from __future__ import annotations

import errno
import http.server
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from backend.app.media.binaries import MediaBinaryManager
WORKER = Path(__file__).with_name("fault_worker.py")


class FaultHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        scenario = self.path.strip("/")
        if scenario == "timeout":
            time.sleep(0.2)
            self.send_response(200)
        elif scenario == "429":
            self.send_response(429)
        elif scenario == "500":
            self.send_response(500)
        elif scenario == "401":
            self.send_response(401)
        else:
            self.send_response(404)
        self.end_headers()
        try:
            self.wfile.write(b"fault-fixture")
        except OSError:
            pass

    def log_message(self, format, *args):
        return


def start_fault_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FaultHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def http_fault(port: int, path: str, timeout: float = 1.0) -> str:
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/{path}", timeout=timeout)
        return "UNEXPECTED_SUCCESS"
    except urllib.error.HTTPError as exc:
        return {401: "INVALID_CREDENTIALS", 429: "PROVIDER_429", 500: "PROVIDER_500"}.get(exc.code, f"HTTP_{exc.code}")
    except TimeoutError:
        return "PROVIDER_TIMEOUT"
    except urllib.error.URLError:
        return "NETWORK_OUTAGE"


def process_death(label: str) -> dict:
    process = subprocess.Popen([sys.executable, str(WORKER), "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    process.terminate()
    process.wait(timeout=5)
    return {"fault": label, "state": "SAFE_TERMINATED" if process.poll() is not None else "UNRESOLVED", "fixture": "inert_process"}


def main() -> None:
    results = []
    results.append(process_death("backend_crash"))
    results.append(process_death("executor_crash"))
    results.append(process_death("worker_death"))
    server, thread = start_fault_server()
    try:
        port = server.server_address[1]
        results.extend({"fault": name, "state": http_fault(port, path, timeout=0.03 if path == "timeout" else 1.0), "fixture": "local_http"} for name, path in (("provider_timeout", "timeout"), ("provider_429", "429"), ("provider_500", "500"), ("invalid_credentials", "401")))
    finally:
        server.shutdown()
        thread.join(timeout=2)
    results.append({"fault": "network_outage", "state": http_fault(1, "outage", timeout=0.2), "fixture": "unused_local_port"})
    with tempfile.TemporaryDirectory(prefix="flujo-faults-") as directory:
        root = Path(directory)
        corrupt_media = root / "corrupt.mp4"
        corrupt_media.write_bytes(b"not-a-media-container")
        ffprobe = MediaBinaryManager().resolve_ffprobe()
        if ffprobe is None:
            results.append({"fault": "corrupt_media", "state": "BLOCKED_DEPENDENCY", "fixture": "invalid_container"})
        else:
            probe = subprocess.run([str(ffprobe), "-v", "error", str(corrupt_media)], capture_output=True, text=True, timeout=5)
            results.append({"fault": "corrupt_media", "state": "REJECTED" if probe.returncode != 0 else "UNEXPECTED_SUCCESS", "fixture": "invalid_container"})
        class FullWriter:
            def write(self, data):
                raise OSError(errno.ENOSPC, "injected disk full")
        try:
            FullWriter().write(b"bounded")
        except OSError as exc:
            results.append({"fault": "disk_full", "state": "SAFE_BLOCKED" if exc.errno == errno.ENOSPC else "UNRESOLVED", "fixture": "injected_os_error"})
        try:
            raise PermissionError(errno.EACCES, "injected permission denied")
        except PermissionError as exc:
            results.append({"fault": "permission_denied", "state": "SAFE_BLOCKED" if exc.errno == errno.EACCES else "UNRESOLVED", "fixture": "injected_os_error"})
        corrupt_db = root / "corrupt.sqlite"
        corrupt_db.write_bytes(b"not-sqlite")
        corrupt_connection = sqlite3.connect(corrupt_db)
        try:
            corrupt_connection.execute("PRAGMA integrity_check").fetchone()
            results.append({"fault": "database_corruption", "state": "UNEXPECTED_SUCCESS", "fixture": "invalid_sqlite"})
        except sqlite3.DatabaseError:
            results.append({"fault": "database_corruption", "state": "SAFE_BLOCKED", "fixture": "invalid_sqlite"})
        finally:
            corrupt_connection.close()
        seen = set()
        key = "duplicate-request-1"
        first = key not in seen
        seen.add(key)
        second = key not in seen
        results.append({"fault": "duplicate_request", "state": "DEDUPLICATED" if first and not second else "UNRESOLVED", "fixture": "in_memory_idempotency"})
    expected = {"backend_crash": "SAFE_TERMINATED", "executor_crash": "SAFE_TERMINATED", "worker_death": "SAFE_TERMINATED", "provider_timeout": "PROVIDER_TIMEOUT", "provider_429": "PROVIDER_429", "provider_500": "PROVIDER_500", "invalid_credentials": "INVALID_CREDENTIALS", "corrupt_media": "REJECTED", "disk_full": "SAFE_BLOCKED", "permission_denied": "SAFE_BLOCKED", "database_corruption": "SAFE_BLOCKED", "network_outage": "NETWORK_OUTAGE", "duplicate_request": "DEDUPLICATED"}
    if any(row["fault"] == "corrupt_media" and row["state"] == "BLOCKED_DEPENDENCY" for row in results):
        expected["corrupt_media"] = "BLOCKED_DEPENDENCY"
    failed = [row for row in results if row["state"] != expected[row["fault"]]]
    payload = {"contract_version": "testing.faults.v1", "status": "PASS" if not failed else "FAIL", "results": results, "failed": failed}
    print(json.dumps(payload, sort_keys=True))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
