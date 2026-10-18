from __future__ import annotations

import hashlib
import json
import re
import traceback
import threading
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..db import dump_json

_TRACE_ID: ContextVar[str | None] = ContextVar("trace_id", default=None)
_REQUEST_ID: ContextVar[str | None] = ContextVar("request_id", default=None)
_SECRET_KEY = re.compile(r"(api[_-]?key|authorization|token|secret|password|credential|private[_-]?key)", re.I)
_SECRET_VALUE = re.compile(r"(?i)(bearer\s+|sk-|nv_|AIza|key=)[A-Za-z0-9_\-./+=]{8,}")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def current_trace_id() -> str:
    value = _TRACE_ID.get()
    if value:
        return value
    value = new_id("trace")
    _TRACE_ID.set(value)
    return value


def set_trace_id(value: str | None = None) -> str:
    trace = value or new_id("trace")
    _TRACE_ID.set(trace)
    return trace


def current_request_id() -> str:
    value = _REQUEST_ID.get()
    if value:
        return value
    value = new_id("request")
    _REQUEST_ID.set(value)
    return value


def set_request_id(value: str | None = None) -> str:
    request_id = value or new_id("request")
    _REQUEST_ID.set(request_id)
    return request_id


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "[depth-limited]"
    if isinstance(value, dict):
        return {key: "[REDACTED]" if _SECRET_KEY.search(str(key)) else redact(item, depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item, depth + 1) for item in value[:100]]
    if isinstance(value, tuple):
        return [redact(item, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        return _SECRET_VALUE.sub("[REDACTED]", value[:10000])
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:1000]


def summary(value: Any) -> str:
    return dump_json(redact(value))


def event_id(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


class EventSink:
    def __init__(self, connection, log_path: Path):
        self.connection = connection
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file_lock = threading.Lock()

    def _write_jsonl(self, event: dict[str, Any]) -> None:
        line = json.dumps(redact(event), ensure_ascii=False, separators=(",", ":")) + "\n"
        with self._file_lock:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(line)

    def audit(self, *, severity: str = "INFO", category: str, message: str, status: str = "ok", project_id: str | None = None, workflow_id: str | None = None, job_id: str | None = None, artifact_id: str | None = None, attempt_id: str | None = None, decision_id: str | None = None, route: str | None = None, operation: str | None = None, duration_ms: int | None = None, input_summary: Any = None, output_summary: Any = None, metadata: dict[str, Any] | None = None) -> str:
        safe_message = str(redact(message))[:1000]
        event = {
            "id": new_id("evt"), "timestamp": now_iso(), "severity": severity, "category": category, "message": safe_message,
            "trace_id": current_trace_id(), "request_id": current_request_id(), "project_id": project_id, "workflow_id": workflow_id, "job_id": job_id,
            "artifact_id": artifact_id, "attempt_id": attempt_id, "decision_id": decision_id, "route": route,
            "operation": operation, "duration_ms": duration_ms, "status": status, "input_summary": summary(input_summary) if input_summary is not None else None,
            "output_summary": summary(output_summary) if output_summary is not None else None, "metadata_json": summary(metadata or {}),
        }
        self.connection.execute("INSERT INTO audit_events(id,timestamp,severity,category,message,trace_id,request_id,project_id,workflow_id,job_id,artifact_id,attempt_id,decision_id,route,operation,duration_ms,status,input_summary,output_summary,metadata_json) VALUES(:id,:timestamp,:severity,:category,:message,:trace_id,:request_id,:project_id,:workflow_id,:job_id,:artifact_id,:attempt_id,:decision_id,:route,:operation,:duration_ms,:status,:input_summary,:output_summary,:metadata_json)", event)
        self._write_jsonl(event)
        return event["id"]

    def error(self, *, source: str, message: str, exc: BaseException | None = None, severity: str = "ERROR", project_id: str | None = None, workflow_id: str | None = None, job_id: str | None = None, route: str | None = None, metadata: dict[str, Any] | None = None) -> str:
        safe_message = str(redact(message))[:1000]
        safe_stack = str(redact(traceback.format_exc()))[:10000] if exc else None
        error = {"id": new_id("err"), "timestamp": now_iso(), "severity": severity, "source": source, "message": safe_message, "stack": safe_stack, "trace_id": current_trace_id(), "request_id": current_request_id(), "project_id": project_id, "workflow_id": workflow_id, "job_id": job_id, "route": route, "metadata_json": summary(metadata or {})}
        self.connection.execute("INSERT INTO error_events(id,timestamp,severity,source,message,stack,trace_id,request_id,project_id,workflow_id,job_id,route,metadata_json) VALUES(:id,:timestamp,:severity,:source,:message,:stack,:trace_id,:request_id,:project_id,:workflow_id,:job_id,:route,:metadata_json)", error)
        self._write_jsonl({"event_type": "error", **error})
        return error["id"]
