from __future__ import annotations

import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.db import connect, migrate
from backend.app.observability.events import EventSink, set_request_id, set_trace_id


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-observability-") as directory_name:
        root = Path(directory_name)
        db = connect(root / "runtime" / "flujo.sqlite3")
        migrate(db)
        sink = EventSink(db, root / "logs" / "events.jsonl")
        secret = "sk-observability-secret-123456"

        def write_event(index: int) -> None:
            set_trace_id(f"trace-test-{index}")
            set_request_id(f"request-test-{index}")
            sink.audit(category="concurrent", message=f"event {index}", route="/api/test", operation="GET", duration_ms=index, status="200", input_summary={"api_key": secret, "unicode": "Ångström 日本語"})

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(write_event, range(100)))
        try:
            raise RuntimeError(f"failure with token={secret}")
        except RuntimeError as exc:
            sink.error(source="test", message=f"failure with token={secret}", exc=exc, route="/api/test")
        db.close()

        lines = (root / "logs" / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(lines) == 101
        events = [json.loads(line) for line in lines]
        assert all(event.get("trace_id") and event.get("request_id") for event in events)
        assert all(secret not in line for line in lines)
        assert all(event.get("route") == "/api/test" for event in events)
        assert all(event.get("duration_ms") is not None for event in events if event.get("event_type") != "error")
        assert all("stack" not in event or secret not in str(event["stack"]) for event in events)
        print("observability_tests_passed=concurrent_jsonl_redaction_request_ids")


if __name__ == "__main__":
    main()
