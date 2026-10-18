from __future__ import annotations

import inspect
import io
import json
import sys
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.gateway.contracts import GatewayError, ModelGateway, MockModelProvider
from app.gateway.meta_provider import MetaModelProvider
from app.gateway.openai_provider import DEFAULT_BASE_URL, DEFAULT_MODEL_ID, OpenAICompatibleProvider

SECRET = "sk-openai-test-secret-abc-789012"
SAFE_STRINGS: list[str] = []


def track(value: object) -> None:
    try:
        SAFE_STRINGS.append(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        SAFE_STRINGS.append(str(value))


class ChatResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, limit: int) -> bytes:
        assert limit > 0
        return self._payload

    def __enter__(self) -> "ChatResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def chat_body(text: str = "hello openai") -> bytes:
    return json.dumps({"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": text}}]}).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.openai.com/v1/chat/completions", code, f"HTTP {code}", {}, io.BytesIO(b"{}"))


def main() -> None:
    # Constructor must never accept a raw secret as a plain argument.
    params = set(inspect.signature(OpenAICompatibleProvider.__init__).parameters)
    assert "secret" not in params and "api_key" not in params and "apiKey" not in params, params
    # Generic surface: name/model_id/base_url configurable, sane defaults.
    assert DEFAULT_BASE_URL == "https://api.openai.com/v1"
    assert DEFAULT_MODEL_ID == "gpt-4o-mini"
    assert "meta" not in DEFAULT_BASE_URL and "muse-spark" not in DEFAULT_MODEL_ID.lower()

    live = OpenAICompatibleProvider(credential_resolver=lambda: SECRET)
    assert live.catalog() and live.catalog()[0].model_id == DEFAULT_MODEL_ID
    assert live.catalog()[0].provider == "openai-compatible"
    assert set(live.catalog()[0].capabilities) == {"text_generation", "vision", "judge"}
    assert repr(live) and SECRET not in repr(live)

    # Credential-gated catalog: no reference -> no live models advertised.
    assert OpenAICompatibleProvider(credential_resolver=lambda: None).catalog() == []

    # 1. Successful text_generation normalization.
    seen: dict[str, object] = {}

    def fake_success(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        return ChatResponse(chat_body("openai live reply"))

    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=fake_success):
        result = live.invoke("text_generation", {"text": "say hi"}, "openai-ok-1")
    track(result)
    assert set(result) == {"output", "provider_policy"}, result
    assert result["output"]["text"] == "openai live reply", result
    assert result["provider_policy"] == "openai-compatible-live", result
    assert str(seen["url"]).startswith(DEFAULT_BASE_URL) and str(seen["url"]).endswith("/chat/completions"), seen
    assert seen["auth"] == f"Bearer {SECRET}"
    assert DEFAULT_MODEL_ID in str(seen["body"])

    # 2. Credential missing -> clean GatewayError with no network attempt.
    missing = OpenAICompatibleProvider(credential_resolver=lambda: None)
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted without credential")) as urlopen_mock:
        try:
            missing.invoke("text_generation", {"text": "x"}, "openai-no-cred")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.retryable is False
        else:
            raise AssertionError("invoke without credential succeeded")
        urlopen_mock.assert_not_called()

    # 3. Auth failure mapping.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=http_error(401)):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-auth")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("401 was not mapped")

    # 4. Quota mapping with retryable=True.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=http_error(429)):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-quota")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_QUOTA_EXCEEDED" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("429 was not mapped")

    # 5. Malformed JSON mapping.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", return_value=ChatResponse(b"not-json{{")):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-bad-json")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("malformed JSON was accepted")

    # 5b. Malformed schema mapping (valid JSON, wrong contract).
    with patch("app.gateway.openai_provider.urllib.request.urlopen", return_value=ChatResponse(json.dumps({"unexpected": 1}).encode())):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-bad-schema")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE", exc.code
        else:
            raise AssertionError("malformed schema was accepted")

    # 6. Timeout mapping.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-timeout")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_TIMEOUT" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("timeout was not mapped")

    # 6b. Network error mapping.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        try:
            live.invoke("text_generation", {"text": "x"}, "openai-net")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_NETWORK_ERROR" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("network error was not mapped")

    # 7. Oversized response rejection.
    tiny = OpenAICompatibleProvider(credential_resolver=lambda: SECRET, max_response_bytes=1024)

    class Oversized(ChatResponse):
        def read(self, limit: int) -> bytes:
            return b"x" * (limit + 1)

    with patch("app.gateway.openai_provider.urllib.request.urlopen", return_value=Oversized(b"")):
        try:
            tiny.invoke("text_generation", {"text": "x"}, "openai-huge")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_RESPONSE_TOO_LARGE", exc.code
        else:
            raise AssertionError("oversized response was accepted")

    # 8. Non-allowlisted host rejection before any network I/O.
    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted to forbidden host")) as urlopen_mock:
        try:
            OpenAICompatibleProvider(credential_resolver=lambda: SECRET, base_url="https://evil.example/v1")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN", exc.code
        else:
            raise AssertionError("non-allowlisted host was accepted")
        urlopen_mock.assert_not_called()

    # 8b. Non-HTTPS rejection.
    try:
        OpenAICompatibleProvider(credential_resolver=lambda: SECRET, base_url="http://api.openai.com/v1", allowed_hosts={"api.openai.com"})
    except GatewayError as exc:
        track({"code": exc.code, "message": exc.message})
        assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN", exc.code
    else:
        raise AssertionError("non-HTTPS endpoint was accepted")

    # 9. Custom base_url + model_id honored without code changes (generic).
    custom = OpenAICompatibleProvider(
        credential_resolver=lambda: SECRET,
        base_url="https://llm.example.com/v1",
        model_id="custom-model-1",
        allowed_hosts={"llm.example.com"},
    )
    assert custom.catalog()[0].model_id == "custom-model-1"
    assert "muse-spark" not in custom.catalog()[0].model_id and "meta" not in custom.base_url

    def fake_custom(request, timeout=None):
        seen["custom_url"] = request.full_url
        seen["custom_body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        return ChatResponse(chat_body("custom reply"))

    with patch("app.gateway.openai_provider.urllib.request.urlopen", side_effect=fake_custom):
        custom_result = custom.invoke("text_generation", {"text": "hi"}, "openai-custom-1")
    track(custom_result)
    assert custom_result["output"]["text"] == "custom reply"
    assert str(seen["custom_url"]) == "https://llm.example.com/v1/chat/completions", seen
    assert "custom-model-1" in str(seen["custom_body"])

    # 10. Gateway-level: both adapters credentialed -> diversity reports 2 providers, health lists both.
    both = ModelGateway(
        providers=[
            MetaModelProvider(credential_resolver=lambda: "sk-meta-x"),
            OpenAICompatibleProvider(credential_resolver=lambda: SECRET),
        ]
    )
    diversity = both.diversity()
    track(diversity)
    assert set(diversity["text_generation"]) == {"meta", "openai-compatible"}, diversity["text_generation"]
    both_health = both.health()
    track(both_health)
    assert "meta" in both_health["providers"] and "openai-compatible" in both_health["providers"], both_health
    assert both_health["simulator_only"] is False, both_health

    # 11. Gateway-level: neither credentialed -> simulator_only exactly as before.
    neither = ModelGateway(
        providers=[
            MockModelProvider(),
            MetaModelProvider(credential_resolver=lambda: None),
            OpenAICompatibleProvider(credential_resolver=lambda: None),
        ]
    )
    neither_health = neither.health()
    track(neither_health)
    assert neither_health["simulator_only"] is True, neither_health
    assert neither_health["provider_source"] == "simulator", neither_health

    # Raw secret must never appear in normalized outputs or error surfaces.
    for blob in SAFE_STRINGS:
        assert SECRET not in blob, blob

    print("openai_provider: passed")


if __name__ == "__main__":
    main()
