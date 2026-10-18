from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .contracts import GatewayError, ModelDescriptor

# NOTE (M9): logic here intentionally duplicates meta_provider.py / openai_provider.py
# instead of extracting a shared http_base.py. The duplication is small (~1 file)
# and keeps M1's/M7's tested behavior byte-for-byte stable.

PROVIDER_NAME = "opencode-zen"
DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
DEFAULT_ALLOWED_HOSTS = frozenset({"opencode.ai"})
DEFAULT_MODEL_ID = "muse-spark-1.3-contributor-free"
DEFAULT_CREDENTIAL_REF = "opencode-zen"

# Endpoint styles per model, per the OpenCode Zen docs. Zen exposes one HTTP
# style per model family; `systemone` (Jev structured eval) is skipped entirely
# as it is not text generation.
#   responses -> POST {base}/responses            (OpenAI Responses API)
#   messages  -> POST {base}/messages             (Anthropic Messages API)
#   chat      -> POST {base}/chat/completions     (OpenAI Chat Completions)
#   models    -> POST {base}/models/<id>          (Google-style; gemini family)
# Unknown ids default to `chat` (widest compatibility).
RESPONSES_MODELS = (
    "gpt-6-astra",
    "gpt-6.1-sol",
    "gpt-6-sol",
    "gpt-6-luna",
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.5-pro",
    "gpt-5.4",
    "gpt-5.4-pro",
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5.3-codex-spark",
    "gpt-5.3-codex",
    "gpt-5.2",
    "gpt-5.2-codex",
    "gpt-5.1",
    "gpt-5.1-codex-max",
    "gpt-5.1-codex",
    "gpt-5.1-codex-mini",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5-nano",
    "grok-build-0.1",
    "grok-4.7",
    "grok-4.6",
    "grok-4.5",
    "muse-spark-1.3",
    "muse-spark-1.2",
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.2-contributor-free",
)

MESSAGES_MODELS = (
    "claude-fable-5",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-opus-4-8",
    "claude-opus-4-7",
    "claude-opus-4-6",
    "claude-opus-4-5",
    "claude-sonnet-5-5",
    "claude-sonnet-5",
    "claude-sonnet-4-6",
    "claude-sonnet-4-5",
    "claude-sonnet-4",
    "claude-haiku-5-5",
    "claude-haiku-4-5",
    "qwen3.8-flash",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "qwen3.8-max",
)

GOOGLE_STYLE_MODELS = (
    "gemini-3.6-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.1-pro",
    "gemini-3-flash",
)

CHAT_MODELS = (
    "deepseek-v4.1-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "deepseek-v4-flash-vision-exp",
    "glm-5.3-flash",
    "glm-5.3",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "kimi-k3",
    "kimi-k2.7-code",
    "kimi-k2.6",
    "kimi-k2.5",
    "mistral-large-4",
    "big-pickle",
    "jev-1.13",
    "jev-1.13-free",
    "exo-free",
    "mimo-v2.6-flash-free",
    "space-bunny-free",
    "longcat-2.5-preview-free",
    "step-5-preview-free",
    "ling-3.0-flash-fin-free",
    "ling-3.1-flash-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
)

MODEL_ENDPOINTS: dict[str, str] = (
    {model_id: "responses" for model_id in RESPONSES_MODELS}
    | {model_id: "messages" for model_id in MESSAGES_MODELS}
    | {model_id: "models" for model_id in GOOGLE_STYLE_MODELS}
    | {model_id: "chat" for model_id in CHAT_MODELS}
)

KNOWN_MODEL_IDS: tuple[str, ...] = tuple(MODEL_ENDPOINTS)

# Documented free ids. These are the only models advertised without a
# credential. Keyless direct calls are attempted for these, but OpenCode's
# server only honors keyless use from inside the OpenCode CLI itself — a
# server rejection is surfaced honestly, never worked around.
FREE_MODEL_IDS: tuple[str, ...] = (
    "big-pickle",
    "space-bunny-free",
    "longcat-2.5-preview-free",
    "step-5-preview-free",
    "exo-free",
    "mimo-v2.6-flash-free",
    "mimo-v2.5-free",
    "ling-3.1-flash-free",
    "ling-3.0-flash-fin-free",
    "nemotron-3-ultra-free",
    "nemotron-3.5-lightning-free",
    "muse-spark-1.3-contributor-free",
    "muse-spark-1.2-contributor-free",
    "jev-1.13-free",
)

