from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Protocol


CAPABILITIES = {
    "text_generation",
    "vision",
    "video_reasoning",
    "image_generation",
    "audio_transcription",
    "embedding",
    "judge",
}


class GatewayError(Exception):
    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ModelDescriptor:
    provider: str
    model_id: str
    version: str
    capabilities: tuple[str, ...]
    modalities: tuple[str, ...]
    fallback_group: str
    priority: int
    status: str = "healthy"
    quality_score: float = 0.82
    novelty_score: float = 0.65
    cost_score: float = 0.80
    latency_score: float = 0.75
    reliability_score: float = 0.90

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model_id": self.model_id,
            "version": self.version,
            "capabilities": list(self.capabilities),
            "modalities": list(self.modalities),
            "fallback_group": self.fallback_group,
            "priority": self.priority,
            "status": self.status,
            "objectives": {"quality": self.quality_score, "novelty": self.novelty_score, "cost": self.cost_score, "latency": self.latency_score, "reliability": self.reliability_score},
        }


@dataclass(frozen=True)
class GatewayResult:
    operation_id: str
    idempotency_key: str
    provider: str
    model_id: str
    capability: str
    output: dict[str, Any]
    cached: bool
    duration_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "idempotency_key": self.idempotency_key,
            "provider": self.provider,
            "model_id": self.model_id,
            "capability": self.capability,
            "output": self.output,
            "cached": self.cached,
            "duration_ms": self.duration_ms,
        }


class ModelProvider(Protocol):
    def catalog(self) -> list[ModelDescriptor]: ...
    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]: ...


