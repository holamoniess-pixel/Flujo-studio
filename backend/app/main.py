from __future__ import annotations

import gc
import hashlib
import hmac
import json
import os
import re
from collections import defaultdict, deque
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from .adaptation import AdaptationBlocked, AdaptationController, AdaptationRequest
from .constitution import as_dict as constitution
from .db import SCHEMA_VERSION, connect, dump_json, integrity_check, migrate, rows_to_dicts
from .doctor import build_health_payload, list_incidents, recommendations, record_incidents
from .gateway import GatewayError, ModelGateway, SimulatorProvider
from .gateway.meta_provider import MetaModelProvider
from .gateway.nvidia_provider import DEFAULT_BASE_URL as NVIDIA_DEFAULT_BASE_URL
from .gateway.nvidia_provider import DEFAULT_MODEL_ID as NVIDIA_DEFAULT_MODEL_ID
from .gateway.nvidia_provider import NvidiaModelProvider
from .gateway.openai_provider import DEFAULT_BASE_URL as OPENAI_DEFAULT_BASE_URL
from .gateway.openai_provider import DEFAULT_MODEL_ID as OPENAI_DEFAULT_MODEL_ID
from .gateway.openai_provider import OpenAICompatibleProvider
from .gateway.zen_provider import DEFAULT_BASE_URL as ZEN_DEFAULT_BASE_URL
from .gateway.zen_provider import DEFAULT_MODEL_ID as ZEN_DEFAULT_MODEL_ID
from .gateway.zen_provider import OpenCodeZenProvider
from .guard import FlujoGuard
from .governor import AdvisorDescriptor, GovernorBlocked, GovernorController, GovernorRequest
from .governor.live_consult import gateway_consult
from .media.binaries import MediaBinaryManager
from .media.engine import MediaEngine
from .memory import MemoryBlocked, MemoryController, MemoryInput
from .monitor import MonitorBlocked, MonitorController
from .provenance import ProvenanceBlocked, ProvenanceController
from .publishing import PublishAdapterRegistry, PublishError, PublishPrepPipeline, YouTubePublishAdapter
from .repositories import SQLiteRepositoryBundle
from .observability.events import EventSink, current_request_id, current_trace_id, new_id, now_iso, set_request_id, set_trace_id
from .security import CredentialMetadata, CredentialStore, CredentialStoreError
from .workflows import LocalDurableEngine, WorkflowCancelled

ROOT = Path(os.environ.get("FLUJO_PROJECT_ROOT", Path(__file__).resolve().parents[2]))
DATA_DIR = Path(os.environ.get("FLUJO_DATA_DIR", ROOT / ".flujo"))
DB_PATH = DATA_DIR / "runtime" / "flujo.sqlite3"
LOG_PATH = DATA_DIR / "logs" / "events.jsonl"
APP_TOKEN = os.environ.get("FLUJO_APP_TOKEN", "")
REFERENCES_DIR = ROOT / "references"
ARTIFACTS_DIR = ROOT / "generated"
MAX_REQUEST_BODY_BYTES = 4 * 1024 * 1024
MAX_QUERY_LIMIT = 500
try:
    MAX_REQUESTS_PER_MINUTE = max(1, min(int(os.environ.get("FLUJO_MAX_REQUESTS_PER_MINUTE", "600")), 10000))
except ValueError:
    MAX_REQUESTS_PER_MINUTE = 600
ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$")
AUTH_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{8,512}$")
_CONTEXT_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")
_RATE_BUCKETS: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = threading.Lock()
for directory in (DATA_DIR, DATA_DIR / "runtime", DATA_DIR / "logs", DATA_DIR / "memory", DATA_DIR / "evidence", REFERENCES_DIR, ARTIFACTS_DIR):
    directory.mkdir(parents=True, exist_ok=True)

DB_RECOVERED_FROM_CORRUPTION = False


def quarantine_database_files() -> None:
    quarantine_path = DB_PATH.with_name(f"{DB_PATH.name}.corrupt-{int(time.time())}")
    for candidate in (DB_PATH, Path(f"{DB_PATH}-wal"), Path(f"{DB_PATH}-shm")):
        if candidate.exists():
            target = quarantine_path.with_name(quarantine_path.name + candidate.suffix)
            for attempt in range(10):
                try:
                    candidate.replace(target)
                    break
                except PermissionError:
                    if attempt == 9:
                        raise
                    time.sleep(0.05)


def database_file_healthy() -> bool:
    if not DB_PATH.exists():
        return True
    probe = sqlite3.connect(DB_PATH, timeout=5)
    try:
        return probe.execute("PRAGMA quick_check").fetchone()[0] == "ok"
    except sqlite3.DatabaseError:
        return False
    finally:
        probe.close()


def open_database():
    global DB_RECOVERED_FROM_CORRUPTION
    if not database_file_healthy():
        gc.collect()
        quarantine_database_files()
        DB_RECOVERED_FROM_CORRUPTION = True
    database = connect(DB_PATH)
    try:
        migrate(database)
        return database
    except sqlite3.DatabaseError:
        database.close()
        del database
        gc.collect()
        quarantine_database_files()
        DB_RECOVERED_FROM_CORRUPTION = True
        recovered = connect(DB_PATH)
        migrate(recovered)
        return recovered


DB = open_database()
REPOSITORIES = SQLiteRepositoryBundle.from_connection(DB)
EVENTS = EventSink(DB, LOG_PATH)
if DB_RECOVERED_FROM_CORRUPTION:
    EVENTS.audit(category="database", message="Corrupt SQLite state was quarantined and a fresh local database was initialized", route="startup", operation="recover", status="recovered", metadata={"database": str(DB_PATH)})
WORKERS = LocalDurableEngine(max_workers=2)
LOCK = threading.Lock()
MEDIA = MediaBinaryManager()
MEDIA_ENGINE = MediaEngine(ROOT, MEDIA)
GATEWAY = ModelGateway(providers=[SimulatorProvider()])
# Production provider pool: the labeled simulator fallback plus credential-gated
# live adapters. NVIDIA is the default live provider (registered first among
# live adapters and highest priority). Meta, OpenAI-compatible, and OpenCode
# Zen stay registered and selectable. Each adapter advertises zero models until
# its credential resolves, so gateway.health() stays honestly simulator-only
# (provider "simulator", reason "no provider key configured") until a key is
# stored. Nothing here ever simulates silently.


def _resolve_nvidia_credential() -> str | None:
    """Call-time resolver for the NVIDIA adapter: opaque credential reference.

    Looks up the active credential reference for provider "nvidia" on each call
    and resolves the raw secret from the managed blob store. Returns None when
    no reference exists or resolution fails, keeping the adapter dormant and
    the gateway on the labeled simulator fallback. The raw secret is never
    persisted or logged here.
    """
    try:
        row = DB.execute("SELECT * FROM credentials WHERE provider=? AND status='active' ORDER BY updated_at DESC LIMIT 1", ("nvidia",)).fetchone()
        if row is None:
            return None
        return CREDENTIALS.read(_credential_from_row(row))
    except Exception:
        return None


def _nvidia_base_url() -> str:
    configured = (os.environ.get("NVIDIA_BASE_URL", "") or "").strip()
    return configured or NVIDIA_DEFAULT_BASE_URL


def _nvidia_model_id() -> str:
    configured = (os.environ.get("NVIDIA_MODEL_ID", "") or "").strip()
    return configured or NVIDIA_DEFAULT_MODEL_ID


try:
    import urllib.parse as _urlparse

    _nvidia_host = (_urlparse.urlparse(_nvidia_base_url()).hostname or "").lower()
    _nvidia_provider = NvidiaModelProvider(
        credential_resolver=_resolve_nvidia_credential,
        base_url=_nvidia_base_url(),
        model_id=_nvidia_model_id(),
        allowed_hosts={_nvidia_host} if _nvidia_host else frozenset(),
    )
    # NVIDIA is the default live provider: first among live adapters in the pool.
    GATEWAY.providers.insert(1, _nvidia_provider)
except GatewayError:
    pass
CREDENTIALS = CredentialStore(DATA_DIR / "runtime" / "credentials")


def _resolve_meta_credential() -> str | None:
    """Call-time resolver for the Meta adapter: opaque credential reference.

    Looks up the active credential reference for provider "meta" on each call
    and resolves the raw secret from the managed blob store. Returns None when
    no reference exists or resolution fails, keeping the gateway on the
    simulator. The raw secret is never persisted or logged here.
    """
    try:
        row = DB.execute("SELECT * FROM credentials WHERE provider=? AND status='active' ORDER BY updated_at DESC LIMIT 1", ("meta",)).fetchone()
        if row is None:
            return None
        return CREDENTIALS.read(_credential_from_row(row))
    except Exception:
        return None


try:
    GATEWAY.providers.append(MetaModelProvider(credential_resolver=_resolve_meta_credential))
except GatewayError:
    pass


def _resolve_openai_credential() -> str | None:
    """Call-time resolver for the generic OpenAI-compatible adapter.

    Looks up the active credential reference for provider "openai-compatible"
    (falling back to "openai") on each call and resolves the raw secret from
    the managed blob store. Returns None when no reference exists, keeping
    the adapter dormant and the gateway on honest simulator health.
    The raw secret is never persisted or logged here.
    """
    try:
        for provider_key in ("openai-compatible", "openai"):
            row = DB.execute("SELECT * FROM credentials WHERE provider=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (provider_key,)).fetchone()
            if row is not None:
                return CREDENTIALS.read(_credential_from_row(row))
        return None
    except Exception:
        return None


def _openai_base_url() -> str:
    configured = (os.environ.get("OPENAI_COMPATIBLE_BASE_URL", "") or "").strip()
    return configured or OPENAI_DEFAULT_BASE_URL


def _openai_model_id() -> str:
    configured = (os.environ.get("OPENAI_COMPATIBLE_MODEL_ID", "") or "").strip()
    return configured or OPENAI_DEFAULT_MODEL_ID


try:
    import urllib.parse as _urlparse

    _openai_host = (_urlparse.urlparse(_openai_base_url()).hostname or "").lower()
    GATEWAY.providers.append(
        OpenAICompatibleProvider(
            credential_resolver=_resolve_openai_credential,
            base_url=_openai_base_url(),
            model_id=_openai_model_id(),
            allowed_hosts={_openai_host} if _openai_host else frozenset(),
        )
    )
except GatewayError:
    pass


def _resolve_zen_credential() -> str | None:
    """Call-time resolver for the OpenCode Zen adapter.

    Looks up the active credential reference for provider "opencode-zen"
    (falling back to "opencode") on each call and resolves the raw secret from
    the managed blob store. Returns None when no reference exists, keeping the
    adapter dormant for paid models (free ids may still be attempted keyless;
    OpenCode's server decides). The raw secret is never persisted or logged here.
    """
    try:
        for provider_key in ("opencode-zen", "opencode"):
            row = DB.execute("SELECT * FROM credentials WHERE provider=? AND status='active' ORDER BY updated_at DESC LIMIT 1", (provider_key,)).fetchone()
            if row is not None:
                return CREDENTIALS.read(_credential_from_row(row))
        return None
    except Exception:
        return None


def _zen_base_url() -> str:
    configured = (os.environ.get("OPENCODE_ZEN_BASE_URL", "") or "").strip()
    return configured or ZEN_DEFAULT_BASE_URL


def _zen_model_id() -> str:
    configured = (os.environ.get("OPENCODE_ZEN_MODEL_ID", "") or "").strip()
    return configured or ZEN_DEFAULT_MODEL_ID


try:
    import urllib.parse as _urlparse

    _zen_host = (_urlparse.urlparse(_zen_base_url()).hostname or "").lower()
    _zen_provider = OpenCodeZenProvider(
        credential_resolver=_resolve_zen_credential,
        base_url=_zen_base_url(),
        model_id=_zen_model_id(),
        allowed_hosts={_zen_host} if _zen_host else frozenset(),
    )
    # Zen stays registered and selectable; NVIDIA (inserted earlier) is the
    # default live provider.
    GATEWAY.providers.insert(2, _zen_provider)
    try:
        # Best-effort live /models discovery (short timeout, failures
        # swallowed): the hardcoded 87-model catalog remains the baseline.
        _zen_provider.refresh_catalog()
    except Exception:
        pass
except GatewayError:
    pass


def _gateway_credentialed() -> bool:
    """App-owned credential probe for the gateway.

    True when ANY provider credential reference resolves from the local vault.
    Adapters that advertise keyless models without a credential do not count:
    the gateway reports simulator_only until a real credential exists.
    """
    for resolver in (_resolve_nvidia_credential, _resolve_meta_credential, _resolve_openai_credential, _resolve_zen_credential):
        try:
            if resolver():
                return True
        except Exception:
            continue
    return False


GATEWAY.set_credential_probe(_gateway_credentialed)


def _resolve_youtube_credential() -> str | None:
    """Call-time resolver for the YouTube publish adapter.

    Looks up the active credential reference for provider "youtube" and
    resolves the raw secret from the managed blob store. Returns None when
    no reference exists or resolution fails, keeping the adapter in its
    honest "not configured" state. The raw secret is never persisted or
    logged here.
    """
    try:
        row = DB.execute("SELECT * FROM credentials WHERE provider=? AND status='active' ORDER BY updated_at DESC LIMIT 1", ("youtube",)).fetchone()
        if row is None:
            return None
        return CREDENTIALS.read(_credential_from_row(row))
    except Exception:
        return None


GUARD = FlujoGuard()
MONITOR = MonitorController(DB, EVENTS)
GOVERNOR = GovernorController(DB, EVENTS, GATEWAY)
ADAPTATION = AdaptationController(DB, EVENTS)
MEMORY = MemoryController(DB, EVENTS)
PROVENANCE = ProvenanceController(DB, EVENTS)


def _publish_media_run(operation: str, inputs: list[Path], output: Path | None, options: dict[str, Any] | None = None) -> Any:
    """Route prep media steps through the real MediaEngine boundary."""
    return MEDIA_ENGINE.run(operation, inputs, output, options or {})


PUBLISH_ADAPTERS = PublishAdapterRegistry()
# Registered enabled (like the gateway providers) but unconfigured: no
# credential reference exists in the local vault, so the adapter stays in
# its honest "not configured" state until the user stores one.
PUBLISH_ADAPTERS.register(YouTubePublishAdapter(credential_resolver=_resolve_youtube_credential, enabled=True))
PUBLISH_BUNDLE_ROOT = ARTIFACTS_DIR / "publish"
def _extract_json_object(text: str) -> dict[str, Any] | None:
    """Extract the first top-level JSON object embedded in free text."""
    if not isinstance(text, str) or "{" not in text:
        return None
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            return obj
        start = text.find("{", start + 1)
    return None


def _prep_caption_generate(title: str, description: str) -> dict[str, Any] | None:
    """Model-backed tag/caption generation for the publish prep pipeline.

    Returns None when no provider credential is configured (the pipeline then
    uses the deterministic local fallback and labels it as such), or when the
    model call fails for any reason. Never raises into the prep pipeline, and
    never invents a credential.
    """
    try:
        state = _slice_gateway_state()
    except Exception:
        return None
    if state.get("simulated"):
        return None
    prompt = (
        "Generate YouTube upload metadata for the video described below. Respond with JSON only, no prose "
        "outside the JSON: {\"tags\": [\"<tag>\", ...], \"captions\": [\"<caption line>\", ...]}. "
        "Provide up to 12 tags (short phrases) and up to 12 caption lines in a natural spoken style. "
        f"Title: {title[:200]}\nDescription: {description[:2000]}"
    )
    try:
        result = GATEWAY.invoke("text_generation", {"prompt": prompt}, f"prep-captions:{hashlib.sha256((title + description).encode()).hexdigest()[:16]}")
    except Exception:
        return None
    output = result.output if isinstance(result.output, dict) else {}
    if output.get("simulator"):
        return None
    obj = _extract_json_object(str(output.get("text", "") or ""))
    if not obj:
        return None
    tags = [str(tag)[:40] for tag in obj.get("tags", []) if str(tag).strip()][:15]
    caption_lines = [str(line)[:200] for line in obj.get("captions", []) if str(line).strip()][:40]
    if not tags and not caption_lines:
        return None
    return {"tags": tags, "caption_lines": caption_lines, "provider": result.provider, "model_id": result.model_id}


PUBLISH_PIPELINE = PublishPrepPipeline(guard=GUARD, media_run=_publish_media_run, bundle_root=PUBLISH_BUNDLE_ROOT, caption_generate=_prep_caption_generate)

app = FastAPI(title="Flujo Studio Local Engine Room", version="0.2.0")
app.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1", "http://localhost", "http://127.0.0.1:5173", "http://localhost:5173"], allow_credentials=False, allow_methods=["GET", "POST", "PUT", "PATCH", "OPTIONS"], allow_headers=["content-type", "x-flujo-trace-id", "x-flujo-request-id", "x-flujo-app-token"])


def safe_context_value(value: str | None, prefix: str) -> str:
    if value and _CONTEXT_PATTERN.fullmatch(value):
        return value
    return new_id(prefix)


def rate_allowed(request: Request) -> bool:
    key = request.client.host if request.client else "local"
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE_BUCKETS[key]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
            return False
        bucket.append(now)
        return True


def error_code_from_response(response: JSONResponse) -> str | None:
    try:
        body = json.loads(response.body.decode("utf-8")) if response.body else {}
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    error = body.get("error") if isinstance(body, dict) else None
    if isinstance(error, dict) and isinstance(error.get("code"), str):
        return error["code"]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict) and isinstance(detail.get("code"), str):
        return detail["code"]
    return None


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AttachmentIn(StrictModel):
    label: str
    kind: str = "file"
    role: str | None = None
    filename: str | None = None
    content_hash: str | None = None
    local_path: str | None = None