VISION_MODEL_IDS = frozenset({"deepseek-v4-flash-vision-exp"})

assert len(KNOWN_MODEL_IDS) == 87, len(KNOWN_MODEL_IDS)
assert len(FREE_MODEL_IDS) == 14, len(FREE_MODEL_IDS)
# The documented free id `mimo-v2.5-free` is not currently present in Zen's
# /v1/models listing; it still routes (default chat style) and stays advertised
# keyless per the docs.
assert set(FREE_MODEL_IDS) - set(KNOWN_MODEL_IDS) <= {"mimo-v2.5-free"}

DEFAULT_CAPABILITIES = ("text_generation",)


def endpoint_for_model(model_id: str) -> str:
    """Return the Zen endpoint style for a model id (default: chat)."""
    style = MODEL_ENDPOINTS.get(model_id)
    if style is not None:
        return style
    # Prefix fallback so newly added ids route sensibly without a code change.
    if model_id.startswith(("gpt-", "grok-", "muse-spark")):
        return "responses"
    if model_id.startswith(("claude-", "qwen")):
        return "messages"
    if model_id.startswith("gemini-"):
        return "models"
    return "chat"


def capabilities_for_model(model_id: str) -> tuple[str, ...]:
    if model_id in VISION_MODEL_IDS:
        return ("text_generation", "vision")
    return DEFAULT_CAPABILITIES


# Opaque credential-reference pattern (same as M1/M7): the provider never
# accepts a raw secret as a constructor argument. Callers pass an opaque
# `credential_ref` plus a zero-arg `credential_resolver` closure that resolves
# the ref at call time. The raw secret lives only in a local variable for the
# duration of one invoke call and is never persisted, logged, or echoed.
# No client-impersonation: no User-Agent overrides, no client version headers,
# no "within OpenCode" signals of any kind.