class MockModelProvider:
    """Deterministic provider-shaped simulator for pipeline and Governor tests."""

    _models = [
        ModelDescriptor("mock", "mock-reasoner", "0.1", ("text_generation", "judge"), ("text",), "reasoning", 10),
        ModelDescriptor("mock", "mock-vision", "0.1", ("vision", "video_reasoning"), ("text", "image", "video"), "vision", 10),
        ModelDescriptor("mock", "mock-image", "0.1", ("image_generation",), ("text", "image"), "generation", 10),
        ModelDescriptor("mock", "mock-audio", "0.1", ("audio_transcription",), ("audio", "text"), "audio", 10),
        ModelDescriptor("mock", "mock-embed", "0.1", ("embedding",), ("text",), "embedding", 10),
    ]

    def catalog(self) -> list[ModelDescriptor]:
        return list(self._models)

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if capability not in CAPABILITIES:
            raise GatewayError("UNSUPPORTED_CAPABILITY", f"Capability '{capability}' is not registered.")
        digest = hashlib.sha256(json.dumps({"capability": capability, "payload": payload, "idempotency_key": idempotency_key}, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        if capability == "embedding":
            return {"embedding": [int(digest[index:index + 2], 16) / 255 for index in range(0, 24, 2)], "dimension": 12, "simulator": True}
        if capability == "audio_transcription":
            return {"text": f"simulated transcript {digest[:12]}", "segments": [], "simulator": True}
        if capability == "image_generation":
            return {"artifact_kind": "simulated-image-plan", "prompt_hash": digest, "simulator": True}
        if capability == "judge":
            return {"score": 0.82, "findings": [], "evidence_hash": digest, "simulator": True}
        return {"text": f"simulated {capability} response {digest[:16]}", "input_hash": digest, "simulator": True}


class MockSecondaryProvider(MockModelProvider):
    _models = [ModelDescriptor("mock-secondary", "secondary-reasoner", "0.1", ("text_generation", "judge"), ("text",), "reasoning", 8, quality_score=0.78, novelty_score=0.72, cost_score=0.88, latency_score=0.70, reliability_score=0.86), ModelDescriptor("mock-secondary", "secondary-vision", "0.1", ("vision", "video_reasoning"), ("text", "image", "video"), "vision", 8, quality_score=0.78, novelty_score=0.72, cost_score=0.88, latency_score=0.70, reliability_score=0.86), ModelDescriptor("mock-secondary", "secondary-image", "0.1", ("image_generation",), ("text", "image"), "generation", 8, quality_score=0.78, novelty_score=0.72, cost_score=0.88, latency_score=0.70, reliability_score=0.86), ModelDescriptor("mock-secondary", "secondary-audio", "0.1", ("audio_transcription",), ("audio", "text"), "audio", 8, quality_score=0.78, novelty_score=0.72, cost_score=0.88, latency_score=0.70, reliability_score=0.86), ModelDescriptor("mock-secondary", "secondary-embed", "0.1", ("embedding",), ("text",), "embedding", 8, quality_score=0.78, novelty_score=0.72, cost_score=0.88, latency_score=0.70, reliability_score=0.86)]


class SimulatorProvider(MockModelProvider):
    """The one explicitly labeled fallback in the production provider pool.

    Unlike MockModelProvider / MockSecondaryProvider (test doubles), this
    provider is allowed in production. It advertises provider "simulator",
    stamps every output with the reason it is simulating, and never lets a
    caller mistake its output for a live provider response. The gateway's
    health() reports this state verbatim: provider "simulator", reason
    "no provider key configured".
    """

    SIMULATOR_REASON = "no provider key configured"

    _models = [
        ModelDescriptor("simulator", "simulator-reasoner", "0.1", ("text_generation", "judge"), ("text",), "reasoning", 10),
        ModelDescriptor("simulator", "simulator-vision", "0.1", ("vision", "video_reasoning"), ("text", "image", "video"), "vision", 10),
        ModelDescriptor("simulator", "simulator-image", "0.1", ("image_generation",), ("text", "image"), "generation", 10),
        ModelDescriptor("simulator", "simulator-audio", "0.1", ("audio_transcription",), ("audio", "text"), "audio", 10),
        ModelDescriptor("simulator", "simulator-embed", "0.1", ("embedding",), ("text",), "embedding", 10),
    ]

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        output = super().invoke(capability, payload, idempotency_key)
        labeled = dict(output)
        labeled["reason"] = self.SIMULATOR_REASON
        return labeled


class HttpModelProvider:
    """Provider-neutral HTTPS adapter; disabled and fail-closed until configured."""

    def __init__(
        self,
        *,
        name: str,
        endpoint: str,
        models: list[ModelDescriptor],
        credential_resolver: Callable[[], str | None],
        allowed_hosts: set[str],
        enabled: bool = False,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        self.name = name[:80]
        self.endpoint = endpoint
        self._models = list(models)
        self._credential_resolver = credential_resolver
        self.allowed_hosts = {host.lower() for host in allowed_hosts}
        self.enabled = enabled
        self.timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self.max_response_bytes = max(1024, min(max_response_bytes, 16 * 1024 * 1024))
        self._validate_endpoint()

    def _validate_endpoint(self) -> None:
        parsed = urllib.parse.urlparse(self.endpoint)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host not in self.allowed_hosts:
            raise GatewayError("PROVIDER_ENDPOINT_FORBIDDEN", "Provider endpoint must use HTTPS and an explicitly allowlisted host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("PROVIDER_ENDPOINT_INVALID", "Provider endpoint cannot contain credentials, query parameters, or fragments.")

    def catalog(self) -> list[ModelDescriptor]:
        return list(self._models) if self.enabled else []

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if not self.enabled:
            raise GatewayError("PROVIDER_DISABLED", f"Provider '{self.name}' is disabled until explicitly configured.")
        secret = self._credential_resolver()
        if not secret:
            raise GatewayError("PROVIDER_CREDENTIAL_UNAVAILABLE", f"Provider '{self.name}' has no resolved credential.")
        request_payload = {"capability": capability, "payload": payload, "idempotency_key": idempotency_key}
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(request_payload, separators=(",", ":"), ensure_ascii=True).encode("utf-8"),
            headers={"content-type": "application/json", "authorization": f"Bearer {secret}", "idempotency-key": idempotency_key},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
            raise GatewayError("PROVIDER_HTTP_ERROR", f"Provider '{self.name}' returned HTTP {exc.code}.", retryable=retryable) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise GatewayError("PROVIDER_NETWORK_ERROR", f"Provider '{self.name}' could not be reached safely.", retryable=True) from exc
        if len(body) > self.max_response_bytes:
            raise GatewayError("PROVIDER_RESPONSE_TOO_LARGE", "Provider response exceeded the configured bound.")
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("PROVIDER_RESPONSE_INVALID", "Provider returned invalid JSON.") from exc
        if not isinstance(decoded, dict) or "output" not in decoded or not isinstance(decoded["output"], dict):
            raise GatewayError("PROVIDER_RESPONSE_INVALID", "Provider response did not match the normalized output contract.")
        return {"output": decoded["output"], "provider_policy": decoded.get("provider_policy", "unverified")}


class ModelGateway:
    def __init__(self, providers: list[ModelProvider] | None = None, credential_probe: Callable[[], bool] | None = None) -> None:
        # No mock providers in the default pool: mocks are test doubles only.
        # The production pool is assembled explicitly by the app (labeled
        # SimulatorProvider plus credential-gated live adapters).
        self.providers = list(providers) if providers is not None else []
        self._credential_probe = credential_probe
        self._operations: dict[str, GatewayResult] = {}

    def set_credential_probe(self, probe: Callable[[], bool] | None) -> None:
        """Install the app-owned credential check (None clears it).

        The probe reports whether ANY provider credential is actually
        configured. Adapters that advertise keyless models without a credential
        do not count: the gateway treats that state as simulator for serving
        purposes and says so in health().
        """
        self._credential_probe = probe

    def _credentialed(self) -> bool | None:
        if self._credential_probe is None:
            return None
        try:
            return bool(self._credential_probe())
        except Exception:
            return None

    def discover(self) -> list[ModelDescriptor]:
        return [model for provider in self.providers for model in provider.catalog()]

    def _select(self, capability: str) -> tuple[ModelProvider, ModelDescriptor]:
        if capability not in CAPABILITIES:
            raise GatewayError("UNSUPPORTED_CAPABILITY", f"Capability '{capability}' is not registered.")
        candidates: list[tuple[ModelProvider, ModelDescriptor]] = []
        for provider in self.providers:
            candidates.extend((provider, model) for model in provider.catalog() if capability in model.capabilities and model.status == "healthy")
        if candidates:
            def objective(item: tuple[ModelProvider, ModelDescriptor]) -> float:
                model = item[1]
                return (model.quality_score * 0.35) + (model.novelty_score * 0.15) + (model.cost_score * 0.15) + (model.latency_score * 0.10) + (model.reliability_score * 0.25) + (model.priority / 1000)
            return max(candidates, key=objective)
        raise GatewayError("NO_MODEL_FOR_CAPABILITY", f"No healthy model satisfies capability '{capability}'.", retryable=True)

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> GatewayResult:
        if not idempotency_key or len(idempotency_key) > 200:
            raise GatewayError("INVALID_IDEMPOTENCY_KEY", "A bounded idempotency key is required.")
        cached = self._operations.get(idempotency_key)
        if cached:
            return GatewayResult(cached.operation_id, cached.idempotency_key, cached.provider, cached.model_id, cached.capability, cached.output, True, 0)
        provider, model = self._select(capability)
        started = time.perf_counter()
        output = provider.invoke(capability, payload, idempotency_key)
        result = GatewayResult(f"modelop_{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}", idempotency_key, model.provider, model.model_id, capability, output, False, int((time.perf_counter() - started) * 1000))
        self._operations[idempotency_key] = result
        return result

    def diversity(self) -> dict[str, Any]:
        result: dict[str, list[str]] = {}
        for capability in sorted(CAPABILITIES):
            result[capability] = sorted({model.provider for model in self.discover() if capability in model.capabilities and model.status == "healthy"})
        return result

    def health(self) -> dict[str, Any]:
        models = self.discover()
        live_providers = {model.provider for model in models if model.provider not in ("mock", "mock-secondary", "simulator")}
        catalog_live = bool(live_providers)
        credentialed = self._credentialed()
        # Serving truth: live only when models are advertised AND a provider
        # credential is actually configured (or no probe is installed, which
        # preserves the historical catalog-only behavior for hand-built
        # gateways in tests). Keyless-advertised models without a credential
        # do not make the gateway live.
        live = catalog_live and credentialed is not False
        # simulator_only is serving truth: True whenever invocations would not
        # reach a credentialed live provider (no live models, or only
        # keyless-advertised models with no credential configured).
        simulator_only = not live
        if live:
            if "nvidia" in live_providers:
                source = "nvidia-live"
            elif "opencode-zen" in live_providers:
                source = "opencode-zen-live"
            elif "meta" in live_providers:
                source = "meta-live"
            else:
                source = f"{sorted(live_providers)[0]}-live"
        else:
            source = "simulator"
        providers = sorted({model.provider for model in models})
        # Live provider preference order: NVIDIA is the default live provider.
        preference = ("nvidia", "opencode-zen", "meta")
        preferred = [name for name in preference if name in providers]
        rest = [name for name in providers if name not in preference]
        providers = preferred + rest
        if live and preferred:
            default_provider: str | None = preferred[0]
        elif "simulator" in providers:
            default_provider = "simulator"
        else:
            default_provider = None
        return {"status": "healthy" if models else "degraded", "provider_count": len(self.providers), "providers": providers, "model_count": len(models), "capabilities": sorted({capability for model in models for capability in model.capabilities}), "diversity": self.diversity(), "simulator_only": simulator_only, "credentialed": credentialed, "provider_source": source, "default_provider": default_provider, "reason": None if live else "no provider key configured"}
