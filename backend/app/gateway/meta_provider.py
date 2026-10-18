from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .contracts import GatewayError, ModelDescriptor

MODEL_ID = "muse-spark-1.3"
PROVIDER_NAME = "meta"
DEFAULT_BASE_URL = "https://api.meta.ai/v1"
DEFAULT_ALLOWED_HOSTS = frozenset({"api.meta.ai"})
SUPPORTED_CAPABILITIES = ("text_generation", "vision", "judge")

# Opaque credential-reference pattern: the provider never accepts a raw secret
# as a constructor argument. Callers pass an opaque `credential_ref` plus a
# zero-arg `credential_resolver` closure that resolves the ref at call time
# (e.g. from the DPAPI credential blob via CredentialStore.read). The raw
# secret is held only in a local variable for the duration of one invoke call
# and is never persisted, logged, or included in outputs/errors.


class MetaModelProvider:
    """Real Meta (Muse Spark) adapter over the OpenAI-compatible Chat Completions wire.

    POST {base_url}/chat/completions with `Authorization: Bearer <credential>`.
    Fail-closed: non-allowlisted hosts raise before any network I/O, and the
    catalog stays empty until a credential reference resolves.
    """

    def __init__(
        self,
        *,
        credential_resolver: Callable[[], str | None],
        credential_ref: str = "meta",
        base_url: str = DEFAULT_BASE_URL,
        allowed_hosts: set[str] | frozenset[str] | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if not callable(credential_resolver):
            raise GatewayError("PROVIDER_CREDENTIAL_UNAVAILABLE", "Meta provider requires a credential resolver.")
        self._credential_resolver = credential_resolver
        # Opaque reference only (e.g. credential_id or provider key); never a secret.
        self.credential_ref = (credential_ref or "meta")[:200]
        self.base_url = base_url
        self.allowed_hosts = {h.lower() for h in (allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS)}
        self.timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self.max_response_bytes = max(1024, min(max_response_bytes, 16 * 1024 * 1024))
        self._descriptor = ModelDescriptor(
            PROVIDER_NAME,
            MODEL_ID,
            "1.3",
            SUPPORTED_CAPABILITIES,
            ("text", "image"),
            "reasoning",
            20,
            quality_score=0.90,
            novelty_score=0.72,
            cost_score=0.70,
            latency_score=0.72,
            reliability_score=0.88,
        )
        self._validate_base_url()

    def _validate_base_url(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host not in self.allowed_hosts:
            raise GatewayError("PROVIDER_ENDPOINT_FORBIDDEN", "Meta provider endpoint must use HTTPS and an explicitly allowlisted host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("PROVIDER_ENDPOINT_INVALID", "Meta provider endpoint cannot contain credentials, query parameters, or fragments.")

    def _has_credential(self) -> bool:
        try:
            return bool(self._credential_resolver())
        except Exception:
            return False

    def catalog(self) -> list[ModelDescriptor]:
        # Simulator-by-default: no credential reference -> no live models advertised.
        if not self._has_credential():
            return []
        return [self._descriptor]

    def _chat_messages(self, capability: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(payload.get("messages"), list) and payload["messages"]:
            messages: list[dict[str, Any]] = []
            for item in payload["messages"][:32]:
                if isinstance(item, dict) and isinstance(item.get("content"), str):
                    role = item.get("role") if item.get("role") in {"user", "assistant", "system"} else "user"
                    messages.append({"role": role, "content": item["content"][:20000]})
            if messages:
                return messages
        text = ""
        for key in ("text", "prompt", "brief", "task", "input"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                text = value[:20000]
                break
        if not text:
            try:
                text = json.dumps(payload, ensure_ascii=False, sort_keys=True)[:20000]
            except (TypeError, ValueError):
                text = ""
        if capability == "vision" and isinstance(payload.get("image_url"), str) and payload["image_url"].startswith("https://"):
            return [{"role": "user", "content": [{"type": "text", "text": text or "Describe the image."}, {"type": "image_url", "image_url": {"url": payload["image_url"][:4000]}}]}]
        return [{"role": "user", "content": text}]

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if capability not in SUPPORTED_CAPABILITIES:
            raise GatewayError("UNSUPPORTED_CAPABILITY", f"Meta provider does not serve capability '{capability}'.")
        # Re-enforce allowlist on every call, before any network I/O.
        self._validate_base_url()
        try:
            secret = self._credential_resolver()
        except Exception as exc:
            raise GatewayError("GATEWAY_AUTH_FAILED", "Meta credential could not be resolved.") from exc
        if not secret:
            raise GatewayError("GATEWAY_AUTH_FAILED", "Meta credential is not configured; staying on the simulator.")
        target = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({"model": MODEL_ID, "messages": self._chat_messages(capability, payload)}, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        request = urllib.request.Request(
            target,
            data=body,
            headers={"content-type": "application/json", "authorization": f"Bearer {secret}", "idempotency-key": idempotency_key},
            method="POST",
        )
        secret = ""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise GatewayError("GATEWAY_AUTH_FAILED", "Meta credential was rejected.", retryable=False) from exc
            if exc.code in {402, 429}:
                raise GatewayError("GATEWAY_QUOTA_EXCEEDED", "Meta quota or rate limit was exceeded.", retryable=True) from exc
            if exc.code in {408, 409, 425} or exc.code >= 500:
                raise GatewayError("GATEWAY_NETWORK_ERROR", "Meta provider could not be reached safely.", retryable=True) from exc
            raise GatewayError("GATEWAY_BAD_RESPONSE", "Meta provider returned an unexpected status.", retryable=False) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise GatewayError("GATEWAY_TIMEOUT", "Meta provider timed out.", retryable=True) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason or exc).lower() or "timeout" in str(reason or exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "Meta provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "Meta provider could not be reached safely.", retryable=True) from exc
        except OSError as exc:
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "Meta provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "Meta provider could not be reached safely.", retryable=True) from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("PROVIDER_RESPONSE_TOO_LARGE", "Meta response exceeded the configured bound.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "Meta returned invalid JSON.") from exc
        try:
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "Meta response did not match the chat-completions contract.") from exc
        if not isinstance(content, str) or not content.strip():
            raise GatewayError("GATEWAY_BAD_RESPONSE", "Meta response did not match the chat-completions contract.")
        return {"output": {"text": content}, "provider_policy": "meta-live"}

    def __repr__(self) -> str:
        return f"MetaModelProvider(base_url={self.base_url!r}, credential_ref={self.credential_ref!r})"