class OpenCodeZenProvider:
    """OpenCode Zen adapter: 87 models across 4 endpoint styles.

    Credentialed use sends `Authorization: Bearer <key>` (paid tier). Without
    a credential the adapter is dormant for paid models but may still attempt
    keyless calls for the documented free ids; OpenCode's own server-side
    rejection of such calls is surfaced honestly.
    """

    def __init__(
        self,
        *,
        credential_resolver: Callable[[], str | None],
        credential_ref: str = DEFAULT_CREDENTIAL_REF,
        name: str = PROVIDER_NAME,
        model_id: str | None = None,
        base_url: str | None = None,
        allowed_hosts: set[str] | frozenset[str] | None = None,
        timeout_seconds: float = 30.0,
        max_response_bytes: int = 4 * 1024 * 1024,
    ) -> None:
        if not callable(credential_resolver):
            raise GatewayError("PROVIDER_CREDENTIAL_UNAVAILABLE", "OpenCode Zen provider requires a credential resolver.")
        self._credential_resolver = credential_resolver
        # Opaque reference only; never a secret.
        self.credential_ref = (credential_ref or DEFAULT_CREDENTIAL_REF)[:200]
        self.name = (name or PROVIDER_NAME)[:80]
        configured_model = (model_id or os.environ.get("OPENCODE_ZEN_MODEL_ID", "") or "").strip()
        self.model_id = (configured_model or DEFAULT_MODEL_ID)[:200]
        self.base_url = (base_url or os.environ.get("OPENCODE_ZEN_BASE_URL", "") or "").strip() or DEFAULT_BASE_URL
        self.allowed_hosts = {h.lower() for h in (allowed_hosts if allowed_hosts is not None else DEFAULT_ALLOWED_HOSTS)}
        self.timeout_seconds = max(1.0, min(timeout_seconds, 120.0))
        self.max_response_bytes = max(1024, min(max_response_bytes, 16 * 1024 * 1024))
        # Instance catalog: starts at the hardcoded baseline; refresh_catalog()
        # merges in live /models discovery results when available.
        self._known_ids: tuple[str, ...] = KNOWN_MODEL_IDS
        self._validate_base_url()

    @property
    def capabilities(self) -> tuple[str, ...]:
        return capabilities_for_model(self.model_id)

    def _validate_base_url(self) -> None:
        parsed = urllib.parse.urlparse(self.base_url)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host not in self.allowed_hosts:
            raise GatewayError("PROVIDER_ENDPOINT_FORBIDDEN", "OpenCode Zen provider endpoint must use HTTPS and an explicitly allowlisted host.")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise GatewayError("PROVIDER_ENDPOINT_INVALID", "OpenCode Zen provider endpoint cannot contain credentials, query parameters, or fragments.")

    def _has_credential(self) -> bool:
        try:
            return bool(self._credential_resolver())
        except Exception:
            return False

    def _descriptor_for(self, model_id: str) -> ModelDescriptor:
        capabilities = capabilities_for_model(model_id)
        modalities = ("text", "image") if "vision" in capabilities else ("text",)
        return ModelDescriptor(
            self.name,
            model_id,
            "1.0",
            capabilities,
            modalities,
            "reasoning",
            22,
            quality_score=0.90,
            novelty_score=0.70,
            cost_score=0.72,
            latency_score=0.72,
            reliability_score=0.88,
        )

    def catalog(self) -> list[ModelDescriptor]:
        # Credentialed: all known models. Keyless: only the documented free
        # ids (dormant for paid models, fail-soft attempt for free ones).
        # `self._known_ids` is the hardcoded baseline plus any ids merged in
        # by refresh_catalog()'s live /models discovery.
        if self._has_credential():
            return [self._descriptor_for(model_id) for model_id in self._known_ids]
        return [self._descriptor_for(model_id) for model_id in FREE_MODEL_IDS]

    def refresh_catalog(self) -> dict[str, Any]:
        """Live /models discovery (M11).

        GET {base_url}/models and merge newly listed ids into this instance's
        catalog. The hardcoded KNOWN_MODEL_IDS remain the baseline; newly
        discovered ids route via endpoint_for_model()'s prefix fallbacks.
        Credentialed when a key resolves, keyless otherwise; server rejections
        surface as GatewayError, never silently. Failures leave the baseline
        catalog untouched. Returns a report dict.
        """
        # Re-enforce allowlist on every call, before any network I/O.
        self._validate_base_url()
        try:
            secret = self._credential_resolver()
        except Exception as exc:
            raise GatewayError("GATEWAY_AUTH_FAILED", "OpenCode Zen credential could not be resolved.") from exc
        target = self.base_url.rstrip("/") + "/models"
        headers = {"accept": "application/json"}
        if secret:
            headers["authorization"] = f"Bearer {secret}"
        request = urllib.request.Request(target, headers=headers, method="GET")
        secret = ""
        timeout = min(self.timeout_seconds, 8.0)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise GatewayError("GATEWAY_AUTH_FAILED", "OpenCode Zen credential was rejected.", retryable=False) from exc
            if exc.code in {402, 429}:
                raise GatewayError("GATEWAY_QUOTA_EXCEEDED", "OpenCode Zen quota or rate limit was exceeded.", retryable=True) from exc
            if exc.code in {408, 409, 425} or exc.code >= 500:
                raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen provider returned an unexpected status.", retryable=False) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise GatewayError("GATEWAY_TIMEOUT", "OpenCode Zen /models discovery timed out.", retryable=True) from exc
        except urllib.error.URLError as exc:
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
        except OSError as exc:
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("PROVIDER_RESPONSE_TOO_LARGE", "OpenCode Zen response exceeded the configured bound.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen returned invalid JSON.") from exc
        data = decoded.get("data") if isinstance(decoded, dict) else None
        discovered: list[str] = []
        if isinstance(data, list):
            for item in data:
                if isinstance(item, dict) and isinstance(item.get("id"), str) and item["id"].strip():
                    model_id = item["id"].strip()[:200]
                    if model_id not in discovered:
                        discovered.append(model_id)
        else:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen /models response did not match the list contract.")
        baseline = set(self._known_ids)
        merged = tuple(dict.fromkeys(list(self._known_ids) + [model_id for model_id in discovered if model_id not in baseline]))
        added = [model_id for model_id in merged if model_id not in baseline]
        self._known_ids = merged
        return {"added": added, "total": len(merged), "baseline": len(KNOWN_MODEL_IDS), "discovered": len(discovered)}

    def _input_text(self, payload: dict[str, Any]) -> str:
        for key in ("text", "prompt", "brief", "task", "input"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value[:20000]
        try:
            return json.dumps(payload, ensure_ascii=False, sort_keys=True)[:20000]
        except (TypeError, ValueError):
            return ""

    def _chat_messages(self, capability: str, payload: dict[str, Any]) -> list[dict[str, Any]]:
        if isinstance(payload.get("messages"), list) and payload["messages"]:
            messages: list[dict[str, Any]] = []
            for item in payload["messages"][:32]:
                if isinstance(item, dict) and isinstance(item.get("content"), str):
                    role = item.get("role") if item.get("role") in {"user", "assistant", "system"} else "user"
                    messages.append({"role": role, "content": item["content"][:20000]})
            if messages:
                return messages
        text = self._input_text(payload)
        if capability == "vision" and isinstance(payload.get("image_url"), str) and payload["image_url"].startswith("https://"):
            return [{"role": "user", "content": [{"type": "text", "text": text or "Describe the image."}, {"type": "image_url", "image_url": {"url": payload["image_url"][:4000]}}]}]
        return [{"role": "user", "content": text}]

    def _target_and_body(self, capability: str, payload: dict[str, Any]) -> tuple[str, bytes]:
        style = endpoint_for_model(self.model_id)
        base = self.base_url.rstrip("/")
        if style == "responses":
            target = base + "/responses"
            body = {"model": self.model_id, "input": self._input_text(payload), "max_output_tokens": 1024}
        elif style == "messages":
            target = base + "/messages"
            simple = [{"role": m["role"], "content": m["content"]} if isinstance(m.get("content"), str) else m for m in self._chat_messages(capability, payload)]
            body = {"model": self.model_id, "max_tokens": 1024, "messages": simple}
        elif style == "models":
            target = base + "/models/" + urllib.parse.quote(self.model_id, safe="")
            text = self._input_text(payload)
            body = {
                "model": self.model_id,
                "messages": self._chat_messages(capability, payload),
                "contents": [{"role": "user", "parts": [{"text": text}]}],
            }
        else:
            target = base + "/chat/completions"
            body = {"model": self.model_id, "messages": self._chat_messages(capability, payload)}
        return target, json.dumps(body, separators=(",", ":"), ensure_ascii=True).encode("utf-8")

    def _extract_text(self, style: str, decoded: Any) -> str:
        if not isinstance(decoded, dict):
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match its contract.")
        if style == "responses":
            parts: list[str] = []
            output = decoded.get("output")
            if isinstance(output, str) and output.strip():
                return output
            if isinstance(output, list):
                for item in output:
                    if not isinstance(item, dict):
                        continue
                    if item.get("type") == "output_text" and isinstance(item.get("text"), str) and item["text"].strip():
                        parts.append(item["text"])
                    elif item.get("type") == "message":
                        content = item.get("content")
                        if isinstance(content, str) and content.strip():
                            parts.append(content)
                        elif isinstance(content, list):
                            for block in content:
                                if isinstance(block, dict) and block.get("type") in {"output_text", "text"} and isinstance(block.get("text"), str) and block["text"].strip():
                                    parts.append(block["text"])
            text = "\n".join(parts)
            if text.strip():
                return text
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match the responses contract.")
        if style == "messages":
            parts = []
            content = decoded.get("content")
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"].strip():
                        parts.append(block["text"])
            text = "\n".join(parts)
            if text.strip():
                return text
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match the messages contract.")
        if style == "models":
            # Tolerant: chat shape, then messages shape, then Google candidates.
            try:
                content = decoded["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                content = None
            if isinstance(content, str) and content.strip():
                return content
            if isinstance(content, list):
                parts = [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"].strip()]
                if parts:
                    return "\n".join(parts)
            try:
                candidates = decoded["candidates"][0]["content"]["parts"]
            except (KeyError, IndexError, TypeError):
                candidates = None
            if isinstance(candidates, list):
                parts = [p["text"] for p in candidates if isinstance(p, dict) and isinstance(p.get("text"), str) and p["text"].strip()]
                if parts:
                    return "\n".join(parts)
            content_blocks = decoded.get("content")
            if isinstance(content_blocks, list):
                parts = [b["text"] for b in content_blocks if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str) and b["text"].strip()]
                if parts:
                    return "\n".join(parts)
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match the models contract.")
        try:
            content = decoded["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match the chat-completions contract.") from exc
        if isinstance(content, list):
            parts = [b["text"] for b in content if isinstance(b, dict) and isinstance(b.get("text"), str) and b["text"].strip()]
            content = "\n".join(parts) if parts else ""
        if not isinstance(content, str) or not content.strip():
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen response did not match the chat-completions contract.")
        return content

    def _server_message(self, exc: urllib.error.HTTPError) -> str:
        try:
            raw = exc.read()
        except Exception:
            return ""
        if not raw:
            return ""
        try:
            decoded = json.loads(raw[:2000].decode("utf-8", errors="replace"))
        except (UnicodeDecodeError, ValueError):
            try:
                text = raw[:300].decode("utf-8", errors="replace").strip()
            except Exception:
                return ""
            return text
        if isinstance(decoded, dict):
            for key in ("message", "error", "detail"):
                value = decoded.get(key)
                if isinstance(value, str) and value.strip():
                    return value.strip()[:300]
                if isinstance(value, dict) and isinstance(value.get("message"), str) and value["message"].strip():
                    return value["message"].strip()[:300]
        return ""

    def invoke(self, capability: str, payload: dict[str, Any], idempotency_key: str) -> dict[str, Any]:
        if capability not in self.capabilities:
            raise GatewayError("UNSUPPORTED_CAPABILITY", f"OpenCode Zen provider does not serve capability '{capability}'.")
        # Re-enforce allowlist on every call, before any network I/O.
        self._validate_base_url()
        try:
            secret = self._credential_resolver()
        except Exception as exc:
            raise GatewayError("GATEWAY_AUTH_FAILED", "OpenCode Zen credential could not be resolved.") from exc
        if not secret:
            # Dormant for paid models; fail-soft keyless attempt for free ones.
            if self.model_id not in FREE_MODEL_IDS:
                raise GatewayError("GATEWAY_AUTH_FAILED", "OpenCode Zen credential is not configured; staying on the simulator.")
            secret = None
        style = endpoint_for_model(self.model_id)
        target, body = self._target_and_body(capability, payload)
        headers = {"content-type": "application/json", "idempotency-key": idempotency_key}
        if secret:
            headers["authorization"] = f"Bearer {secret}"
        request = urllib.request.Request(target, data=body, headers=headers, method="POST")
        secret = ""
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            server_message = self._server_message(exc)
            if exc.code in {401, 403}:
                # OpenCode's FreeTierError (403, keyless on a paid model) lands
                # here: the server's message is preserved verbatim.
                detail = f" {server_message}" if server_message else ""
                raise GatewayError("GATEWAY_AUTH_FAILED", f"OpenCode Zen credential was rejected.{detail}", retryable=False) from exc
            if exc.code in {402, 429}:
                raise GatewayError("GATEWAY_QUOTA_EXCEEDED", "OpenCode Zen quota or rate limit was exceeded.", retryable=True) from exc
            if exc.code in {408, 409, 425} or exc.code >= 500:
                raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen provider returned an unexpected status.", retryable=False) from exc
        except (TimeoutError, socket.timeout) as exc:
            raise GatewayError("GATEWAY_TIMEOUT", "OpenCode Zen provider timed out.", retryable=True) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (TimeoutError, socket.timeout)) or "timed out" in str(reason or exc).lower() or "timeout" in str(reason or exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "OpenCode Zen provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
        except OSError as exc:
            if isinstance(exc, TimeoutError) or "timed out" in str(exc).lower() or "timeout" in str(exc).lower():
                raise GatewayError("GATEWAY_TIMEOUT", "OpenCode Zen provider timed out.", retryable=True) from exc
            raise GatewayError("GATEWAY_NETWORK_ERROR", "OpenCode Zen provider could not be reached safely.", retryable=True) from exc
        if len(raw) > self.max_response_bytes:
            raise GatewayError("PROVIDER_RESPONSE_TOO_LARGE", "OpenCode Zen response exceeded the configured bound.")
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GatewayError("GATEWAY_BAD_RESPONSE", "OpenCode Zen provider returned invalid JSON.") from exc
        text = self._extract_text(style, decoded)
        return {"output": {"text": text}, "provider_policy": f"{self.name}-live"}

    def __repr__(self) -> str:
        return f"OpenCodeZenProvider(name={self.name!r}, model_id={self.model_id!r}, base_url={self.base_url!r}, credential_ref={self.credential_ref!r})"
