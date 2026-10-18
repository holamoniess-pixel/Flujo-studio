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
from app.gateway.meta_provider import DEFAULT_BASE_URL, MODEL_ID, MetaModelProvider

SECRET = "sk-meta-test-secret-xyz-123456"
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


def chat_body(text: str = "hello live") -> bytes:
    return json.dumps({"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": text}}]}).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://api.meta.ai/v1/chat/completions", code, f"HTTP {code}", {}, io.BytesIO(b"{}"))


def main() -> None:
    # Constructor must never accept a raw secret as a plain argument.
    params = set(inspect.signature(MetaModelProvider.__init__).parameters)
    assert "secret" not in params and "api_key" not in params and "apiKey" not in params, params

    live = MetaModelProvider(credential_resolver=lambda: SECRET)
    assert live.catalog() and live.catalog()[0].model_id == MODEL_ID == "muse-spark-1.3"
    assert live.catalog()[0].provider == "meta"
    assert set(live.catalog()[0].capabilities) == {"text_generation", "vision", "judge"}
    assert "image_generation" not in live.catalog()[0].capabilities and "video_reasoning" not in live.catalog()[0].capabilities
    assert repr(live) and SECRET not in repr(live)

    # Credential-gated catalog: no reference -> no live models advertised.
    assert MetaModelProvider(credential_resolver=lambda: None).catalog() == []

    # 1. Successful text_generation normalization (same shape as HttpModelProvider.invoke).
    seen: dict[str, object] = {}
    real_urlopen = None

    def fake_success(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        return ChatResponse(chat_body("live spark reply"))

    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=fake_success):
        result = live.invoke("text_generation", {"text": "say hi"}, "meta-ok-1")
    track(result)
    assert set(result) == {"output", "provider_policy"}, result
    assert result["output"]["text"] == "live spark reply", result
    assert result["provider_policy"] == "meta-live", result
    assert str(seen["url"]).startswith(DEFAULT_BASE_URL) and str(seen["url"]).endswith("/chat/completions"), seen
    assert seen["auth"] == f"Bearer {SECRET}"

    # 2. Credential missing -> clean GatewayError with no network attempt.
    missing = MetaModelProvider(credential_resolver=lambda: None)
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted without credential")) as urlopen_mock:
        try:
            missing.invoke("text_generation", {"text": "x"}, "meta-no-cred")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.retryable is False
        else:
            raise AssertionError("invoke without credential succeeded")
        urlopen_mock.assert_not_called()

    # 3. Auth failure mapping.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=http_error(401)):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-auth")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("401 was not mapped")

    # 4. Quota mapping with retryable=True.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=http_error(429)):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-quota")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_QUOTA_EXCEEDED" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("429 was not mapped")

    # 5. Malformed JSON mapping.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", return_value=ChatResponse(b"not-json{{")):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-bad-json")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("malformed JSON was accepted")

    # 5b. Malformed schema mapping (valid JSON, wrong contract).
    with patch("app.gateway.meta_provider.urllib.request.urlopen", return_value=ChatResponse(json.dumps({"unexpected": 1}).encode())):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-bad-schema")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE", exc.code
        else:
            raise AssertionError("malformed schema was accepted")

    # 6. Timeout mapping.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-timeout")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_TIMEOUT" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("timeout was not mapped")

    # 6b. Network error mapping.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        try:
            live.invoke("text_generation", {"text": "x"}, "meta-net")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_NETWORK_ERROR" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("network error was not mapped")

    # 7. Oversized response rejection (bounds consistent with provider tests).
    tiny = MetaModelProvider(credential_resolver=lambda: SECRET, max_response_bytes=1024)

    class Oversized(ChatResponse):
        def read(self, limit: int) -> bytes:
            return b"x" * (limit + 1)

    with patch("app.gateway.meta_provider.urllib.request.urlopen", return_value=Oversized(b"")):
        try:
            tiny.invoke("text_generation", {"text": "x"}, "meta-huge")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_RESPONSE_TOO_LARGE", exc.code
        else:
            raise AssertionError("oversized response was accepted")

    # 8. Non-allowlisted host rejection before any network I/O.
    with patch("app.gateway.meta_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted to forbidden host")) as urlopen_mock:
        try:
            MetaModelProvider(credential_resolver=lambda: SECRET, base_url="https://evil.example/v1")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN", exc.code
        else:
            raise AssertionError("non-allowlisted host was accepted")
        urlopen_mock.assert_not_called()

    # 9. Gateway health in both modes.
    simulator_gateway = ModelGateway()
    simulator_health = simulator_gateway.health()
    track(simulator_health)
    assert simulator_health["simulator_only"] is True, simulator_health
    assert simulator_health["provider_source"] == "simulator", simulator_health

    gated_gateway = ModelGateway(providers=[MockModelProvider(), MetaModelProvider(credential_resolver=lambda: None)])
    gated_health = gated_gateway.health()
    assert gated_health["simulator_only"] is True and gated_health["provider_source"] == "simulator", gated_health

    live_gateway = ModelGateway(providers=[MockModelProvider(), MetaModelProvider(credential_resolver=lambda: SECRET)])
    live_health = live_gateway.health()
    track(live_health)
    assert live_health["simulator_only"] is False, live_health
    assert live_health["provider_source"] == "meta-live", live_health
    assert "meta" in live_health["providers"], live_health

    # Raw secret must never appear in normalized outputs or error surfaces.
    for blob in SAFE_STRINGS:
        assert SECRET not in blob, blob

    print("meta_provider: passed")


if __name__ == "__main__":
    main()
