from __future__ import annotations

import json
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .contracts import GatewayError, ModelDescriptor

# NOTE (M7): logic here intentionally duplicates meta_provider.py instead of
# extracting a shared http_base.py. The duplication is small (~1 file) and
# keeps M1's tested behavior byte-for-byte stable. See commit message.

PROVIDER_NAME = "openai-compatible"
DEFAULT_MODEL_ID = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"
DEFAULT_ALLOWED_HOSTS = frozenset({"api.openai.com"})
DEFAULT_CAPABILITIES = ("text_generation", "vision", "judge")

# Opaque credential-reference pattern (same as M1): the provider never accepts
# a raw secret as a constructor argument. Callers pass an opaque
# `credential_ref` plus a zero-arg `credential_resolver` closure that resolves
# the ref at call time. The raw secret lives only in a local variable for the
# duration of one invoke call and is never persisted, logged, or echoed.


class OpenAICompatibleProvider:
    """Generic OpenAI-compatible Chat Completions adapter.

    POST {base_url}/chat/completions with `Authorization: Bearer <credential>`.
    No Meta-specific paths, headers, or model ids. Any base_url + model_id +
    credential combination works via constructor config. Fail-closed: the
    catalog stays empty until a credential resolves, and non-allowlisted
    endpoints raise before any network I/O.
    """

    def __init__(
        self,
        *,
        credential_resolver: Callable[[], str | None],
        credential_ref: str = "openai-compatible",
        name: str = PROVIDER_NAME,
        model_id: str = DEFAULT_MODEL_ID,
        base_url: str = DEFAULT_BASE_URL,
        capabilities: tuple[str, ...] = DEFAULT_CAPABILITIES,
        allowed_hosts: set[str] | frozenset[str] | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if not callable(credential_resolver):
            raise GatewayError("PROVIDER_CREDENTIAL_UNAVAILABLE", "OpenAI-compatible provider requires a credential resolver.")
        self._credential_resolver = credential_resolver
        # Opaque reference only; never a secret.
        self.credential_ref = (credential_ref or "openai-compatible")[:200]
        self.name = (name or PROVIDER_NAME)[:80]
        self.model_id = (model_id or DEFAULT_MODEL_ID)[:200]
        self.base_url = base_url
        self.capabilities = tuple(capabilities or DEFAULT_CAPABILITIES)
        self.allowed_hosts = {h.lower() for h in (allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS)}
        self.timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self.max_response_bytes = max(1024, min(max_response_bytes, 16 * 1024 * 1024))
        self._descriptor = ModelDescriptor(
            self.name,
            self.model_id,
            "1.0",
            self.capabilities,
            ("text", "image"),
            "reasoning",
            18,
            quality_score=0.88,
            novelty_score=0.66,
            cost_score=0.74,
            latency_score=0.74,
            reliability_score=0.86,
        )
        self._validate_base_url()

    def _validate_base_url(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host not in self.allowed_hosts:
            raise GatewayError("PROVIDER_ENDPOINT_FORBIDDEN", "OpenAI-compatible provider endpoint must use HTTPS and an explicitly allowlisted host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("PROVIDER_ENDPOINT_INVALID", "OpenAI-compatible provider endpoint cannot contain credentials, query parameters, or fragments.")

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
        if capability not in self.capabilities:
            raise GatewayError("UNSUPPORTED_CAPABILITY", f"OpenAI-compatible provider does not serve capability '{capability}'.")
        # Re-enforce allowlist on every call, before any network I/O.
        self._validate_base_url()
        try:
            secret = self._credential_resolver()
        except Exception as exc:
            raise GatewayError("GATEWAY_AUTH_FAILED", "OpenAI-compatible credential could not be resolved.") from exc
        if not secret:
            raise GatewayError("GATEWAY_AUTH_FAILED", "OpenAI-compatible credential is not configured; staying on the simulator.")
        target = self.base_url.rstrip("/") + "/chat/completions"
        body = json.dumps({"model": self.model_id, "messages": self._chat_messages(capability, payload)}, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
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
                raise GatewayError("GATEWAY_AUTH_FAILED", "OpenAI-compatible credential was rejected.", retryable=False) from exc
            if exc.code in {402, 429}:
                raise GatewayError("GATEWAY_QUOTA_EXCEEDED", "OpenAI-compatible quota or rate limit was exceeded.", retryable=True) from exc
            if exc.code in {408, 409, 425} or exc.code >= 500:
                raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenAI-compatible provider could not be reached safely.", retryable=True) from exc
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenAI-compatible provider returned an unexpected status.", retryable=False) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise GatewayError("GATEWAY_TIMEOUT", "OpenAI-compatible provider timed out.", retryable=True) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason or exc).lower() or "timeout" in str(reason or exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "OpenAI-compatible provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenAI-compatible provider could not be reached safely.", retryable=True) from exc
        except OSError as exc:
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "OpenAI-compatible provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenAI-compatible provider could not be reached safely.", retryable=True) from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("PROVIDER_RESPONSE_TOO_LARGE", "OpenAI-compatible response exceeded the configured bound.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenAI-compatible provider returned invalid JSON.") from exc
        try:
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenAI-compatible response did not match the chat-completions contract.") from exc
        if not isinstance(content, str) or not content.strip():
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenAI-compatible response did not match the chat-completions contract.")
        return {"output": {"text": content}, "provider_policy": f"{self.name}-live"}

    def __repr__(self) -> str:
        return f"OpenAICompatibleProvider(name={self.name!r}, model_id={self.model_id!r}, base_url={self.base_url!r}, credential_ref={self.credential_ref!r})"
