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


def request(base: str, path: str, method: str = "GET", payload: object | None = None, token: str | None = "contract-session-token", raw: bytes | None = None, extra_headers: dict[str, str] | None = None) -> tuple[int, dict, dict[str, str]]:
    data = raw if raw is not None else (json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None)
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("content-type", "application/json")
    if token is not None:
        req.add_header("x-flujo-app-token", token)
    for key, value in (extra_headers or {}).items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=8) as response:
            return response.status, json.loads(response.read().decode("utf-8")), dict(response.headers)
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}, dict(error.headers)


def start_backend(directory: Path, token: str, port: int, max_requests: int | None = None) -> tuple[subprocess.Popen[bytes], str]:
    env = os.environ.copy()
    env.update({
        "FLUJO_PROJECT_ROOT": str(directory),
        "FLUJO_DATA_DIR": str(directory / ".flujo"),
        "FLUJO_PORT": str(port),
        "FLUJO_APP_TOKEN": token,
        "FLUJO_LOG_LEVEL": "warning",
    })
    if max_requests is not None:
        env["FLUJO_MAX_REQUESTS_PER_MINUTE"] = str(max_requests)
    process = subprocess.Popen([sys.executable, "-u", str(ROOT / "backend" / "run.py")], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            status, _, _ = request(base, "/health", token=None)
            if status == 200:
                return process, base
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.1)
    process.kill()
    stdout, stderr = process.communicate(timeout=5)
    raise AssertionError(f"backend did not become healthy: {stdout[-1000:]} {stderr[-1000:]}")


def stop_backend(process: subprocess.Popen[bytes]) -> None:
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=8)


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-negative-") as directory_name:
        directory = Path(directory_name)
        old_token = "contract-session-token"
        port = 47823
        process, base = start_backend(directory, old_token, port)
        try:
            status, body, headers = request(base, "/api/installation", extra_headers={"x-flujo-trace-id": "trace-negative-1", "x-flujo-request-id": "request-negative-1"})
            assert status == 200 and headers["x-flujo-trace-id"] == "trace-negative-1" and headers["x-flujo-request-id"] == "request-negative-1"
            assert request(base, "/api/installation", token=None)[0] == 401
            assert request(base, "/api/installation", token="")[0] == 400
            assert request(base, "/api/installation", token="Bearer contract-session-token")[0] == 400
            assert request(base, "/api/installation", token="wrong-token-123")[0] == 401
            replay_a = request(base, "/api/installation", token=old_token)
            replay_b = request(base, "/api/installation", token=old_token)
            assert replay_a[0] == 200 and replay_b[0] == 200

            malformed = request(base, "/api/installation/onboarding", "POST", raw=b"{not-json")
            assert malformed[0] == 422 and malformed[1]["error"]["code"] == "REQUEST_VALIDATION_ERROR"
            unexpected = request(base, "/api/installation/onboarding", "POST", {"completed": True, "unexpected": "reject"})
            assert unexpected[0] == 422 and unexpected[1]["error"]["code"] == "REQUEST_VALIDATION_ERROR"
            null_value = request(base, "/api/installation/onboarding", "POST", {"completed": None})
            assert null_value[0] == 422
            unicode_status, unicode_body, _ = request(base, "/api/gateway/invoke", "POST", {"capability": "vision", "payload": {"nested": {"unicode": "Ångström 日本語"}}, "idempotency_key": "unicode-negative-1"})
            assert unicode_status == 200 and unicode_body["output"]
            invalid_identifier = request(base, "/api/jobs/not%20a%20valid%20id")
            assert invalid_identifier[0] == 422 and invalid_identifier[1]["error"]["code"] == "INVALID_IDENTIFIER"
            invalid_method = request(base, "/api/installation", "PATCH", {})
            assert invalid_method[0] == 405 and invalid_method[1]["error"]["code"] == "METHOD_NOT_ALLOWED"
            oversized = request(base, "/api/installation/onboarding", "POST", raw=b"", extra_headers={"Content-Length": str(4 * 1024 * 1024 + 1)})
            assert oversized[0] == 413 and oversized[1]["error"]["code"] == "REQUEST_BODY_TOO_LARGE"

            status, events, _ = request(base, "/api/diagnostics/events?limit=20")
            assert status == 200
            http_events = [event for event in events if event.get("category") == "http" and event.get("route") == "/api/installation"]
            assert http_events and all(event.get("trace_id") and event.get("request_id") and event.get("operation") and event.get("duration_ms") is not None and event.get("status") for event in http_events[:3])
        finally:
            stop_backend(process)

        process, empty_base = start_backend(directory, "", port + 1)
        try:
            empty_status, empty_body, _ = request(empty_base, "/api/installation", token=None)
            assert empty_status == 503 and empty_body["error"]["code"] == "LOCAL_SESSION_TOKEN_UNCONFIGURED"
        finally:
            stop_backend(process)

        process, rate_base = start_backend(directory, "rate-session-token", port + 2, max_requests=3)
        try:
            assert request(rate_base, "/api/installation", token="rate-session-token")[0] == 200
            assert request(rate_base, "/api/installation", token="rate-session-token")[0] == 200
            assert request(rate_base, "/api/installation", token="rate-session-token")[0] == 200
            rate_status, rate_body, _ = request(rate_base, "/api/installation", token="rate-session-token")
            assert rate_status == 429 and rate_body["error"]["code"] == "RATE_LIMITED"
        finally:
            stop_backend(process)

        corrupt_directory = directory / "corrupt-startup"
        (corrupt_directory / ".flujo" / "runtime").mkdir(parents=True, exist_ok=True)
        (corrupt_directory / ".flujo" / "runtime" / "flujo.sqlite3").write_bytes(b"corrupt sqlite state")
        process, corrupt_base = start_backend(corrupt_directory, "corrupt-session-token", port + 3)
        try:
            corrupt_health, _, _ = request(corrupt_base, "/health", token=None)
            assert corrupt_health == 200
            quarantined = list((corrupt_directory / ".flujo" / "runtime").glob("flujo.sqlite3.corrupt-*"))
            assert quarantined
        finally:
            stop_backend(process)

        new_token = "restart-session-token"
        process, base = start_backend(directory, new_token, port)
        try:
            assert request(base, "/api/installation", token=old_token)[0] == 401
            assert request(base, "/api/installation", token=new_token)[0] == 200
        finally:
            stop_backend(process)
        print(json.dumps({"auth_states": True, "empty_server_token_blocked": True, "rate_limited": True, "corruption_recovered": True, "replay_within_session": True, "restart_token_rotation": True, "validation_states": True, "invalid_method": True, "unicode_and_nested_payload": True, "oversized_body": True, "observability_fields": True}))


if __name__ == "__main__":
    main()