class MessageIn(StrictModel):
    content: str = Field(min_length=1, max_length=20000)
    attachments: list[AttachmentIn] = Field(default_factory=list, max_length=16)
    project_id: str = "project_northstar"
    campaign_id: str = "campaign_citrus"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class ModelInvokeIn(StrictModel):
    capability: str = Field(min_length=1, max_length=80)
    payload: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=1, max_length=200)
    project_id: str = "project_northstar"


class MediaOperationIn(StrictModel):
    operation: str = Field(min_length=1, max_length=80)
    inputs: list[str] = Field(min_length=1, max_length=16)
    project_id: str = "project_northstar"
    output: str | None = None
    options: dict[str, Any] = Field(default_factory=dict)
    trace_id: str | None = None
    timeout_seconds: int = Field(default=300, ge=1, le=1800)


class CredentialIn(StrictModel):
    provider: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=120)
    secret: str = Field(min_length=1, max_length=400)
    scope: str = Field(default="local", min_length=1, max_length=120)


class OnboardingIn(StrictModel):
    completed: bool = False
    skipped: bool = False


class RecoveryRequeueIn(StrictModel):
    confirm: bool = False
    actor: str = Field(default="local-user", min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


class ProviderIn(StrictModel):
    name: str
    api_key: str = Field(min_length=1, max_length=400)


class ConsentIn(StrictModel):
    learning_scope: str = Field(default="PROJECT_ONLY", pattern="^(PROJECT_ONLY|USER_ONLY|GLOBAL_ALLOWED)$")
    face_consent: bool = False
    likeness_consent: bool = False
    voice_consent: bool = False
    identity_source: str = Field(default="UNSPECIFIED", min_length=1, max_length=120)
    consent_state: str = Field(default="PENDING", pattern="^(PENDING|GRANTED|REVOKED)$")
    project_scope: str = Field(default="PROJECT_ONLY", pattern="^(PROJECT_ONLY|USER_ONLY|GLOBAL_ALLOWED)$")
    retention_days: int = Field(default=30, ge=1, le=3650)


class GuardReviewDecisionIn(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=120)
    decision: str = Field(pattern="^(APPROVE|REJECT)$")
    note: str = Field(min_length=1, max_length=2000)


class ArtifactPublishIn(StrictModel):
    project_id: str = "project_northstar"
    modality: str = Field(default="document", pattern="^(text|image|video|audio|ocr|metadata|document)$")


class PublishPrepStartIn(StrictModel):
    artifact_id: str
    platform: str = Field(default="youtube", pattern="^[a-z0-9][a-z0-9_-]{0,79}$")
    modality: str = Field(default="document", pattern="^(text|image|video|audio|ocr|metadata|document)$")
    title: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=2000)
    tags: list[str] | None = Field(default=None, max_length=15)
    privacy: str = Field(default="unlisted", pattern="^(unlisted|private|public)$")


class GovernorDecideIn(StrictModel):
    task: str = Field(min_length=1, max_length=20000)
    required_capability: str = Field(default="text_generation", min_length=1, max_length=80)
    context: dict[str, Any] = Field(default_factory=dict)
    budget: float = Field(default=1.0, ge=0, le=100)
    risk_level: str = Field(default="medium", pattern="^(low|medium|high|critical)$")
    max_advisors: int = Field(default=3, ge=1, le=8)
    retry_budget: int = Field(default=1, ge=0, le=2)
    needs_tool: bool = False
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=200)


class GovernorOutcomeIn(StrictModel):
    success: bool
    accuracy: float = Field(ge=0, le=1)
    task_affinity: float = Field(ge=0, le=1)
    agreement: float = Field(ge=0, le=1)
    note: str = Field(default="", max_length=2000)


class AdaptationExperienceIn(StrictModel):
    project_id: str = "project_northstar"
    task: str = Field(min_length=1, max_length=10000)
    feedback: str = Field(min_length=1, max_length=10000)
    candidate_type: str = Field(default="prompt", pattern="^(prompt|skill|routing|tool|recipe|parameter)$")
    scope: str = Field(default="PROJECT", pattern="^(TASK|PROJECT|USER|GLOBAL)$")
    user_id: str | None = Field(default=None, max_length=120)
    model_id: str | None = Field(default=None, max_length=200)
    provider_id: str | None = Field(default=None, max_length=200)
    tool_name: str | None = Field(default=None, max_length=200)
    recipe_name: str | None = Field(default=None, max_length=200)
    outcome: str = Field(default="", max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    proposal: dict[str, Any] = Field(default_factory=dict)
    consent: dict[str, Any] = Field(default_factory=dict)
    experience_id: str | None = Field(default=None, max_length=200)


class AdaptationFeedbackIn(StrictModel):
    outcome: str = Field(min_length=1, max_length=2000)
    quality: float = Field(ge=0, le=1)
    attribution: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)


class AdaptationEvaluateIn(StrictModel):
    cases: list[dict[str, Any]] = Field(min_length=1, max_length=200)


class AdaptationReviewIn(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    target_version: int | None = Field(default=None, ge=1, le=1000)
    consent: dict[str, Any] = Field(default_factory=dict)


class AdaptationDriftIn(StrictModel):
    observed_score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    evidence: list[str] = Field(min_length=1, max_length=100)
    reviewer_id: str | None = Field(default=None, max_length=120)


class AdaptationModelOutcomeIn(StrictModel):
    model_id: str = Field(min_length=1, max_length=200)
    task_key: str = Field(min_length=1, max_length=200)
    success: bool
    quality: float = Field(ge=0, le=1)
    latency_ms: int = Field(ge=0, le=3600000)
    cost: float = Field(ge=0, le=100000)


class MemoryRecordIn(StrictModel):
    project_id: str = "project_northstar"
    domain: str = Field(default="PROJECT", pattern="^(FLUJO|PROJECT|CONVERSATION|USER_PREFERENCE|OBSIDIAN|REFERENCE|SKILL|DECISION)$")
    scope: str = Field(default="PROJECT", pattern="^(TASK|PROJECT|CONVERSATION|USER|GLOBAL)$")
    content: str = Field(min_length=1, max_length=50000)
    source: str = Field(default="USER", pattern="^(USER|SYSTEM|REFERENCE|OBSIDIAN|SKILL|DECISION|MODEL|TOOL|IMPORT)$")
    confidence: float = Field(default=0.5, ge=0, le=1)
    provenance: dict[str, Any] = Field(default_factory=dict)
    permission: dict[str, Any] = Field(default_factory=dict)
    title: str = Field(default="", max_length=500)
    user_id: str | None = Field(default=None, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=200)
    task_key: str | None = Field(default=None, max_length=200)
    expires_at: str | None = Field(default=None, max_length=80)


class MemoryUpdateIn(StrictModel):
    project_id: str = "project_northstar"
    content: str | None = Field(default=None, max_length=50000)
    title: str | None = Field(default=None, max_length=500)
    confidence: float | None = Field(default=None, ge=0, le=1)
    provenance: dict[str, Any] | None = None
    permission: dict[str, Any] = Field(default_factory=dict)
    user_id: str | None = Field(default=None, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=200)


class MemoryQueryIn(StrictModel):
    project_id: str = "project_northstar"
    query: str = Field(min_length=1, max_length=10000)
    user_id: str | None = Field(default=None, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=200)
    domain: str | None = None
    scope: str | None = None
    permission: dict[str, Any] = Field(default_factory=dict)
    limit: int = Field(default=20, ge=1, le=100)


class MemoryImportIn(StrictModel):
    project_id: str = "project_northstar"
    bundle: dict[str, Any]
    user_id: str | None = Field(default=None, max_length=120)
    permission: dict[str, Any] = Field(default_factory=dict)


class ObsidianImportIn(StrictModel):
    project_id: str = "project_northstar"
    markdown: str = Field(min_length=1, max_length=2_000_000)
    relative_path: str = Field(min_length=1, max_length=500)
    user_id: str | None = Field(default=None, max_length=120)
    permission: dict[str, Any] = Field(default_factory=dict)


class MemoryDeleteIn(StrictModel):
    project_id: str = "project_northstar"
    user_id: str | None = Field(default=None, max_length=120)
    conversation_id: str | None = Field(default=None, max_length=200)
    permission: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(default="", max_length=500)


class ProvenanceNodeIn(StrictModel):
    project_id: str = "project_northstar"
    node_type: str = Field(min_length=1, max_length=40)
    source_ref: str = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=500)
    content_hash: str | None = Field(default=None, max_length=64)
    hash_material: Any | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    reproduction: dict[str, Any] = Field(default_factory=dict)


class ProvenanceEdgeIn(StrictModel):
    project_id: str = "project_northstar"
    child_node_id: str = Field(min_length=1, max_length=200)
    parent_node_id: str = Field(min_length=1, max_length=200)
    relationship: str = Field(min_length=1, max_length=200)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProvenanceFinalIn(StrictModel):
    project_id: str = "project_northstar"
    source_ref: str = Field(min_length=1, max_length=500)
    label: str = Field(default="", max_length=500)
    content_hash: str = Field(min_length=64, max_length=64)
    parent_ids: list[str] = Field(min_length=1, max_length=100)
    metadata: dict[str, Any] = Field(default_factory=dict)
    reproduction: dict[str, Any] = Field(default_factory=dict)


class ProvenanceExportIn(StrictModel):
    project_id: str = "project_northstar"
    node_id: str = Field(min_length=1, max_length=200)


class ProvenanceVerifyExportIn(StrictModel):
    bundle: dict[str, Any]


class PromotionIn(StrictModel):
    candidate_id: str = Field(min_length=1, max_length=200)
    candidate_type: str = Field(default="skill", min_length=1, max_length=40)
    evaluation_outcome: str = Field(pattern="^(PASS|FAIL|NEAR_MISS)$")
    evidence: list[str] = Field(default_factory=list, max_length=100)
    rollback_target: str | None = Field(default=None, max_length=200)


class PromotionDecisionIn(StrictModel):
    reviewer_id: str = Field(min_length=1, max_length=120)
    decision: str = Field(pattern="^(APPROVE|REJECT|OVERRIDE)$")
    reason: str = Field(min_length=1, max_length=2000)
    rollback_target: str | None = Field(default=None, max_length=200)


class MonitorResumeIn(StrictModel):
    actor: str = Field(min_length=1, max_length=120)
    human_confirmed: bool = False


class MonitorHaltIn(StrictModel):
    reason: str = Field(min_length=1, max_length=1000)
    actor: str = Field(min_length=1, max_length=120)


class ToolIn(StrictModel):
    name: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=40)
    scope: str = Field(default="PROJECT", min_length=1, max_length=40)


class ToolTransitionIn(StrictModel):
    state: str = Field(pattern="^(CREATED|TESTING|ACTIVE|REVIEW|DEPRECATED|RETIRED)$")
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=1000)


def seed_workspace() -> None:
    now = now_iso()
    DB.execute("INSERT OR IGNORE INTO projects(id,name,slug,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?)", ("project_northstar", "Northstar Food Stories", "northstar-food-stories", now, now, "{}"))
    DB.execute("INSERT OR IGNORE INTO campaigns(id,project_id,name,created_at,updated_at,metadata_json) VALUES(?,?,?,?,?,?)", ("campaign_citrus", "project_northstar", "Citrus at Dusk", now, now, "{}"))
    DB.execute("INSERT OR IGNORE INTO consent_policies(id,project_id,learning_scope,face_consent,voice_consent,retention_days,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)", ("consent_project_northstar", "project_northstar", "PROJECT_ONLY", 0, 0, 30, now, now))
    DB.execute("INSERT OR IGNORE INTO monitor_state(id,status,updated_at) VALUES(1,'RUNNING',?)", (now,))
    DB.execute("INSERT OR IGNORE INTO monitor_limits(id,updated_at) VALUES(1,?)", (now,))
    for advisor in (
        AdvisorDescriptor("advisor-creative", "Creative Advisor", "creative", ("text_generation", "judge"), 0.90, 0.08, 120),
        AdvisorDescriptor("advisor-evidence", "Evidence Advisor", "evidence", ("text_generation", "judge", "embedding"), 0.86, 0.06, 140),
        AdvisorDescriptor("advisor-critic", "Critique Advisor", "critique", ("text_generation", "judge"), 0.78, 0.09, 160),
        AdvisorDescriptor("advisor-vision", "Vision Advisor", "multimodal", ("vision", "video_reasoning"), 0.82, 0.10, 180),
    ):
        GOVERNOR.register_advisor(advisor)
    DB.execute("INSERT OR IGNORE INTO slo_policies(id,job_type,target_seconds,alert_after_seconds,updated_at) VALUES(?,?,?,?,?)", ("slo_creative_brief", "CreativeBrief", 60, 90, now))
    DB.execute("INSERT OR IGNORE INTO installation_state(id,installation_id,first_seen_at) VALUES(1,?,?,?)", ("install_local", now)) if False else None
    if DB.execute("SELECT 1 FROM installation_state WHERE id=1").fetchone() is None:
        DB.execute("INSERT INTO installation_state(id,installation_id,first_seen_at,onboarding_completed,onboarding_skipped) VALUES(1,?,?,0,0)", (new_id("install"), now))
    if DB.execute("SELECT 1 FROM conversations WHERE id='conversation_citrus'").fetchone() is None:
        DB.execute("INSERT INTO conversations(id,project_id,campaign_id,title,created_at,updated_at) VALUES(?,?,?,?,?,?)", ("conversation_citrus", "project_northstar", "campaign_citrus", "Citrus at Dusk", now, now))
        DB.execute("INSERT INTO messages(id,conversation_id,role,content,created_at,metadata_json) VALUES(?,?,?,?,?,?)", (new_id("msg"), "conversation_citrus", "assistant", "I am ready to carry a creative brief through the inspectable Flujo flow.", now, "{}"))
    EVENTS.audit(category="startup", message="Local Flujo backend initialized", project_id="project_northstar", route="startup", operation="initialize", output_summary={"database": str(DB_PATH), "project_root": str(ROOT)})


