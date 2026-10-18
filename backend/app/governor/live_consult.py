from __future__ import annotations

import hashlib
import json
from typing import Any

from .orchestrator import AdvisorDescriptor, GovernorBlocked, GovernorRequest
from ..gateway.contracts import GatewayError

# Live-capable capability mapping: Governor -> gateway.
# Any other capability raises GovernorBlocked exactly like the simulator does.
_CAPABILITY_MAP = {
    "text_generation": "text_generation",
    "judge": "judge",
    "vision": "vision",
    "embedding": "embedding",
}

_SIMULATOR_CAPABILITIES = {
    "text_generation",
    "judge",
    "embedding",
    "vision",
    "video_reasoning",
    "image_generation",
    "audio_transcription",
}


def simulator_consult(advisor: AdvisorDescriptor, request: GovernorRequest) -> dict[str, Any]:
    # This is the ONLY simulator path in the Governor. It is used exclusively
    # when no provider credential is configured, and it says so explicitly.
    # Live consults go through gateway_consult below via the real ModelGateway.
    if request.required_capability not in _SIMULATOR_CAPABILITIES:
        raise GovernorBlocked(
            "CONSULTATION_CAPABILITY_UNAVAILABLE",
            "The local simulator has no consultation adapter for this capability.",
        )
    digest = hashlib.sha256(f"{advisor.advisor_id}|{request.task}".encode()).hexdigest()[:16]
    return {
        "answer": f"simulated {advisor.role} recommendation for the requested task",
        "confidence": 0.76,
        "evidence": [f"simulator-evidence:{digest}"],
        "cost": advisor.cost_per_consult,
        "agreement_key": "simulator-consensus",
        "simulator_only": True,
        "reason": "no provider key configured",
    }


def _build_prompt(advisor: AdvisorDescriptor, request: GovernorRequest) -> str:
    # Deterministic structure, compact (<1500 chars).
    prompt = (
        f"You are a Governor advisor with role '{advisor.role}' (id '{advisor.advisor_id}'). "
        f"Task: {request.task[:600]} "
        f"Required capability: {request.required_capability}. "
        f"Risk level: {request.risk_level}. "
        "Respond with JSON only: "
        '{"answer": "<recommendation string>", "confidence": <0-1 number>, "evidence": ["<fact>", "..."]}.'
    )
    return prompt[:1500]


def _coerce_float(value: Any, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def _extract_output(result: Any) -> dict[str, Any]:
    # Accept GatewayResult objects (with .output) or plain dicts.
    output: Any = getattr(result, "output", None)
    if output is None and isinstance(result, dict):
        output = result.get("output", result)
    if not isinstance(output, dict):
        return {}
    # Meta-style double nesting: {"output": {"text": ...}, ...}
    inner = output.get("output")
    if isinstance(inner, dict) and ("answer" in inner or "text" in inner or "confidence" in inner or "score" in inner or "evidence" in inner):
        output = inner
    # Provider may return the requested JSON as a string inside "text"/"answer".
    for key in ("answer", "text"):
        raw = output.get(key)
        if isinstance(raw, str):
            stripped = raw.strip()
            if stripped.startswith("{") and stripped.endswith("}"):
                try:
                    decoded = json.loads(stripped)
                except (ValueError, json.JSONDecodeError):
                    decoded = None
                if isinstance(decoded, dict):
                    merged = dict(output)
                    merged.update(decoded)
                    return merged
    return output


def _invoke_gateway(gateway: Any, capability: str, payload: dict[str, Any], idempotency_key: str) -> Any:
    # ModelGateway.invoke uses the `payload` keyword (contracts.py); call
    # positionally-tolerant so mocks and adapters with either name work.
    try:
        return gateway.invoke(capability=capability, payload=payload, idempotency_key=idempotency_key)
    except TypeError as exc:
        if "payload" in str(exc) or "input" in str(exc) or "unexpected keyword" in str(exc):
            return gateway.invoke(capability=capability, input=payload, idempotency_key=idempotency_key)
        raise


def gateway_consult(gateway: Any, advisor: AdvisorDescriptor, request: GovernorRequest) -> dict[str, Any]:
    # Routing policy (M11): consults go through the real gateway whenever a
    # provider credential is configured. The labeled simulator fallback is
    # used ONLY when the gateway reports simulator_only (no provider key
    # configured). Live failures are returned honestly (success=False with the
    # gateway error code), never silently downgraded to simulation.
    try:
        health = gateway.health()
    except Exception:
        health = {}
    simulator_only = True
    credentialed = True
    if isinstance(health, dict):
        simulator_only = bool(health.get("simulator_only", True))
        credentialed = health.get("credentialed", True)
        if credentialed is None:
            # No credential probe installed (hand-built gateways, older tests):
            # preserve the historical catalog-only behavior.
            credentialed = True
    if simulator_only or not credentialed:
        return simulator_consult(advisor, request)

    mapped = _CAPABILITY_MAP.get(request.required_capability)
    if mapped is None:
        raise GovernorBlocked(
            "CONSULTATION_CAPABILITY_UNAVAILABLE",
            "The local simulator has no consultation adapter for this capability.",
        )

    prompt = _build_prompt(advisor, request)
    base_key = request.idempotency_key if request.idempotency_key else "no-key"
    idempotency_key = f"governor:{base_key}:{advisor.advisor_id}"[:200]
    payload = {"prompt": prompt, "advisor_id": advisor.advisor_id, "role": advisor.role}

    try:
        result = _invoke_gateway(gateway, mapped, payload, idempotency_key)
    except GatewayError as exc:
        return {
            "answer": "",
            "confidence": 0.0,
            "evidence": [],
            "cost": advisor.cost_per_consult,
            "agreement_key": "",
            "success": False,
            "retryable": bool(exc.retryable),
            "error_code": exc.code,
            "simulator_only": False,
        }

    output = _extract_output(result)
    raw_answer = output.get("answer", output.get("text", ""))
    answer = str(raw_answer) if raw_answer is not None else ""
    raw_conf = output.get("confidence", output.get("score", 0.5))
    confidence = _coerce_float(raw_conf, 0.5)
    confidence = max(0.0, min(confidence, 1.0))
    raw_evidence = output.get("evidence", output.get("findings", []))
    if not isinstance(raw_evidence, list):
        raw_evidence = []
    evidence = [str(item)[:1000] for item in raw_evidence if str(item).strip()][:5]
    return {
        "answer": answer,
        "confidence": confidence,
        "evidence": evidence,
        "cost": advisor.cost_per_consult,
        "agreement_key": answer.lower()[:160],
        "success": True,
        "simulator_only": False,
    }
