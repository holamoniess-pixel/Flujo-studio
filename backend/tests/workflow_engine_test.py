from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from backend.app.workflows import LocalDurableEngine


def work(value: str) -> str:
    time.sleep(0.05)
    return value


def blocked_work(gate: threading.Event) -> str:
    gate.wait(timeout=2)
    return "released"


engine = LocalDurableEngine(max_workers=1)
gate = threading.Event()
try:
    first = engine.submit("first-job", blocked_work, gate)
    for _ in range(20):
        if first.running():
            break
        time.sleep(0.01)
    second = engine.submit("second-job", work, "cancelled")
    assert engine.cancel("second-job") is True
    assert second.cancelled() is True
    gate.set()
    assert first.result(timeout=2) == "released"
finally:
    engine.shutdown()

engine = LocalDurableEngine(max_workers=2)
try:
    future = engine.submit("test-job", work, "ok")
    assert future.result(timeout=2) == "ok"
finally:
    engine.shutdown()

print("workflow_engine: passed")