seed_workspace()


def recover_incomplete_jobs() -> list[str]:
    rows = DB.execute("SELECT id,project_id,status,checkpoint,checkpoint_data_json FROM jobs WHERE status IN ('queued','running')").fetchall()
    recovered: list[str] = []
    for row in rows:
        try:
            checkpoint_data = json.loads(row["checkpoint_data_json"] or "{}")
        except json.JSONDecodeError:
            checkpoint_data = {}
        checkpoint_data.update({"recovery_required": True, "recovery_reason": "The engine restarted before this job reached a terminal checkpoint.", "recovered_at": now_iso()})
        DB.execute("UPDATE jobs SET status='recovery_required',error_code='WORKFLOW_RECOVERY_REQUIRED',error_message=?,checkpoint_data_json=?,updated_at=? WHERE id=?", ("The workflow was quarantined after engine restart; no side effect was replayed automatically.", dump_json(checkpoint_data), now_iso(), row["id"]))
        EVENTS.audit(category="workflow-recovery", message="Incomplete workflow quarantined after engine restart", project_id=row["project_id"], job_id=row["id"], route="startup", operation="recover", status="recovery_required", metadata={"previous_status": row["status"], "checkpoint": row["checkpoint"]})
        recovered.append(row["id"])
    return recovered


RECOVERED_JOBS = recover_incomplete_jobs()


@app.middleware("http")
async def trace_and_capture(request: Request, call_next):
    trace = set_trace_id(safe_context_value(request.headers.get("x-flujo-trace-id"), "trace"))
    request_id = set_request_id(safe_context_value(request.headers.get("x-flujo-request-id"), "request"))
    started = time.perf_counter()
    response: JSONResponse
    try:
        content_length = request.headers.get("content-length")
        body_too_large = content_length is not None and (not content_length.isdigit() or int(content_length) > MAX_REQUEST_BODY_BYTES)
        if not body_too_large and request.url.path.startswith("/api") and request.method not in {"GET", "HEAD", "OPTIONS"} and content_length is None:
            body_too_large = len(await request.body()) > MAX_REQUEST_BODY_BYTES
        if body_too_large:
            response = JSONResponse(status_code=413, content={"error": {"code": "REQUEST_BODY_TOO_LARGE", "message": "The request body exceeds the local control-plane limit."}})
        elif request.url.path.startswith("/api"):
            supplied_token = request.headers.get("x-flujo-app-token")
            if not APP_TOKEN:
                EVENTS.audit(category="security", message="Rejected API request because the engine session token is not configured", route=request.url.path, operation=request.method, status="blocked", metadata={"error_code": "LOCAL_SESSION_TOKEN_UNCONFIGURED"})
                response = JSONResponse(status_code=503, content={"error": {"code": "LOCAL_SESSION_TOKEN_UNCONFIGURED", "message": "The local engine room is unavailable until its supervised session token is configured."}})
            elif supplied_token is None:
                EVENTS.audit(category="security", message="Rejected API request with missing session token", route=request.url.path, operation=request.method, status="denied", metadata={"error_code": "AUTH_TOKEN_MISSING"})
                response = JSONResponse(status_code=401, content={"error": {"code": "AUTH_TOKEN_MISSING", "message": "The local engine room request was not authenticated."}})
            elif not AUTH_TOKEN_PATTERN.fullmatch(supplied_token):
                EVENTS.audit(category="security", message="Rejected API request with malformed session token", route=request.url.path, operation=request.method, status="denied", metadata={"error_code": "AUTH_TOKEN_MALFORMED"})
                response = JSONResponse(status_code=400, content={"error": {"code": "AUTH_TOKEN_MALFORMED", "message": "The local engine room request had a malformed authentication header."}})
            elif not hmac.compare_digest(supplied_token, APP_TOKEN):
                EVENTS.audit(category="security", message="Rejected unauthenticated local API request", route=request.url.path, operation=request.method, status="denied", metadata={"error_code": "UNAUTHENTICATED_LOCAL_REQUEST"})
                response = JSONResponse(status_code=401, content={"error": {"code": "UNAUTHENTICATED_LOCAL_REQUEST", "message": "The local engine room request was not authenticated."}})
            elif not rate_allowed(request):
                EVENTS.audit(category="rate-limit", message="Local API request rate limit exceeded", route=request.url.path, operation=request.method, status="denied", metadata={"error_code": "RATE_LIMITED"})
                response = JSONResponse(status_code=429, content={"error": {"code": "RATE_LIMITED", "message": "The local engine room request rate limit was exceeded."}})
            else:
                response = await call_next(request)
        else:
            response = await call_next(request)
        duration = int((time.perf_counter() - started) * 1000)
        error_code = error_code_from_response(response)
        response.headers["x-flujo-trace-id"] = trace
        response.headers["x-flujo-request-id"] = request_id
        EVENTS.audit(category="http", message=f"{request.method} {request.url.path}", route=request.url.path, operation=request.method, duration_ms=duration, status=str(response.status_code), metadata={"trace_id": trace, "request_id": request_id, "error_code": error_code, "content_length": content_length})
        return response
    except Exception as exc:
        EVENTS.error(source="http", message=f"Unhandled request error at {request.method} {request.url.path}", exc=exc, route=request.url.path, metadata={"trace_id": trace, "request_id": request_id})
        raise


@app.on_event("shutdown")
def shutdown_workers() -> None:
    WORKERS.shutdown()
    DB.close()


@app.exception_handler(RequestValidationError)
async def request_validation_error(request: Request, exc: RequestValidationError):
    trace = current_trace_id()
    request_id = current_request_id()
    safe_details = [{"loc": list(error.get("loc", [])), "type": error.get("type", "validation_error"), "msg": str(error.get("msg", "Invalid request."))[:300]} for error in exc.errors()[:20]]
    EVENTS.audit(category="validation", message="Request validation failed", route=request.url.path, operation=request.method, status="invalid", metadata={"error_code": "REQUEST_VALIDATION_ERROR", "request_id": request_id, "field_count": len(safe_details)})
    return JSONResponse(status_code=422, content={"error": {"code": "REQUEST_VALIDATION_ERROR", "message": "The request did not satisfy the local API contract.", "trace_id": trace, "request_id": request_id}, "detail": safe_details})


@app.exception_handler(HTTPException)
async def http_error(request: Request, exc: HTTPException):
    detail = exc.detail if isinstance(exc.detail, dict) else {"code": "HTTP_ERROR", "message": str(exc.detail)}
    code = detail.get("code") if isinstance(detail.get("code"), str) else "HTTP_ERROR"
    message = detail.get("message") if isinstance(detail.get("message"), str) else "The local engine room rejected the request."
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message, "trace_id": current_trace_id(), "request_id": current_request_id()}, "detail": detail})


@app.exception_handler(StarletteHTTPException)
async def framework_http_error(request: Request, exc: StarletteHTTPException):
    code = "METHOD_NOT_ALLOWED" if exc.status_code == 405 else f"HTTP_{exc.status_code}"
    message = "The HTTP method is not allowed for this local API route." if exc.status_code == 405 else "The local engine room rejected the request."
    return JSONResponse(status_code=exc.status_code, content={"error": {"code": code, "message": message, "trace_id": current_trace_id(), "request_id": current_request_id()}, "detail": {"code": code, "message": message}})


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    error_id = EVENTS.error(source="fastapi", message="Unhandled backend exception", exc=exc, route=request.url.path)
    return JSONResponse(status_code=500, content={"error": {"code": "INTERNAL_ERROR", "message": "The local engine room stopped this operation safely.", "error_id": error_id, "trace_id": current_trace_id(), "request_id": current_request_id()}})


def validate_identifier(value: str, label: str = "identifier") -> str:
    if not ID_PATTERN.fullmatch(value):
        raise HTTPException(status_code=422, detail={"code": "INVALID_IDENTIFIER", "message": f"The {label} format is invalid."})
    return value


def ensure_project(project_id: str) -> None:
    validate_identifier(project_id, "project_id")
    if DB.execute("SELECT 1 FROM projects WHERE id=?", (project_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail={"code": "PROJECT_NOT_FOUND", "message": "Project context was not found."})


def consent_for(project_id: str) -> dict[str, Any]:
    row = DB.execute("SELECT * FROM consent_policies WHERE project_id=?", (project_id,)).fetchone()
    if row is None:
        now = now_iso()
        DB.execute("INSERT INTO consent_policies(id,project_id,created_at,updated_at) VALUES(?,?,?,?)", (new_id("consent"), project_id, now, now))
        row = DB.execute("SELECT * FROM consent_policies WHERE project_id=?", (project_id,)).fetchone()
    return dict(row)


def record_guard(project_id: str, job_id: str | None, decision: Any, artifact_id: str | None = None) -> str:
    decision_id = new_id("guard")
    DB.execute("INSERT INTO guard_decisions(id,project_id,job_id,phase,status,checks_json,reason,policy_version,modalities_json,classifier_states_json,evidence_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)", (decision_id, project_id, job_id, decision.phase, decision.status, dump_json(decision.checks), decision.reason, decision.policy_version, dump_json(decision.modalities), dump_json(decision.classifier_states), dump_json(decision.evidence), now_iso()))
    if decision.status == "REVIEW":
        DB.execute("INSERT INTO guard_reviews(id,project_id,job_id,artifact_id,decision_id,reason,evidence_json,policy_version,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (new_id("guard-review"), project_id, job_id, artifact_id, decision_id, decision.reason, dump_json(decision.evidence), decision.policy_version, now_iso()))
    EVENTS.audit(category="guard", message=decision.reason, project_id=project_id, job_id=job_id, artifact_id=artifact_id, decision_id=decision_id, route="guard", operation=decision.phase, status=decision.status, output_summary=decision.as_dict())
    return decision_id


def set_job(job_id: str, status: str, checkpoint: str, progress: float, error_code: str | None = None, error_message: str | None = None) -> None:
    DB.execute("UPDATE jobs SET status=?,checkpoint=?,progress=?,checkpoint_data_json=?,error_code=?,error_message=?,updated_at=? WHERE id=?", (status, checkpoint, progress, dump_json({"checkpoint": checkpoint, "progress": progress, "updated_at": now_iso()}), error_code, error_message, now_iso(), job_id))


def step(job_id: str, workflow: str, name: str, progress: float, project_id: str) -> None:
    if WORKERS.is_cancelled(job_id):
        raise WorkflowCancelled(f"Workflow {job_id} was cancelled before {name}.")
    set_job(job_id, "running", name, progress)
    EVENTS.audit(category="workflow", message=name, project_id=project_id, workflow_id=workflow, job_id=job_id, route="vertical-slice", operation=name, status="running")
    time.sleep(0.02)


_CLAIM_SOURCE_TYPES = ("frame-sequence", "visual-signal", "text-intent", "temporal-signal", "audio-signal")

_SIMULATOR_NO_KEY_REASON = "no provider key configured"


class _SliceSimulatorGateway:
    """Gateway-shaped shim exposing only the labeled simulator fallback.

    Used by the vertical slice when no provider credential is configured, so
    every model call in the run is explicitly simulated and labeled, and live
    adapters (including keyless free-tier attempts) are never consulted.
    """

    def __init__(self) -> None:
        self._provider = SimulatorProvider()

    def health(self) -> dict[str, Any]:
        return {"simulator_only": True, "provider_source": "simulator", "default_provider": "simulator", "reason": _SIMULATOR_NO_KEY_REASON}

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        output = self._provider.invoke(capability, payload, idempotency_key)
        return {"provider": "simulator", "model_id": "simulator-reasoner", "capability": capability, "output": output}


def _slice_gateway_state() -> dict[str, Any]:
    """Honest gateway state for the vertical slice.

    The slice counts as live only when the gateway reports serving truth:
    live models advertised AND a provider credential configured. Keyless
    free-tier models advertised without a credential do NOT make the slice
    live; the run uses the labeled simulator fallback and says so.
    """
    try:
        health = GATEWAY.health()
    except Exception:
        health = {}
    simulated = bool(health.get("simulator_only", True))
    return {
        "simulated": simulated,
        "simulator_only": simulated,
        "credentialed": health.get("credentialed"),
        "default_provider": health.get("default_provider"),
        "reason": health.get("reason"),
    }


def _slice_invoker(simulated: bool) -> tuple[Any, Any]:
    """Return (invoke_fn, consult_gateway) for one slice run.

    invoke_fn(capability, payload, idempotency_key) -> (provider, model_id, output_dict).
    """
    if simulated:
        shim = _SliceSimulatorGateway()

        def invoke_simulated(capability: str, payload: dict[str, Any], idempotency_key: str) -> tuple[str, str, dict[str, Any]]:
            result = shim.invoke(capability, payload, idempotency_key)
            output = result.get("output")
            return result["provider"], result["model_id"], output if isinstance(output, dict) else {}

        return invoke_simulated, shim

    def invoke_live(capability: str, payload: dict[str, Any], idempotency_key: str) -> tuple[str, str, dict[str, Any]]:
        result = GATEWAY.invoke(capability, payload, idempotency_key)
        output = result.output if isinstance(result.output, dict) else {}
        return result.provider, result.model_id, output

    return invoke_live, GATEWAY


def _analysis_prompt(brief: str) -> str:
    return (
        "You are analyzing a creative brief for a short video ad. Read the brief, then respond with JSON only, "
        "no prose outside the JSON: {\"claims\": [{\"claim\": \"<one concrete observation grounded in the brief>\", "
        "\"source_type\": \"<frame-sequence|visual-signal|text-intent|temporal-signal|audio-signal>\", "
        "\"locator\": \"<where in the brief or timeline this comes from>\", \"confidence\": <0.0-1.0>}]}. "
        "Emit at most 6 claims, each grounded in the brief text. Brief:\n" + brief[:4000]
    )


def _claims_from_model_text(text: str) -> tuple[list[dict[str, Any]], str]:
    """Parse analysis claims from a model response.

    Only claims the model actually emitted are returned. When nothing parses,
    the note says so and the caller records zero claims. Nothing is invented.
    """
    claims: list[dict[str, Any]] = []
    if not isinstance(text, str) or not text.strip():
        return claims, "model response was empty"
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1 and len(claims) < 6:
        try:
            obj, _ = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict):
            items = obj.get("claims")
            if isinstance(items, list):
                for item in items:
                    if not isinstance(item, dict) or len(claims) >= 6:
                        continue
                    claim_text = str(item.get("claim", "") or "")[:500].strip()
                    if not claim_text:
                        continue
                    source_type = str(item.get("source_type", "text-intent") or "text-intent")
                    if source_type not in _CLAIM_SOURCE_TYPES:
                        source_type = "text-intent"
                    locator = str(item.get("locator", "brief") or "brief")[:200]
                    try:
                        confidence = float(item.get("confidence", 0.5))
                    except (TypeError, ValueError):
                        confidence = 0.5
                    confidence = max(0.0, min(confidence, 1.0))
                    claims.append({"claim": claim_text, "source_type": source_type, "locator": locator, "confidence": confidence})
                break
        start = text.find("{", start + 1)
    note = "" if claims else "model response contained no parseable claims"
    return claims, note


def _genome_patterns(claims: list[dict[str, Any]]) -> dict[str, list[str]]:
    """Group real claims into genome pattern buckets. Buckets stay empty when
    there is nothing to group; no patterns are invented."""
    patterns: dict[str, list[str]] = {"hooks": [], "visual": [], "editing": [], "cta": []}
    for item in claims:
        text = str(item.get("claim", ""))[:120]
        if not text:
            continue
        source_type = item.get("source_type")
        if source_type in ("frame-sequence", "temporal-signal", "audio-signal"):
            bucket = "editing"
        elif source_type == "visual-signal":
            bucket = "visual"
        else:
            bucket = "hooks"
        if len(patterns[bucket]) < 3:
            patterns[bucket].append(text)
    return patterns


def _scenes_from_claims(claims: list[dict[str, Any]], brief: str, simulated: bool) -> list[dict[str, Any]]:
    """Build blueprint scenes from real claims, or one brief-excerpt scene when
    no claims exist. Scene purposes always cite their source."""
    scenes: list[dict[str, Any]] = []
    for index, item in enumerate(claims[:3]):
        scenes.append(
            {
                "id": f"scene-{index + 1:02d}",
                "duration_seconds": 6,
                "purpose": str(item.get("claim", ""))[:140],
                "entities": [],
                "status": "planned",
                "source": "simulator-note" if simulated else "model-claim",
            }
        )
    if not scenes:
        excerpt = " ".join(brief.split())[:140] or "open brief"
        scenes.append(
            {
                "id": "scene-01",
                "duration_seconds": 6,
                "purpose": excerpt,
                "entities": [],
                "status": "planned",
                "source": "brief-excerpt",
                "note": "no analyzed claims available; scene drafted from the brief text only",
            }
        )
    return scenes


def _simulator_slice_reply(brief: str, claim_count: int, decision: Any, memory_hits: list[dict[str, Any]], scenes: list[dict[str, Any]], qa_status: str, ffmpeg_available: bool, postflight_allowed: bool) -> str:
    """Honest labeled reply for simulator-fallback runs. Describes what the
    pipeline actually recorded; never pretends the model analyzed the brief."""
    excerpt = " ".join(brief.split())[:220]
    hit_titles = [str(item.get("title", "untitled"))[:80] for item in memory_hits[:3]]
    memory_part = f"Memory lookup found {len(memory_hits)} entries" + (f" ({'; '.join(hit_titles)})" if hit_titles else "") + "."
    fused = str(getattr(decision, "fused_answer", "") or "")[:300]
    governor_part = f"Governor council returned status {getattr(decision, 'status', 'unknown')}" + (f": {fused}" if fused else ".")
    media_part = "The media worker is ready." if ffmpeg_available else "Media rendering is blocked: FFmpeg/FFprobe are not available on this machine."
    gate_part = "The Guard postflight passed." if postflight_allowed else "A Guard gate is still active on this run."
    return (
        "I ran your brief through the local pipeline, but I cannot draft creative work yet: no provider key is "
        "configured, so every model stage used the labeled simulator fallback instead of a real model. "
        f"Your brief was recorded ({len(brief)} characters): \"{excerpt}\". "
        f"{memory_part} {governor_part} "
        f"The pipeline drafted {len(scenes)} scene(s) from the brief text and QA reports {qa_status}. "
        f"{media_part} {gate_part} "
        "To get real creative analysis, store an NVIDIA API key in the local credential vault under the provider ref "
        "\"nvidia\", then send the brief again."
    )


def _live_slice_reply_prompt(brief: str, claims: list[dict[str, Any]], decision: Any, memory_hits: list[dict[str, Any]], scenes: list[dict[str, Any]], qa_status: str) -> str:
    claim_lines = "\n".join(f"- {item['claim']} (confidence {item['confidence']:.2f})" for item in claims[:6]) or "- (no claims were produced)"
    hit_lines = "\n".join(f"- {item.get('title', 'untitled')}" for item in memory_hits[:3]) or "- (no memory hits)"
    scene_lines = "\n".join(f"- {scene['id']}: {scene['purpose'][:100]}" for scene in scenes) or "- (no scenes)"
    fused = str(getattr(decision, "fused_answer", "") or "")[:600]
    return (
        "You are Flujo Studio's creative assistant. A user sent the brief below and the local pipeline processed it. "
        "Write a concise reply (under 140 words) that responds to their brief and summarizes what the pipeline produced. "
        "Do not invent deliverables beyond what is listed.\n\n"
        f"Brief: {brief[:2000]}\n\n"
        f"Analysis claims:\n{claim_lines}\n\n"
        f"Governor decision ({getattr(decision, 'status', 'unknown')}): {fused}\n\n"
        f"Memory hits:\n{hit_lines}\n\n"
        f"Scenes:\n{scene_lines}\n\n"
        f"QA status: {qa_status}"
    )


def run_vertical_slice(job_id: str, conversation_id: str, message: MessageIn) -> None:
    workflow = new_id("workflow")
    project_id = message.project_id
    trace = current_trace_id()
    brief = (message.content or "").strip()
    try:
        reference_ids: list[str] = []
        attachments = message.attachments or [AttachmentIn(label="Brief text", kind="text", role="creative direction")]
        consent = consent_for(project_id)
        preflight = GUARD.preflight(text=message.content, references=[item.model_dump() for item in attachments], consent=consent)
        record_guard(project_id, job_id, preflight)
        if not preflight.allowed:
            code = "GUARD_BLOCKED" if preflight.status == "BLOCK" else "GUARD_REVIEW_REQUIRED"
            set_job(job_id, "blocked" if preflight.status == "BLOCK" else "needs_review", "GuardPreflight", 0.04, code, preflight.reason)
            return
        step(job_id, workflow, "ReferenceIngestion", 0.08, project_id)
        for item in attachments:
            ref_id = new_id("ref")
            reference_ids.append(ref_id)
            filename = item.filename or f"{ref_id}.json"
            stored_path = REFERENCES_DIR / filename
            DB.execute('INSERT INTO "references"(id,project_id,conversation_id,label,kind,role,path,sha256,status,metadata_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)', (ref_id, project_id, conversation_id, item.label, item.kind, item.role, str(stored_path), item.content_hash, "ingested", dump_json(item.model_dump()), now_iso()))

        step(job_id, workflow, "ModelGateway", 0.16, project_id)
        gateway_state = _slice_gateway_state()
        simulated = gateway_state["simulated"]
        invoke_slice, consult_gateway = _slice_invoker(simulated)
        plan_provider, plan_model, plan_output = invoke_slice(
            "text_generation",
            {"brief": brief, "references": [item.model_dump() for item in attachments], "milestone": "single_scene"},
            f"{job_id}:single-scene-planning",
        )
        EVENTS.audit(category="model-gateway", message="Single-scene planning response received", project_id=project_id, workflow_id=workflow, job_id=job_id, route="single-scene", operation="plan", status="simulator_only" if simulated else "completed", metadata={"provider": plan_provider, "model_id": plan_model, "capability": "text_generation", "simulator_only": simulated, "reason": gateway_state["reason"], "idempotency_key": f"{job_id}:single-scene-planning"})

        step(job_id, workflow, "AdaptiveAnalysis", 0.22, project_id)
        claims: list[dict[str, Any]] = []
        analysis_model = plan_model
        analysis_note = ""
        if not simulated:
            analysis_provider, analysis_model, analysis_output = invoke_slice("text_generation", {"prompt": _analysis_prompt(brief)}, f"{job_id}:adaptive-analysis")
            claims, analysis_note = _claims_from_model_text(str(analysis_output.get("text", "")))
            EVENTS.audit(category="model-gateway", message="Adaptive analysis complete", project_id=project_id, workflow_id=workflow, job_id=job_id, route="single-scene", operation="analyze", status="completed", metadata={"provider": analysis_provider, "model_id": analysis_model, "claim_count": len(claims), "note": analysis_note})
        else:
            analysis_note = "analysis skipped: no provider key configured (labeled simulator fallback); zero claims recorded"
            EVENTS.audit(category="model-gateway", message="Adaptive analysis skipped", project_id=project_id, workflow_id=workflow, job_id=job_id, route="single-scene", operation="analyze", status="simulator_only", metadata={"claim_count": 0, "reason": gateway_state["reason"], "note": analysis_note})
        evidence_ids: list[str] = []
        for item in claims:
            evidence_id = new_id("evidence")
            evidence_ids.append(evidence_id)
            DB.execute("INSERT INTO evidence(id,project_id,reference_id,claim,source_type,source_locator,model_id,model_version,confidence,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)", (evidence_id, project_id, reference_ids[0], item["claim"], item["source_type"], item["locator"], analysis_model, "1.0", item["confidence"], now_iso(), dump_json({"adaptive": True, "trace_id": trace, "simulated": simulated})))

        step(job_id, workflow, "MemoryLookupWithProvenance", 0.32, project_id)
        memory_hits: list[dict[str, Any]] = []
        memory_note = ""
        try:
            memory_hits = MEMORY.semantic(brief, project_id=project_id, limit=5)
        except Exception as exc:
            memory_note = f"memory lookup failed safely: {exc}"
        EVENTS.audit(category="memory", message=f"Memory lookup returned {len(memory_hits)} hits", project_id=project_id, workflow_id=workflow, job_id=job_id, route="single-scene", operation="memory-lookup", status="completed", metadata={"hit_count": len(memory_hits), "hit_titles": [str(item.get("title", ""))[:80] for item in memory_hits[:5]], "note": memory_note})

        step(job_id, workflow, "GovernorCouncil", 0.42, project_id)
        gov_request = GovernorRequest(
            task=f"Creative direction for this brief: {brief[:600]}",
            required_capability="text_generation",
            context={"brief": brief[:2000], "claims": [item["claim"] for item in claims], "memory_hit_titles": [str(item.get("title", ""))[:80] for item in memory_hits[:5]], "simulated": simulated},
            risk_level="medium",
            max_advisors=3,
            idempotency_key=f"{job_id}:governor-council",
        )
        decision = GOVERNOR.decide(gov_request, consultation=lambda advisor, req: gateway_consult(consult_gateway, advisor, req))
        decision_id = new_id("decision")
        confidences = [float(op.get("confidence", 0.0)) for op in decision.opinions if op.get("success")]
        fused_confidence = sum(confidences) / len(confidences) if confidences else 0.0
        conflict = decision.conflict if isinstance(decision.conflict, dict) else {}
        rationale = str(conflict.get("reason", "") or "")[:1000] or f"Governor fused {len(decision.opinions)} advisor opinions with status {decision.status}."
        DB.execute("INSERT INTO decisions(id,project_id,conversation_id,status,decision,rationale,confidence,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?,?)", (decision_id, project_id, conversation_id, decision.status, str(decision.fused_answer or "")[:2000], rationale, fused_confidence, now_iso(), dump_json({"advisor_count": len(decision.selected_advisors), "governor_decision_id": decision.decision_id, "simulated": simulated, "trace_id": trace})))

        step(job_id, workflow, "CreativeGenome", 0.51, project_id)
        genome_id = new_id("genome")
        genome_name = " ".join(brief.split())[:60] or "Untitled brief"
        genome_metadata = {"simulated": simulated, "claim_count": len(claims), "governor_decision_id": decision.decision_id, "trace_id": trace}
        if simulated:
            genome_metadata["note"] = "genome seeded from brief-derived structure only; no live analysis (no provider key configured)"
        DB.execute("INSERT INTO genomes(id,project_id,name,version,patterns_json,evidence_ids_json,created_at) VALUES(?,?,?,?,?,?,?)", (genome_id, project_id, genome_name, "v0.1", dump_json(_genome_patterns(claims)), dump_json(evidence_ids), now_iso()))

        step(job_id, workflow, "CreativeBlueprint", 0.61, project_id)
        blueprint_id = new_id("blueprint")
        scenes = _scenes_from_claims(claims, brief, simulated)
        blueprint_name = f"{genome_name} / Blueprint"
        DB.execute("INSERT INTO blueprints(id,project_id,genome_id,name,scenes_json,constraints_json,created_at) VALUES(?,?,?,?,?,?,?)", (blueprint_id, project_id, genome_id, blueprint_name, dump_json(scenes), dump_json({"identity_consistency": "required", "human_review": True}), now_iso()))

        step(job_id, workflow, "CreativeCompiler", 0.69, project_id)
        compile_id = new_id("compile")
        model_roles = [str(rec.get("role", ""))[:80] for rec in decision.selected_advisors if rec.get("role")] or ["governor-advisor"]
        plan = {"scene_ids": [scene["id"] for scene in scenes], "operations": ["inspect_media"] + (["render_remotion"] if scenes else []), "provider_neutral": True, "model_roles": model_roles, "governor_decision_id": decision.decision_id, "simulated": simulated}
        DB.execute("INSERT INTO compile_runs(id,blueprint_id,provider_neutral_plan_json,status,created_at) VALUES(?,?,?,?,?)", (compile_id, blueprint_id, dump_json(plan), "compiled", now_iso()))

        step(job_id, workflow, "PermissionCheck", 0.77, project_id)
        permission_id = new_id("permission")
        media_health = MEDIA.health_check()
        ffmpeg_available = media_health["ffmpeg"]["status"] == "healthy" and media_health["ffprobe"]["status"] == "healthy"
        permission_decision = "allow" if ffmpeg_available else "deny"
        if media_health["status"] == "corrupt":
            permission_reason = "A bundled media binary failed verification; media execution is blocked instead of simulated."
            media_error_code = "MEDIA_DEPENDENCY_CORRUPT"
        elif ffmpeg_available:
            permission_reason = "Bundled FFmpeg and FFprobe are verified and the operation is allowlisted."
            media_error_code = None
        else:
            permission_reason = "Bundled FFmpeg/FFprobe are missing on this machine; media execution is blocked instead of simulated."
            media_error_code = "MEDIA_DEPENDENCY_MISSING"
        DB.execute("INSERT INTO permission_checks(id,project_id,operation,requested_permissions_json,decision,reason,created_at) VALUES(?,?,?,?,?,?,?)", (permission_id, project_id, "render_scene_01", dump_json(["MEDIA_READ", "MEDIA_WRITE", "PROCESS_EXECUTE"]), permission_decision, permission_reason, now_iso()))
        step(job_id, workflow, "SandboxedMediaOperation", 0.84, project_id)
        artifact_id = new_id("artifact")
        artifact_path = ARTIFACTS_DIR / f"{artifact_id}-scene-01.json"
        artifact_payload = {"artifact_id": artifact_id, "kind": "scene-plan", "scene": scenes[0], "permission": permission_decision, "reason": permission_reason, "generation": {"milestone": "single_scene", "status": "simulator_only" if simulated else "provider_generated", "playable": False, "provider": plan_provider, "model_id": plan_model, "capability": "text_generation"}, "created_at": now_iso()}
        artifact_path.write_text(json.dumps(artifact_payload, indent=2), encoding="utf-8")
        artifact_status = "blocked_dependency" if not ffmpeg_available else "created"
        DB.execute("INSERT INTO artifacts(id,project_id,kind,name,path,status,created_at,metadata_json) VALUES(?,?,?,?,?,?,?,?)", (artifact_id, project_id, "scene-plan", blueprint_name, str(artifact_path), artifact_status, now_iso(), dump_json({"trace_id": trace, "media_render": "not_executed" if not ffmpeg_available else "ready"})))
        DB.execute("INSERT INTO lineage(id,artifact_id,parent_type,parent_id,relationship,created_at) VALUES(?,?,?,?,?,?)", (new_id("lineage"), artifact_id, "blueprint", blueprint_id, "compiled-from", now_iso()))
        DB.execute("INSERT INTO lineage(id,artifact_id,parent_type,parent_id,relationship,created_at) VALUES(?,?,?,?,?,?)", (new_id("lineage"), artifact_id, "genome", genome_id, "informed-by", now_iso()))
        for evidence_id in evidence_ids:
            DB.execute("INSERT INTO lineage(id,artifact_id,parent_type,parent_id,relationship,created_at) VALUES(?,?,?,?,?,?)", (new_id("lineage"), artifact_id, "evidence", evidence_id, "supported-by", now_iso()))
        postflight = GUARD.postflight(output_summary=json.dumps(artifact_payload), consent=consent)
        record_guard(project_id, job_id, postflight)
        guard_error_code = None if postflight.allowed else ("GUARD_BLOCKED" if postflight.status == "BLOCK" else "GUARD_REVIEW_REQUIRED")
        guard_error_message = None if postflight.allowed else postflight.reason
        step(job_id, workflow, "Quality", 0.94, project_id)
        qa_id = new_id("qa")
        qa_status = "blocked" if not ffmpeg_available else "passed"
        findings = [{"severity": "blocking", "code": media_error_code, "message": permission_reason}] if not ffmpeg_available else []
        DB.execute("INSERT INTO qa_runs(id,project_id,job_id,status,findings_json,created_at) VALUES(?,?,?,?,?,?)", (qa_id, project_id, job_id, qa_status, dump_json(findings), now_iso()))
        final_status = "blocked" if (not ffmpeg_available or guard_error_code == "GUARD_BLOCKED") else "needs_review" if guard_error_code else "completed"
        final_error_code = media_error_code or guard_error_code
        final_error_message = permission_reason if not ffmpeg_available else guard_error_message
        set_job(job_id, final_status, "ArtifactLineage", 1.0, final_error_code, final_error_message)
        DB.execute("INSERT INTO lineage(id,artifact_id,parent_type,parent_id,relationship,created_at) VALUES(?,?,?,?,?,?)", (new_id("lineage"), artifact_id, "decision", decision_id, "approved-by", now_iso()))
        EVENTS.audit(category="workflow", message="Vertical slice reached Artifact Lineage", project_id=project_id, workflow_id=workflow, job_id=job_id, artifact_id=artifact_id, decision_id=decision_id, route="single-scene", operation="complete", status=final_status, output_summary={"qa": qa_status, "media_dependency": ffmpeg_available, "guard": postflight.status, "generation": artifact_payload["generation"]})
        if simulated:
            assistant_text = _simulator_slice_reply(brief, len(claims), decision, memory_hits, scenes, qa_status, ffmpeg_available, postflight.allowed)
        else:
            try:
                _, _, reply_output = invoke_slice("text_generation", {"prompt": _live_slice_reply_prompt(brief, claims, decision, memory_hits, scenes, qa_status)}, f"{job_id}:assistant-reply")
                reply_text = str(reply_output.get("text", "") or "").strip()
            except GatewayError:
                reply_text = ""
            if not reply_text:
                fused = str(decision.fused_answer or "")[:400]
                assistant_text = f"Pipeline complete: {len(claims)} claims analyzed, Governor decision {decision.status}" + (f" ({fused})" if fused else "") + f", {len(scenes)} scene(s) drafted, QA {qa_status}."
            else:
                assistant_text = reply_text
        DB.execute("INSERT INTO messages(id,conversation_id,role,content,created_at,metadata_json) VALUES(?,?,?,?,?,?)", (new_id("msg"), conversation_id, "assistant", assistant_text, now_iso(), dump_json({"job_id": job_id, "workflow_id": workflow, "artifact_id": artifact_id, "trace_id": trace, "simulated": simulated})))
    except GatewayError as exc:
        set_job(job_id, "blocked", "ModelGateway", 1.0, exc.code, exc.message)
        EVENTS.audit(category="model-gateway", message="Single-scene generation blocked at ModelGateway", project_id=project_id, job_id=job_id, route="single-scene", operation="plan", status="blocked", output_summary={"code": exc.code, "retryable": exc.retryable})
    except WorkflowCancelled as exc:
        set_job(job_id, "cancelled", "cancelled", 1.0, "WORKFLOW_CANCELLED", str(exc)[:500])
        EVENTS.audit(category="workflow", message="Workflow cancelled cooperatively", project_id=project_id, workflow_id=workflow, job_id=job_id, route="vertical-slice", operation="cancel", status="cancelled")
    except Exception as exc:
        set_job(job_id, "failed", "error", 1.0, "WORKFLOW_FAILED", str(exc)[:500])
        EVENTS.error(source="vertical-slice", message="Vertical slice failed", exc=exc, project_id=project_id, workflow_id=workflow, job_id=job_id, metadata={"trace_id": trace})


@app.get("/health")
def health() -> dict[str, Any]:
    media_health = public_media_health()
    return {"status": "ok", "service": "flujo-local-engine-room", "version": "0.2.0", "trace_id": current_trace_id(), "request_id": current_request_id(), "database": DB_PATH.exists(), "database_integrity": integrity_check(DB), "schema_version": SCHEMA_VERSION, "auth": {"session_token_configured": bool(APP_TOKEN)}, "ffmpeg": media_health["ffmpeg"]["status"] == "healthy", "ffprobe": media_health["ffprobe"]["status"] == "healthy", "media": media_health, "gateway": GATEWAY.health(), "worker_capacity": 2, "constitution_version": constitution()["version"]}


@app.get("/api/constitution")
def constitution_route() -> dict[str, Any]:
    return constitution()


@app.get("/api/jobs/recovery")
def recovery_jobs() -> list[dict[str, Any]]:
    return rows_to_dicts(DB.execute("SELECT id,project_id,workflow_name,status,error_code,error_message,checkpoint,checkpoint_data_json,updated_at FROM jobs WHERE status='recovery_required' ORDER BY updated_at DESC").fetchall())


@app.get("/api/media/health")
def public_media_health() -> dict[str, Any]:
    raw = MEDIA.health_check()
    safe = dict(raw)
    for key in ("ffmpeg", "ffprobe"):
        if isinstance(safe.get(key), dict):
            safe[key] = {field: value for field, value in safe[key].items() if field not in {"path", "platform_root"}}
    safe.pop("platform_root", None)
    return safe


def media_health() -> dict[str, Any]:
    return public_media_health()


def adaptation_request(payload: AdaptationExperienceIn) -> AdaptationRequest:
    return AdaptationRequest(project_id=payload.project_id, task=payload.task, feedback=payload.feedback, candidate_type=payload.candidate_type, scope=payload.scope, user_id=payload.user_id, model_id=payload.model_id, provider_id=payload.provider_id, tool_name=payload.tool_name, recipe_name=payload.recipe_name, proposal=payload.proposal, evidence_ids=tuple(payload.evidence_ids), consent=payload.consent, outcome=payload.outcome)


def adaptation_error(exc: AdaptationBlocked) -> None:
    raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/adaptation/experiences")
def adaptation_experience(payload: AdaptationExperienceIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return ADAPTATION.record_experience(adaptation_request(payload))
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/experiences/{experience_id}/feedback")
def adaptation_feedback(experience_id: str, payload: AdaptationFeedbackIn) -> dict[str, Any]:
    validate_identifier(experience_id, "experience_id")
    try:
        return ADAPTATION.attribute_feedback(experience_id, outcome=payload.outcome, quality=payload.quality, attribution=payload.attribution, evidence_ids=payload.evidence_ids)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.get("/api/adaptation/candidates")
def adaptation_candidates(project_id: str = "project_northstar") -> list[dict[str, Any]]:
    ensure_project(project_id)
    return rows_to_dicts(DB.execute("SELECT id,project_id,user_id,scope,candidate_type,task_key,status,current_version,source_experience_id,created_at,updated_at FROM adaptation_candidates WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall())


@app.post("/api/adaptation/candidates")
def adaptation_candidate(payload: AdaptationExperienceIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return ADAPTATION.extract_candidate(adaptation_request(payload), experience_id=payload.experience_id)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.get("/api/adaptation/candidates/{candidate_id}")
def adaptation_candidate_get(candidate_id: str) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.get_candidate(candidate_id)
    except AdaptationBlocked as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/adaptation/candidates/{candidate_id}/compile")
def adaptation_compile(candidate_id: str) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.compile_candidate(candidate_id)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/candidates/{candidate_id}/evaluate")
def adaptation_evaluate(candidate_id: str, payload: AdaptationEvaluateIn) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.evaluate_candidate(candidate_id, payload.cases).as_dict()
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/candidates/{candidate_id}/promote")
def adaptation_promote(candidate_id: str, payload: AdaptationReviewIn) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.promote(candidate_id, reviewer_id=payload.reviewer_id, reason=payload.reason, consent=payload.consent)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/candidates/{candidate_id}/rollback")
def adaptation_rollback(candidate_id: str, payload: AdaptationReviewIn) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.rollback(candidate_id, reviewer_id=payload.reviewer_id, reason=payload.reason, target_version=payload.target_version)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/candidates/{candidate_id}/drift")
def adaptation_drift(candidate_id: str, payload: AdaptationDriftIn) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.record_drift(candidate_id, observed_score=payload.observed_score, threshold=payload.threshold, evidence=payload.evidence, reviewer_id=payload.reviewer_id)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.post("/api/adaptation/candidates/{candidate_id}/retire")
def adaptation_retire(candidate_id: str, payload: AdaptationReviewIn) -> dict[str, Any]:
    validate_identifier(candidate_id, "candidate_id")
    try:
        return ADAPTATION.retire(candidate_id, reviewer_id=payload.reviewer_id, reason=payload.reason)
    except AdaptationBlocked as exc:
        adaptation_error(exc)


@app.get("/api/adaptation/model-affinity")
def adaptation_model_affinity(model_id: str | None = None, task_key: str | None = None) -> list[dict[str, Any]]:
    return ADAPTATION.model_affinity(model_id=model_id, task_key=task_key)


@app.post("/api/adaptation/model-affinity/outcome")
def adaptation_model_outcome(payload: AdaptationModelOutcomeIn) -> list[dict[str, Any]]:
    return ADAPTATION.record_model_outcome(model_id=payload.model_id, task_key=payload.task_key, success=payload.success, quality=payload.quality, latency_ms=payload.latency_ms, cost=payload.cost)


def memory_input(payload: MemoryRecordIn) -> MemoryInput:
    return MemoryInput(project_id=payload.project_id, domain=payload.domain, scope=payload.scope, content=payload.content, source=payload.source, confidence=payload.confidence, provenance=payload.provenance, permission=payload.permission, title=payload.title, user_id=payload.user_id, conversation_id=payload.conversation_id, task_key=payload.task_key, expires_at=payload.expires_at)


def memory_error(exc: MemoryBlocked, not_found: bool = False) -> None:
    raise HTTPException(status_code=404 if not_found else 422, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/memory/records")
def memory_create(payload: MemoryRecordIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return MEMORY.create(memory_input(payload))
    except MemoryBlocked as exc:
        memory_error(exc)


@app.get("/api/memory/records")
def memory_records(project_id: str = "project_northstar", user_id: str | None = None, conversation_id: str | None = None, permission: str = "") -> list[dict[str, Any]]:
    ensure_project(project_id)
    try:
        permission_data = json.loads(permission) if permission else {}
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail={"code": "INVALID_MEMORY_PERMISSION", "message": "Permission must be valid JSON."})
    return [MEMORY._record(row) for row in MEMORY._query_rows(project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission_data)]


@app.get("/api/memory/records/{memory_id}")
def memory_get(memory_id: str, project_id: str = "project_northstar", user_id: str | None = None, conversation_id: str | None = None, permission: str = "") -> dict[str, Any]:
    validate_identifier(memory_id, "memory_id")
    try:
        permission_data = json.loads(permission) if permission else {}
        return MEMORY.get(memory_id, project_id=project_id, user_id=user_id, conversation_id=conversation_id, permission=permission_data)
    except json.JSONDecodeError:
        raise HTTPException(status_code=422, detail={"code": "INVALID_MEMORY_PERMISSION", "message": "Permission must be valid JSON."})
    except MemoryBlocked as exc:
        memory_error(exc, not_found=True)


@app.patch("/api/memory/records/{memory_id}")
def memory_update(memory_id: str, payload: MemoryUpdateIn) -> dict[str, Any]:
    validate_identifier(memory_id, "memory_id")
    ensure_project(payload.project_id)
    try:
        return MEMORY.update(memory_id, project_id=payload.project_id, content=payload.content, title=payload.title, confidence=payload.confidence, provenance=payload.provenance, permission=payload.permission, user_id=payload.user_id, conversation_id=payload.conversation_id)
    except MemoryBlocked as exc:
        memory_error(exc, not_found=exc.code == "MEMORY_NOT_FOUND")


@app.delete("/api/memory/records/{memory_id}")
def memory_delete(memory_id: str, payload: MemoryDeleteIn) -> dict[str, Any]:
    validate_identifier(memory_id, "memory_id")
    ensure_project(payload.project_id)
    try:
        return MEMORY.delete(memory_id, project_id=payload.project_id, user_id=payload.user_id, conversation_id=payload.conversation_id, permission=payload.permission, reason=payload.reason)
    except MemoryBlocked as exc:
        memory_error(exc, not_found=True)


@app.post("/api/memory/search/exact")
def memory_search_exact(payload: MemoryQueryIn) -> list[dict[str, Any]]:
    ensure_project(payload.project_id)
    return MEMORY.exact(payload.query, project_id=payload.project_id, user_id=payload.user_id, conversation_id=payload.conversation_id, permission=payload.permission, domain=payload.domain, scope=payload.scope, limit=payload.limit)


@app.post("/api/memory/search/semantic")
def memory_search_semantic(payload: MemoryQueryIn) -> list[dict[str, Any]]:
    ensure_project(payload.project_id)
    return MEMORY.semantic(payload.query, project_id=payload.project_id, user_id=payload.user_id, conversation_id=payload.conversation_id, permission=payload.permission, domain=payload.domain, scope=payload.scope, limit=payload.limit)


@app.post("/api/memory/retention/purge")
def memory_retention(project_id: str | None = "project_northstar") -> dict[str, Any]:
    if project_id:
        ensure_project(project_id)
    return MEMORY.purge_expired(project_id=project_id)


@app.post("/api/memory/export")
def memory_export(payload: MemoryQueryIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    return MEMORY.export_bundle(project_id=payload.project_id, user_id=payload.user_id, conversation_id=payload.conversation_id, permission=payload.permission, domain=payload.domain)


@app.post("/api/memory/import")
def memory_import(payload: MemoryImportIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return MEMORY.import_bundle(payload.bundle, project_id=payload.project_id, user_id=payload.user_id, permission=payload.permission)
    except MemoryBlocked as exc:
        memory_error(exc)


@app.post("/api/memory/obsidian/import")
def memory_obsidian_import(payload: ObsidianImportIn) -> list[dict[str, Any]]:
    ensure_project(payload.project_id)
    try:
        return MEMORY.import_obsidian_markdown(markdown=payload.markdown, project_id=payload.project_id, relative_path=payload.relative_path, user_id=payload.user_id, permission=payload.permission)
    except MemoryBlocked as exc:
        memory_error(exc)


def provenance_error(exc: ProvenanceBlocked, not_found: bool = False) -> None:
    raise HTTPException(status_code=404 if not_found else 422, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/provenance/nodes")
def provenance_node(payload: ProvenanceNodeIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return PROVENANCE.create_node(project_id=payload.project_id, node_type=payload.node_type, source_ref=payload.source_ref, label=payload.label, content_hash=payload.content_hash, hash_material=payload.hash_material, metadata=payload.metadata, reproduction=payload.reproduction)
    except ProvenanceBlocked as exc:
        provenance_error(exc)


@app.get("/api/provenance/nodes/{node_id}")
def provenance_node_get(node_id: str, project_id: str = "project_northstar") -> dict[str, Any]:
    validate_identifier(node_id, "node_id")
    try:
        return PROVENANCE.get_node(node_id, project_id=project_id)
    except ProvenanceBlocked as exc:
        provenance_error(exc, not_found=True)


@app.post("/api/provenance/edges")
def provenance_edge(payload: ProvenanceEdgeIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return PROVENANCE.link(project_id=payload.project_id, child_id=payload.child_node_id, parent_id=payload.parent_node_id, relationship=payload.relationship, metadata=payload.metadata)
    except ProvenanceBlocked as exc:
        provenance_error(exc)


@app.post("/api/provenance/final-artifacts")
def provenance_final(payload: ProvenanceFinalIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return PROVENANCE.register_final_artifact(project_id=payload.project_id, source_ref=payload.source_ref, label=payload.label, content_hash=payload.content_hash, parent_ids=payload.parent_ids, metadata=payload.metadata, reproduction=payload.reproduction)
    except ProvenanceBlocked as exc:
        provenance_error(exc)


@app.get("/api/provenance/nodes/{node_id}/lineage")
def provenance_lineage(node_id: str, project_id: str = "project_northstar") -> dict[str, Any]:
    validate_identifier(node_id, "node_id")
    try:
        return PROVENANCE.lineage(node_id, project_id=project_id)
    except ProvenanceBlocked as exc:
        provenance_error(exc, not_found=exc.code == "NODE_NOT_FOUND")


@app.get("/api/provenance/nodes/{node_id}/verify")
def provenance_verify(node_id: str, project_id: str = "project_northstar") -> dict[str, Any]:
    validate_identifier(node_id, "node_id")
    try:
        return PROVENANCE.verify_node(node_id, project_id=project_id)
    except ProvenanceBlocked as exc:
        provenance_error(exc, not_found=exc.code == "NODE_NOT_FOUND")


@app.post("/api/provenance/exports")
def provenance_export(payload: ProvenanceExportIn) -> dict[str, Any]:
    ensure_project(payload.project_id)
    try:
        return PROVENANCE.export(payload.node_id, project_id=payload.project_id)
    except ProvenanceBlocked as exc:
        provenance_error(exc, not_found=exc.code == "NODE_NOT_FOUND")


@app.post("/api/provenance/exports/verify")
def provenance_export_verify(payload: ProvenanceVerifyExportIn) -> dict[str, Any]:
    try:
        return PROVENANCE.verify_export(payload.bundle)
    except ProvenanceBlocked as exc:
        provenance_error(exc)


@app.get("/api/provenance/nodes/{node_id}/c2pa")
def provenance_c2pa(node_id: str, project_id: str = "project_northstar") -> dict[str, Any]:
    validate_identifier(node_id, "node_id")
    try:
        return PROVENANCE.c2pa_manifest(node_id, project_id=project_id)
    except ProvenanceBlocked as exc:
        provenance_error(exc, not_found=exc.code == "NODE_NOT_FOUND")


@app.get("/api/governor/advisors")
def governor_advisors() -> list[dict[str, Any]]:
    return GOVERNOR.advisors()


@app.post("/api/governor/decide")
def governor_decide(payload: GovernorDecideIn) -> dict[str, Any]:
    try:
        decision = GOVERNOR.decide(GovernorRequest(task=payload.task, required_capability=payload.required_capability, context=payload.context, budget=payload.budget, risk_level=payload.risk_level, max_advisors=payload.max_advisors, retry_budget=payload.retry_budget, needs_tool=payload.needs_tool, idempotency_key=payload.idempotency_key), consultation=lambda advisor, request: gateway_consult(GATEWAY, advisor, request))
        return decision.as_dict()
    except GovernorBlocked as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": exc.message}) from exc


@app.get("/api/governor/decisions/{decision_id}")
def governor_decision(decision_id: str) -> dict[str, Any]:
    validate_identifier(decision_id, "decision_id")
    try:
        return GOVERNOR.get_decision(decision_id)
    except GovernorBlocked as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/governor/decisions/{decision_id}/outcome")
def governor_outcome(decision_id: str, payload: GovernorOutcomeIn) -> dict[str, Any]:
    validate_identifier(decision_id, "decision_id")
    try:
        return GOVERNOR.record_outcome(decision_id=decision_id, success=payload.success, accuracy=payload.accuracy, task_affinity=payload.task_affinity, agreement=payload.agreement, note=payload.note)
    except GovernorBlocked as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc


@app.get("/api/models")
def models() -> list[dict[str, Any]]:
    return [model.as_dict() for model in GATEWAY.discover()]


@app.post("/api/media/operation")
def media_operation(payload: MediaOperationIn) -> dict[str, Any]:
    try:
        MONITOR.authorize(project_id=payload.project_id, operation=f"Media:{payload.operation}")
        MONITOR.record_spend(project_id=payload.project_id, amount=0.02, operation=f"Media:{payload.operation}")
    except MonitorBlocked as exc:
        EVENTS.audit(category="monitor", message=exc.message, project_id=payload.project_id, route="media", operation="authorize", status="HALTED", metadata={"code": exc.code})
        raise HTTPException(status_code=423, detail={"code": exc.code, "message": exc.message}) from exc
    result = MEDIA_ENGINE.run(payload.operation, [Path(item) for item in payload.inputs], Path(payload.output) if payload.output else None, payload.options, payload.trace_id or current_trace_id(), payload.timeout_seconds, payload.project_id)
    EVENTS.audit(category="media", message=result.message, route="media", operation=payload.operation, status=result.status, duration_ms=result.duration_ms, metadata={"operation_id": result.operation_id, "error_code": result.error_code, "input_count": len(result.input_artifacts)})
    return result.as_dict()


@app.get("/api/gateway/health")
def gateway_health() -> dict[str, Any]:
    return GATEWAY.health()


# Last-seen timestamp (ISO 8601) of a frontend diagnostic event. The Doctor's
# Electron check reads this as the frontend heartbeat signal; None means the
# desktop shell has never reported in.
_FRONTEND_HEARTBEAT_AT: str | None = None


def _backend_port() -> int:
    try:
        return int(os.environ.get("FLUJO_PORT", "47821") or 47821)
    except (TypeError, ValueError):
        return 47821


def _engine_probe() -> dict[str, Any]:
    """Gather real engine liveness signals for the Doctor's Engine check.

    Never self-attests: worker-thread responsiveness is measured by timing a
    no-op through the real worker pool, schema sanity by PRAGMA quick_check,
    and active jobs by counting non-terminal job rows.
    """
    probe: dict[str, Any] = {"worker_threads_ok": False, "event_loop_lag_ms": -1.0, "schema_ok": False, "active_jobs": 0}
    try:
        started = time.perf_counter()
        WORKERS.executor.submit(lambda: None).result(timeout=5)
        probe["event_loop_lag_ms"] = (time.perf_counter() - started) * 1000.0
        probe["worker_threads_ok"] = True
    except Exception:
        probe["worker_threads_ok"] = False
    try:
        row = DB.execute("PRAGMA quick_check").fetchone()
        probe["schema_ok"] = bool(row and row[0] == "ok")
    except Exception:
        probe["schema_ok"] = False
    try:
        row = DB.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('queued','running','cancelling')").fetchone()
        probe["active_jobs"] = int(row[0]) if row else 0
    except Exception:
        pass
    return probe


@app.get("/api/doctor/health")
def doctor_health() -> dict[str, Any]:
    return build_health_payload(
        DB,
        GATEWAY,
        public_media_health(),
        2,
        engine_probe=_engine_probe(),
        backend_port=_backend_port(),
        frontend_heartbeat_at=_FRONTEND_HEARTBEAT_AT,
    )


@app.post("/api/doctor/check")
def doctor_check() -> dict[str, Any]:
    payload = build_health_payload(
        DB,
        GATEWAY,
        public_media_health(),
        2,
        engine_probe=_engine_probe(),
        backend_port=_backend_port(),
        frontend_heartbeat_at=_FRONTEND_HEARTBEAT_AT,
    )
    recs = recommendations(payload["checks"])
    record_incidents(DB, payload["checks"], recs)
    payload["recommendations"] = recs
    return payload


@app.get("/api/doctor/incidents")
def doctor_incident_list(limit: int = 50) -> list[dict[str, Any]]:
    return list_incidents(DB, limit)


@app.post("/api/gateway/invoke")
def gateway_invoke(payload: ModelInvokeIn) -> dict[str, Any]:
    try:
        MONITOR.authorize(project_id=payload.project_id, operation=f"ModelGateway:{payload.capability}")
        MONITOR.record_spend(project_id=payload.project_id, amount=0.01, operation=f"ModelGateway:{payload.capability}")
        result = GATEWAY.invoke(payload.capability, payload.payload, payload.idempotency_key)
        EVENTS.audit(category="model-gateway", message="Model Gateway invocation", route="gateway", operation=payload.capability, status="cached" if result.cached else "ok", metadata={"provider": result.provider, "model_id": result.model_id, "idempotency_key": payload.idempotency_key})
        return result.as_dict()
    except MonitorBlocked as exc:
        EVENTS.audit(category="monitor", message=exc.message, project_id=payload.project_id, route="gateway", operation="authorize", status="HALTED", metadata={"code": exc.code})
        raise HTTPException(status_code=423, detail={"code": exc.code, "message": exc.message}) from exc
    except GatewayError as exc:
        EVENTS.audit(category="model-gateway", message=exc.message, route="gateway", operation=payload.capability, status="error", output_summary={"code": exc.code, "retryable": exc.retryable})
        raise HTTPException(status_code=409 if exc.code == "INVALID_IDEMPOTENCY_KEY" else 422, detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable}) from exc


@app.get("/api/installation")
def installation() -> dict[str, Any]:
    row = DB.execute("SELECT installation_id,first_seen_at,onboarding_completed,onboarding_skipped FROM installation_state WHERE id=1").fetchone()
    return {"installation_id": row["installation_id"], "first_seen_at": row["first_seen_at"], "onboarding_completed": bool(row["onboarding_completed"]), "onboarding_skipped": bool(row["onboarding_skipped"]), "show_onboarding": not bool(row["onboarding_completed"] or row["onboarding_skipped"])}


@app.post("/api/installation/onboarding")
def onboarding(payload: OnboardingIn) -> dict[str, Any]:
    DB.execute("UPDATE installation_state SET onboarding_completed=?, onboarding_skipped=? WHERE id=1", (int(payload.completed), int(payload.skipped)))
    EVENTS.audit(category="installation", message="Onboarding state updated", route="setup", operation="update", output_summary=payload.model_dump())
    return installation()


@app.get("/api/monitor/slo")
def monitor_slo() -> list[dict[str, Any]]:
    return rows_to_dicts(DB.execute("SELECT * FROM slo_policies WHERE enabled=1 ORDER BY job_type").fetchall())


@app.get("/api/tools")
def tools(project_id: str = "project_northstar") -> list[dict[str, Any]]:
    ensure_project(project_id)
    return rows_to_dicts(DB.execute("SELECT * FROM tool_lifecycle WHERE project_id=? ORDER BY updated_at DESC", (project_id,)).fetchall())


@app.post("/api/tools")
def register_tool(payload: ToolIn) -> dict[str, Any]:
    tool_id = new_id("tool")
    now = now_iso()
    DB.execute("INSERT INTO tool_lifecycle(id,project_id,name,version,state,scope,security_status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (tool_id, "project_northstar", payload.name, payload.version, "CREATED", payload.scope, "UNREVIEWED", now, now))
    EVENTS.audit(category="tool-lifecycle", message="Tool registered", project_id="project_northstar", route="tools", operation="create", status="CREATED", metadata={"tool_id": tool_id, "actor": "local-user"})
    return {"tool_id": tool_id, "state": "CREATED", "security_status": "UNREVIEWED"}


@app.post("/api/tools/{tool_id}/transition")
def transition_tool(tool_id: str, payload: ToolTransitionIn) -> dict[str, Any]:
    validate_identifier(tool_id, "tool_id")
    row = DB.execute("SELECT * FROM tool_lifecycle WHERE id=?", (tool_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "TOOL_NOT_FOUND", "message": "Tool was not found."})
    allowed = {"CREATED": {"TESTING", "RETIRED"}, "TESTING": {"ACTIVE", "REVIEW", "RETIRED"}, "ACTIVE": {"REVIEW", "DEPRECATED"}, "REVIEW": {"ACTIVE", "DEPRECATED", "RETIRED"}, "DEPRECATED": {"RETIRED"}, "RETIRED": set()}
    if payload.state not in allowed.get(row["state"], set()):
        raise HTTPException(status_code=409, detail={"code": "TOOL_TRANSITION_FORBIDDEN", "message": f"Tool cannot transition from {row['state']} to {payload.state}."})
    now = now_iso()
    security_status = "REVIEW_REQUIRED" if payload.state in {"ACTIVE", "REVIEW"} else row["security_status"]
    DB.execute("UPDATE tool_lifecycle SET state=?,security_status=?,last_audited_at=?,updated_at=? WHERE id=?", (payload.state, security_status, now, now, tool_id))
    EVENTS.audit(category="tool-lifecycle", message="Tool lifecycle transition", project_id=row["project_id"], route="tools", operation="transition", status=payload.state, metadata={"tool_id": tool_id, "actor": payload.actor, "reason": payload.reason})
    return {"tool_id": tool_id, "state": payload.state, "security_status": security_status}


@app.post("/api/promotions")
def create_promotion(payload: PromotionIn) -> dict[str, Any]:
    project_id = "project_northstar"
    review_id = new_id("promotion")
    DB.execute("INSERT INTO promotion_reviews(id,project_id,candidate_id,candidate_type,evaluation_outcome,status,decision_reason,rollback_target,created_at) VALUES(?,?,?,?,?,?,?,?,?)", (review_id, project_id, payload.candidate_id, payload.candidate_type, payload.evaluation_outcome, "PENDING_HUMAN", dump_json({"evidence": payload.evidence}), payload.rollback_target, now_iso()))
    EVENTS.audit(category="promotion", message="Promotion recommendation created", project_id=project_id, route="adaptation", operation="recommend", status="PENDING_HUMAN", metadata={"review_id": review_id, "candidate_id": payload.candidate_id, "outcome": payload.evaluation_outcome, "human_required": True})
    return {"review_id": review_id, "status": "PENDING_HUMAN", "evaluation_outcome": payload.evaluation_outcome, "human_required": True}


@app.get("/api/promotions")
def promotions(project_id: str = "project_northstar") -> list[dict[str, Any]]:
    ensure_project(project_id)
    return rows_to_dicts(DB.execute("SELECT * FROM promotion_reviews WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())


@app.post("/api/promotions/{review_id}/decision")
def promotion_decision(review_id: str, payload: PromotionDecisionIn) -> dict[str, Any]:
    validate_identifier(review_id, "review_id")
    row = DB.execute("SELECT * FROM promotion_reviews WHERE id=?", (review_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PROMOTION_REVIEW_NOT_FOUND", "message": "Promotion review was not found."})
    if row["status"] != "PENDING_HUMAN":
        raise HTTPException(status_code=409, detail={"code": "PROMOTION_ALREADY_DECIDED", "message": "This promotion review already has a human decision."})
    final_outcome = "HUMAN_OVERRIDE" if payload.decision == "OVERRIDE" else row["evaluation_outcome"]
    status = "APPROVED" if payload.decision in {"APPROVE", "OVERRIDE"} else "REJECTED"
    now = now_iso()
    DB.execute("UPDATE promotion_reviews SET evaluation_outcome=?,status=?,reviewer_id=?,decision_reason=?,rollback_target=?,decided_at=? WHERE id=?", (final_outcome, status, payload.reviewer_id, payload.reason, payload.rollback_target or row["rollback_target"], now, review_id))
    EVENTS.audit(category="promotion", message="Human promotion decision recorded", project_id=row["project_id"], route="adaptation", operation=payload.decision.lower(), status=status, metadata={"review_id": review_id, "reviewer_id": payload.reviewer_id, "evaluation_outcome": final_outcome, "rollback_target": payload.rollback_target or row["rollback_target"]})
    return {"review_id": review_id, "status": status, "evaluation_outcome": final_outcome, "reviewer_id": payload.reviewer_id, "decided_at": now}


@app.get("/api/monitor/state")
def monitor_state() -> dict[str, Any]:
    return {**MONITOR.snapshot(), "independent": True, "human_resume_required": True}


@app.get("/api/monitor/dashboard")
def monitor_dashboard() -> dict[str, Any]:
    return MONITOR.dashboard()


@app.post("/api/monitor/halt")
def monitor_halt(payload: MonitorHaltIn) -> dict[str, Any]:
    return MONITOR.halt(payload.reason, payload.actor)


@app.post("/api/monitor/resume")
def monitor_resume(payload: MonitorResumeIn) -> dict[str, Any]:
    try:
        return MONITOR.resume(actor=payload.actor, human_confirmed=payload.human_confirmed)
    except MonitorBlocked as exc:
        raise HTTPException(status_code=403, detail={"code": exc.code, "message": exc.message}) from exc


@app.get("/api/projects/{project_id}/consent")
def get_consent(project_id: str) -> dict[str, Any]:
    ensure_project(project_id)
    return consent_for(project_id)


@app.put("/api/projects/{project_id}/consent")
def update_consent(project_id: str, payload: ConsentIn) -> dict[str, Any]:
    ensure_project(project_id)
    if payload.consent_state == "GRANTED" and payload.identity_source == "UNSPECIFIED" and (payload.face_consent or payload.likeness_consent or payload.voice_consent):
        raise HTTPException(status_code=422, detail={"code": "CONSENT_IDENTITY_SOURCE_REQUIRED", "message": "Identity source is required for granted likeness or voice consent."})
    now = now_iso()
    likeness_consent = payload.likeness_consent or payload.face_consent
    DB.execute("UPDATE consent_policies SET learning_scope=?,face_consent=?,likeness_consent=?,voice_consent=?,identity_source=?,consent_state=?,project_scope=?,consent_version=?,retention_days=?,updated_at=? WHERE project_id=?", (payload.learning_scope, int(payload.face_consent), int(likeness_consent), int(payload.voice_consent), payload.identity_source, payload.consent_state, payload.project_scope, "consent.v1", payload.retention_days, now, project_id))
    EVENTS.audit(category="guard", message="Consent and learning scope updated", project_id=project_id, route="guard", operation="consent", status=payload.consent_state, output_summary=payload.model_dump())
    return consent_for(project_id)


@app.get("/api/jobs/{job_id}/guard")
def job_guard(job_id: str) -> list[dict[str, Any]]:
    validate_identifier(job_id, "job_id")
    if DB.execute("SELECT 1 FROM jobs WHERE id=?", (job_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job was not found."})
    return rows_to_dicts(DB.execute("SELECT * FROM guard_decisions WHERE job_id=? ORDER BY created_at", (job_id,)).fetchall())


@app.get("/api/guard/policies")
def guard_policies() -> dict[str, Any]:
    return {"policy_version": GUARD.policy_version, "policies": GUARD.policy_definitions, "trust_classes": sorted(GUARD_POLICY_TRUST_CLASSES), "supported_modalities": sorted(GUARD_SUPPORTED_MODALITIES)}


@app.get("/api/projects/{project_id}/guard/reviews")
def guard_reviews(project_id: str) -> list[dict[str, Any]]:
    ensure_project(project_id)
    return rows_to_dicts(DB.execute("SELECT * FROM guard_reviews WHERE project_id=? ORDER BY created_at DESC", (project_id,)).fetchall())


@app.post("/api/guard/reviews/{review_id}/decision")
def guard_review_decision(review_id: str, payload: GuardReviewDecisionIn) -> dict[str, Any]:
    validate_identifier(review_id, "review_id")
    row = DB.execute("SELECT * FROM guard_reviews WHERE id=?", (review_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "GUARD_REVIEW_NOT_FOUND", "message": "Guard review was not found."})
    if row["status"] != "PENDING_HUMAN":
        raise HTTPException(status_code=409, detail={"code": "GUARD_REVIEW_ALREADY_DECIDED", "message": "This Guard review already has a human decision."})
    now = now_iso()
    status = "APPROVED" if payload.decision == "APPROVE" else "REJECTED"
    DB.execute("UPDATE guard_reviews SET decision=?,reviewer_id=?,reviewer_note=?,status=?,decided_at=? WHERE id=?", (payload.decision, payload.reviewer_id, payload.note, status, now, review_id))
    EVENTS.audit(category="guard", message="Guard human review decision recorded", project_id=row["project_id"], job_id=row["job_id"], artifact_id=row["artifact_id"], decision_id=row["decision_id"], route="guard", operation="review", status=status, metadata={"review_id": review_id, "reviewer_id": payload.reviewer_id, "decision": payload.decision})
    return {"review_id": review_id, "status": status, "decision": payload.decision, "reviewer_id": payload.reviewer_id, "decided_at": now}


@app.post("/api/artifacts/{artifact_id}/publish")
def publish_artifact(artifact_id: str, payload: ArtifactPublishIn) -> dict[str, Any]:
    validate_identifier(artifact_id, "artifact_id")
    ensure_project(payload.project_id)
    artifact = DB.execute("SELECT * FROM artifacts WHERE id=? AND project_id=?", (artifact_id, payload.project_id)).fetchone()
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": "Artifact was not found for this project."})
    try:
        artifact_metadata = json.loads(artifact["metadata_json"] or "{}")
    except json.JSONDecodeError:
        artifact_metadata = {"metadata_parse_error": True}
    consent = consent_for(payload.project_id)
    summary = json.dumps({"artifact_id": artifact_id, "name": artifact["name"], "status": artifact["status"], "metadata": artifact_metadata}, ensure_ascii=False)
    decision = GUARD.publish_gate(artifact_summary=summary, modality=payload.modality, consent=consent)
    decision_id = record_guard(payload.project_id, None, decision, artifact_id=artifact_id)
    check_id = new_id("guard-publish")
    gate_status = "PASS" if decision.allowed and artifact["status"] in {"created", "ready"} else "BLOCK" if decision.status == "BLOCK" or artifact["status"] not in {"created", "ready"} else "REVIEW"
    gate_reason = decision.reason if artifact["status"] in {"created", "ready"} else "Artifact status is not publishable under the local Guard gate."
    DB.execute("INSERT INTO guard_publish_checks(id,artifact_id,project_id,decision_id,status,policy_version,reason,created_at) VALUES(?,?,?,?,?,?,?,?)", (check_id, artifact_id, payload.project_id, decision_id, gate_status, decision.policy_version, gate_reason, now_iso()))
    if gate_status != "PASS":
        EVENTS.audit(category="guard", message=gate_reason, project_id=payload.project_id, artifact_id=artifact_id, decision_id=decision_id, route="publish", operation="publish", status=gate_status, metadata={"publish_gate": True, "policy_version": decision.policy_version})
        code = "GUARD_PUBLISH_BLOCKED" if gate_status == "BLOCK" else "GUARD_PUBLISH_REVIEW_REQUIRED"
        raise HTTPException(status_code=423, detail={"code": code, "message": gate_reason, "decision_id": decision_id, "policy_version": decision.policy_version})
    DB.execute("UPDATE artifacts SET status='published' WHERE id=? AND project_id=?", (artifact_id, payload.project_id))
    EVENTS.audit(category="guard", message="Artifact passed Guard publish gate", project_id=payload.project_id, artifact_id=artifact_id, decision_id=decision_id, route="publish", operation="publish", status="PUBLISHED", metadata={"publish_gate": True, "policy_version": decision.policy_version})
    return {"artifact_id": artifact_id, "status": "PUBLISHED", "guard_status": decision.status, "decision_id": decision_id, "publish_check_id": check_id, "policy_version": decision.policy_version}


def publish_prep_guard(project_id: str, artifact: sqlite3.Row, modality: str) -> tuple[Any, str, str]:
    """Run the authoritative Guard publish gate and record it, mirroring /publish.

    Returns (decision, decision_id, gate_status). Guard review stays
    authoritative: nothing proceeds to prep on anything but PASS.
    """
    try:
        artifact_metadata = json.loads(artifact["metadata_json"] or "{}")
    except json.JSONDecodeError:
        artifact_metadata = {"metadata_parse_error": True}
    consent = consent_for(project_id)
    summary = json.dumps({"artifact_id": artifact["id"], "name": artifact["name"], "status": artifact["status"], "metadata": artifact_metadata}, ensure_ascii=False)
    decision = GUARD.publish_gate(artifact_summary=summary, modality=modality, consent=consent)
    decision_id = record_guard(project_id, None, decision, artifact_id=artifact["id"])
    check_id = new_id("guard-publish")
    gate_status = "PASS" if decision.allowed and artifact["status"] in {"created", "ready"} else "BLOCK" if decision.status == "BLOCK" or artifact["status"] not in {"created", "ready"} else "REVIEW"
    gate_reason = decision.reason if artifact["status"] in {"created", "ready"} else "Artifact status is not publishable under the local Guard gate."
    DB.execute("INSERT INTO guard_publish_checks(id,artifact_id,project_id,decision_id,status,policy_version,reason,created_at) VALUES(?,?,?,?,?,?,?,?)", (check_id, artifact["id"], project_id, decision_id, gate_status, decision.policy_version, gate_reason, now_iso()))
    return decision, decision_id, gate_status


def publish_prep_run_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "run_id": row["id"],
        "project_id": row["project_id"],
        "artifact_id": row["artifact_id"],
        "platform": row["platform"],
        "status": row["status"],
        "steps": json.loads(row["steps_json"] or "[]"),
        "bundle_path": row["bundle_path"],
        "guard_decision_id": row["guard_decision_id"],
        "error_code": row["error_code"],
        "error_message": row["error_message"],
        "receipt": json.loads(row["receipt_json"] or "{}"),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


@app.get("/api/publish/adapters")
def publish_adapters() -> list[dict[str, Any]]:
    return PUBLISH_ADAPTERS.statuses()


@app.post("/api/projects/{project_id}/publish/prep")
def publish_prep_start(project_id: str, payload: PublishPrepStartIn) -> dict[str, Any]:
    validate_identifier(project_id, "project_id")
    validate_identifier(payload.artifact_id, "artifact_id")
    ensure_project(project_id)
    try:
        adapter = PUBLISH_ADAPTERS.get(payload.platform)
    except PublishError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
    artifact = DB.execute("SELECT * FROM artifacts WHERE id=? AND project_id=?", (payload.artifact_id, project_id)).fetchone()
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": "Artifact was not found for this project."})
    decision, decision_id, gate_status = publish_prep_guard(project_id, artifact, payload.modality)
    run_id = new_id("publish-prep")
    now = now_iso()
    if gate_status != "PASS":
        code = "GUARD_PUBLISH_BLOCKED" if gate_status == "BLOCK" else "GUARD_PUBLISH_REVIEW_REQUIRED"
        DB.execute("INSERT INTO publish_prep_runs(id,project_id,artifact_id,platform,status,steps_json,bundle_path,guard_decision_id,error_code,error_message,receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, project_id, artifact["id"], adapter.platform_id(), "blocked", dump_json([{"name": "guard_gate", "status": "blocked", "detail": decision.reason, "artifacts": []}]), None, decision_id, code, decision.reason, "{}", now, now))
        EVENTS.audit(category="publish", message="Publish prep blocked at the Guard gate", project_id=project_id, artifact_id=artifact["id"], decision_id=decision_id, route="publish", operation="prep", status="blocked", metadata={"gate_status": gate_status, "run_id": run_id})
        raise HTTPException(status_code=423, detail={"code": code, "message": decision.reason, "decision_id": decision_id, "run_id": run_id})
    try:
        artifact_metadata = json.loads(artifact["metadata_json"] or "{}")
    except json.JSONDecodeError:
        artifact_metadata = {}
    result = PUBLISH_PIPELINE.run(
        run_id=run_id,
        artifact={"id": artifact["id"], "name": artifact["name"], "status": artifact["status"], "path": artifact["path"], "media_path": artifact_metadata.get("media_path") or artifact["path"], "metadata": artifact_metadata},
        modality=payload.modality,
        consent=consent_for(project_id),
        overrides={"title": payload.title, "description": payload.description, "tags": payload.tags, "privacy": payload.privacy},
        guard_decision=decision,
    )
    DB.execute("INSERT INTO publish_prep_runs(id,project_id,artifact_id,platform,status,steps_json,bundle_path,guard_decision_id,error_code,error_message,receipt_json,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (run_id, project_id, artifact["id"], adapter.platform_id(), result.status, dump_json([step.as_dict() for step in result.steps]), result.bundle_path, decision_id, None if result.status == "completed" else "PREP_RUN_BLOCKED", None, "{}", now, now_iso()))
    EVENTS.audit(category="publish", message="Publish prep run finished", project_id=project_id, artifact_id=artifact["id"], decision_id=decision_id, route="publish", operation="prep", status=result.status, metadata={"run_id": run_id, "platform": adapter.platform_id(), "bundle_path": result.bundle_path})
    return publish_prep_run_dict(DB.execute("SELECT * FROM publish_prep_runs WHERE id=?", (run_id,)).fetchone())


@app.get("/api/projects/{project_id}/publish/prep")
def publish_prep_list(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    validate_identifier(project_id, "project_id")
    ensure_project(project_id)
    bounded = max(1, min(int(limit), 100))
    rows = DB.execute("SELECT * FROM publish_prep_runs WHERE project_id=? ORDER BY created_at DESC LIMIT ?", (project_id, bounded)).fetchall()
    return [publish_prep_run_dict(row) for row in rows]


@app.get("/api/publish/prep/{run_id}")
def publish_prep_get(run_id: str) -> dict[str, Any]:
    validate_identifier(run_id, "run_id")
    row = DB.execute("SELECT * FROM publish_prep_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PUBLISH_PREP_RUN_NOT_FOUND", "message": "Publish prep run was not found."})
    return publish_prep_run_dict(row)


@app.post("/api/publish/prep/{run_id}/upload")
def publish_prep_upload(run_id: str) -> dict[str, Any]:
    validate_identifier(run_id, "run_id")
    row = DB.execute("SELECT * FROM publish_prep_runs WHERE id=?", (run_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "PUBLISH_PREP_RUN_NOT_FOUND", "message": "Publish prep run was not found."})
    if row["status"] != "completed":
        raise HTTPException(status_code=409, detail={"code": "PUBLISH_PREP_NOT_READY", "message": "Only a completed prep run can be handed to a platform adapter."})
    try:
        adapter = PUBLISH_ADAPTERS.get(row["platform"])
    except PublishError as exc:
        raise HTTPException(status_code=404, detail={"code": exc.code, "message": exc.message}) from exc
    try:
        manifest = json.loads((Path(row["bundle_path"]).parent / row["id"] / "manifest.json").read_text(encoding="utf-8")) if row["bundle_path"] else {}
    except (OSError, json.JSONDecodeError):
        manifest = {}
    bundle = {
        "run_id": row["id"],
        "title": manifest.get("title") or "Untitled Flujo export",
        "description": manifest.get("description") or "",
        "tags": manifest.get("tags") or [],
        "privacy": manifest.get("privacy") or "unlisted",
        "media_path": row["bundle_path"],
        "bundle_path": row["bundle_path"],
    }
    try:
        receipt = adapter.upload(bundle)
    except PublishError as exc:
        status_code = 423 if exc.code in {"ADAPTER_DISABLED", "ADAPTER_CREDENTIAL_UNAVAILABLE"} else 422 if exc.code in {"BUNDLE_INVALID"} else 502
        EVENTS.audit(category="publish", message="Platform upload refused or failed", project_id=row["project_id"], artifact_id=row["artifact_id"], route="publish", operation="upload", status="failed", metadata={"run_id": run_id, "platform": row["platform"], "code": exc.code})
        raise HTTPException(status_code=status_code, detail={"code": exc.code, "message": exc.message, "retryable": exc.retryable}) from exc
    DB.execute("UPDATE publish_prep_runs SET status='uploaded',receipt_json=?,updated_at=? WHERE id=?", (dump_json(receipt), now_iso(), run_id))
    EVENTS.audit(category="publish", message="Platform upload completed", project_id=row["project_id"], artifact_id=row["artifact_id"], route="publish", operation="upload", status="uploaded", metadata={"run_id": run_id, "platform": row["platform"]})
    return {"run_id": run_id, "status": "uploaded", "platform": row["platform"], "receipt": receipt}


@app.get("/api/projects/{project_id}/conversations")
def conversations(project_id: str) -> list[dict[str, Any]]:
    ensure_project(project_id)
    return rows_to_dicts(DB.execute("SELECT id,title,pinned,archived,created_at,updated_at FROM conversations WHERE project_id=? AND archived=0 ORDER BY pinned DESC,updated_at DESC", (project_id,)).fetchall())


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str) -> dict[str, Any]:
    validate_identifier(conversation_id, "conversation_id")
    row = DB.execute("SELECT id,project_id,campaign_id,title,pinned,archived,created_at,updated_at FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation was not found."})
    result = dict(row)
    result["messages"] = rows_to_dicts(DB.execute("SELECT id,role,content,created_at,metadata_json FROM messages WHERE conversation_id=? ORDER BY created_at", (conversation_id,)).fetchall())
    return result


@app.post("/api/conversations/{conversation_id}/messages", status_code=202)
def message(conversation_id: str, payload: MessageIn) -> dict[str, Any]:
    validate_identifier(conversation_id, "conversation_id")
    row = DB.execute("SELECT id,project_id FROM conversations WHERE id=?", (conversation_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation was not found."})
    try:
        MONITOR.authorize(project_id=payload.project_id, operation="CreativeBrief")
    except MonitorBlocked as exc:
        EVENTS.audit(category="monitor", message=exc.message, project_id=payload.project_id, route="chat", operation="authorize", status="HALTED", metadata={"code": exc.code})
        raise HTTPException(status_code=423, detail={"code": exc.code, "message": exc.message}) from exc
    request_fingerprint = hashlib.sha256(dump_json({"conversation_id": conversation_id, "content": payload.content, "attachments": [item.model_dump() for item in payload.attachments], "project_id": payload.project_id, "campaign_id": payload.campaign_id}).encode()).hexdigest()
    idempotency_key = payload.idempotency_key or request_fingerprint
    with LOCK:
        existing = REPOSITORIES.jobs.find_by_idempotency(idempotency_key)
        if existing:
            if existing["request_fingerprint"] and existing["request_fingerprint"] != request_fingerprint:
                EVENTS.audit(category="idempotency", message="Conflicting idempotency key rejected", project_id=payload.project_id, job_id=existing["id"], route="chat", operation="queue", status="denied", metadata={"idempotency_key": idempotency_key})
                raise HTTPException(status_code=409, detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": "The idempotency key is already associated with a different request."})
            trace = set_trace_id()
            EVENTS.audit(category="idempotency", message="Existing Creative brief returned", project_id=payload.project_id, job_id=existing["id"], route="chat", operation="reuse", status="cached", metadata={"idempotency_key": idempotency_key})
            return {"job_id": existing["id"], "workflow": "CreativeBrief", "status": existing["status"], "checkpoint": existing["checkpoint"], "progress": existing["progress"], "trace_id": trace, "cached": True}
        now = now_iso()
        job_id = REPOSITORIES.jobs.create_message_job(conversation_id, payload.project_id, payload.content, [item.model_dump() for item in payload.attachments], idempotency_key, request_fingerprint, now)
    trace = set_trace_id()
    EVENTS.audit(category="workflow", message="Creative brief queued", project_id=payload.project_id, job_id=job_id, route="chat", operation="queue", input_summary=payload.model_dump(), metadata={"trace_id": trace, "idempotency_key": idempotency_key})
    WORKERS.submit(job_id, run_vertical_slice, job_id, conversation_id, payload)
    return {"job_id": job_id, "workflow": "CreativeBrief", "status": "queued", "trace_id": trace, "cached": False}


@app.get("/api/jobs")
def jobs(project_id: str = "project_northstar", limit: int = 50) -> list[dict[str, Any]]:
    ensure_project(project_id)
    return REPOSITORIES.jobs.list(project_id, limit)


@app.get("/api/jobs/{job_id}")
def job(job_id: str) -> dict[str, Any]:
    validate_identifier(job_id, "job_id")
    result = REPOSITORIES.jobs.get(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job was not found."})
    return result


@app.post("/api/jobs/{job_id}/requeue")
def requeue_job(job_id: str, payload: RecoveryRequeueIn) -> dict[str, Any]:
    validate_identifier(job_id, "job_id")
    row = DB.execute("SELECT id,project_id,status,checkpoint,progress,checkpoint_data_json FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job was not found."})
    if row["status"] != "recovery_required":
        raise HTTPException(status_code=409, detail={"code": "JOB_NOT_RECOVERY_REQUIRED", "message": "Only quarantined jobs can be manually requeued."})
    if not payload.confirm:
        raise HTTPException(status_code=409, detail={"code": "RECOVERY_CONFIRMATION_REQUIRED", "message": "Explicit confirmation is required before requesting a recovery requeue."})
    try:
        checkpoint_data = json.loads(row["checkpoint_data_json"] or "{}")
    except json.JSONDecodeError:
        checkpoint_data = {}
    if not isinstance(checkpoint_data, dict):
        checkpoint_data = {}
    now = now_iso()
    checkpoint_data.update({"requeue_requested": True, "requeue_requested_at": now, "requeue_actor": payload.actor, "requeue_reason": payload.reason, "automatic_resume": False, "side_effect_replay_requires_review": True})
    DB.execute("UPDATE jobs SET status='queued',checkpoint='HumanRequeueRequested',error_code=NULL,error_message=NULL,checkpoint_data_json=?,updated_at=? WHERE id=? AND status='recovery_required'", (dump_json(checkpoint_data), now, job_id))
    EVENTS.audit(category="workflow-recovery", message="Human recovery requeue requested; execution remains paused for review", project_id=row["project_id"], job_id=job_id, route="jobs", operation="requeue", status="queued", metadata={"actor": payload.actor, "reason": payload.reason, "previous_status": row["status"], "automatic_resume": False, "side_effect_replay_requires_review": True})
    return {"job_id": job_id, "status": "queued", "checkpoint": "HumanRequeueRequested", "automatic_resume": False, "side_effect_replay_requires_review": True}


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str) -> dict[str, Any]:
    validate_identifier(job_id, "job_id")
    row = DB.execute("SELECT id,status,checkpoint,progress,project_id FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job was not found."})
    if row["status"] in {"completed", "blocked", "failed", "cancelled"}:
        return {"job_id": job_id, "status": row["status"], "cancelled": False, "reason": "Job is already terminal."}
    accepted = WORKERS.cancel(job_id)
    set_job(job_id, "cancelling" if accepted else "cancelled", row["checkpoint"], row["progress"], "WORKFLOW_CANCEL_REQUESTED", "Cancellation requested by the local control plane.")
    EVENTS.audit(category="workflow", message="Workflow cancellation requested", project_id=row["project_id"], job_id=job_id, route="jobs", operation="cancel", status="accepted" if accepted else "not-running")
    return {"job_id": job_id, "status": "cancelling" if accepted else "cancelled", "cancelled": accepted}


@app.get("/api/jobs/{job_id}/replay")
def replay(job_id: str) -> list[dict[str, Any]]:
    validate_identifier(job_id, "job_id")
    return rows_to_dicts(DB.execute("SELECT id,timestamp,severity,category,message,trace_id,request_id,project_id,workflow_id,job_id,artifact_id,attempt_id,decision_id,route,operation,duration_ms,status,input_summary,output_summary,metadata_json FROM audit_events WHERE job_id=? ORDER BY timestamp", (job_id,)).fetchall())


@app.get("/api/artifacts/{artifact_id}/lineage")
def artifact_lineage(artifact_id: str) -> dict[str, Any]:
    validate_identifier(artifact_id, "artifact_id")
    artifact = REPOSITORIES.artifacts.get_with_lineage(artifact_id)
    if artifact is None:
        raise HTTPException(status_code=404, detail={"code": "ARTIFACT_NOT_FOUND", "message": "Artifact was not found."})
    lineage = artifact.pop("lineage", [])
    return {"artifact": artifact, "lineage": lineage}


@app.get("/api/diagnostics/errors")
def errors(limit: int = 100) -> list[dict[str, Any]]:
    return REPOSITORIES.incidents.list(limit)


@app.get("/api/diagnostics/events")
def events(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(limit, 500))
    return rows_to_dicts(DB.execute("SELECT * FROM audit_events ORDER BY timestamp DESC LIMIT ?", (safe_limit,)).fetchall())


@app.get("/api/providers")
def providers() -> list[dict[str, Any]]:
    return rows_to_dicts(DB.execute("SELECT id,name,masked_key,status,last_tested_at,created_at,updated_at FROM providers ORDER BY name").fetchall())


def _credential_from_row(row: sqlite3.Row) -> CredentialMetadata:
    return CredentialMetadata(row["id"], row["provider"], row["display_name"], row["masked_value"], row["scope"], row["status"], row["blob_path"], row["created_at"], row["updated_at"])


def _credential_public(metadata: CredentialMetadata) -> dict[str, Any]:
    return {key: value for key, value in metadata.as_dict().items() if key != "blob_path"}


@app.get("/api/credentials")
def credentials() -> list[dict[str, Any]]:
    return REPOSITORIES.credentials.list()


@app.post("/api/credentials")
def create_credential(payload: CredentialIn) -> dict[str, Any]:
    now = now_iso()
    try:
        metadata = CREDENTIALS.create(payload.provider, payload.display_name, payload.secret, payload.scope, now)
        DB.execute("INSERT INTO credentials(id,provider,display_name,masked_value,scope,status,blob_path,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)", (metadata.credential_id, metadata.provider, metadata.display_name, metadata.masked_value, metadata.scope, metadata.status, metadata.blob_path, metadata.created_at, metadata.updated_at))
        EVENTS.audit(category="credentials", message="Credential metadata created in secure store", route="settings/credentials", operation="create", output_summary=_credential_public(metadata))
        return _credential_public(metadata)
    except CredentialStoreError as exc:
        EVENTS.error(source="credential-store", message=exc.message, route="settings/credentials", metadata={"code": exc.code, "provider": payload.provider})
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/credentials/{credential_id}/rotate")
def rotate_credential(credential_id: str, payload: CredentialIn) -> dict[str, Any]:
    validate_identifier(credential_id, "credential_id")
    row = DB.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CREDENTIAL_NOT_FOUND", "message": "Credential was not found."})
    try:
        now = now_iso()
        metadata = CREDENTIALS.rotate(_credential_from_row(row), payload.secret, now)
        DB.execute("UPDATE credentials SET masked_value=?,status=?,updated_at=? WHERE id=?", (metadata.masked_value, metadata.status, metadata.updated_at, credential_id))
        EVENTS.audit(category="credentials", message="Credential rotated", route="settings/credentials", operation="rotate", output_summary=_credential_public(metadata))
        return _credential_public(metadata)
    except CredentialStoreError as exc:
        EVENTS.error(source="credential-store", message=exc.message, route="settings/credentials", metadata={"code": exc.code, "credential_id": credential_id})
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/credentials/{credential_id}/revoke")
def revoke_credential(credential_id: str) -> dict[str, Any]:
    validate_identifier(credential_id, "credential_id")
    row = DB.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CREDENTIAL_NOT_FOUND", "message": "Credential was not found."})
    try:
        metadata = CREDENTIALS.revoke(_credential_from_row(row), now_iso())
        DB.execute("UPDATE credentials SET status=?,updated_at=? WHERE id=?", (metadata.status, metadata.updated_at, credential_id))
        EVENTS.audit(category="credentials", message="Credential revoked", route="settings/credentials", operation="revoke", output_summary=_credential_public(metadata))
        return _credential_public(metadata)
    except CredentialStoreError as exc:
        EVENTS.error(source="credential-store", message=exc.message, route="settings/credentials", metadata={"code": exc.code, "credential_id": credential_id})
        raise HTTPException(status_code=503, detail={"code": exc.code, "message": exc.message}) from exc


@app.post("/api/credentials/{credential_id}/test")
def test_credential(credential_id: str) -> dict[str, Any]:
    validate_identifier(credential_id, "credential_id")
    row = DB.execute("SELECT * FROM credentials WHERE id=?", (credential_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail={"code": "CREDENTIAL_NOT_FOUND", "message": "Credential was not found."})
    metadata = _credential_from_row(row)
    if metadata.status != "active":
        return {"credential_id": credential_id, "status": "blocked", "code": "CREDENTIAL_REVOKED", "provider_call": False}
    try:
        CREDENTIALS.read(metadata)
        EVENTS.audit(category="credentials", message="Credential vault access verified locally", route="settings/credentials", operation="test", output_summary={"credential_id": credential_id, "provider": metadata.provider, "provider_call": False})
        return {"credential_id": credential_id, "status": "verified_local_store", "provider_call": False}
    except CredentialStoreError as exc:
        EVENTS.error(source="credential-store", message=exc.message, route="settings/credentials", metadata={"code": exc.code, "credential_id": credential_id})
        return {"credential_id": credential_id, "status": "error", "code": exc.code, "provider_call": False}


@app.post("/api/providers")
def add_provider(payload: ProviderIn) -> dict[str, Any]:
    masked = f"{payload.api_key[:3]}••••••••{payload.api_key[-3:]}" if len(payload.api_key) >= 8 else "••••••••"
    provider_id = new_id("provider")
    now = now_iso()
    DB.execute("INSERT INTO providers(id,name,masked_key,status,last_tested_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?)", (provider_id, payload.name, masked, "configured", None, now, now))
    EVENTS.audit(category="credentials", message="Provider added with secret redaction", route="settings/providers", operation="create", output_summary={"provider": payload.name, "masked_key": masked})
    return {"id": provider_id, "name": payload.name, "masked_key": masked, "status": "configured"}


class FrontendDiagnostic(StrictModel):
    category: str = "renderer"
    message: str = Field(default="frontend diagnostic", max_length=1000)
    output_summary: dict[str, Any] = Field(default_factory=dict)
    route: str | None = None


@app.post("/api/diagnostics/events", status_code=202)
def frontend_diagnostic(payload: FrontendDiagnostic) -> dict[str, Any]:
    global _FRONTEND_HEARTBEAT_AT
    _FRONTEND_HEARTBEAT_AT = now_iso()
    event_id = EVENTS.audit(category=payload.category, message=payload.message, route=payload.route or "renderer", operation="frontend-diagnostic", status="error" if payload.category in {"renderer-error", "unhandled-rejection"} else "ok", output_summary=payload.output_summary)
    return {"accepted": True, "event_id": event_id, "trace_id": current_trace_id()}


# M11: frontend de-simulation endpoints. They live in extra_routes.py; this is
# the single place that wires them into the app, after all services exist.
from .extra_routes import register_extra_routes as _register_extra_routes

_register_extra_routes(app)
