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
from app.gateway.openai_provider import OpenAICompatibleProvider
from app.gateway.zen_provider import (
    DEFAULT_BASE_URL,
    DEFAULT_MODEL_ID,
    FREE_MODEL_IDS,
    KNOWN_MODEL_IDS,
    OpenCodeZenProvider,
    endpoint_for_model,
)

SECRET = "sk-zen-test-secret-xyz-987654"
SAFE_STRINGS: list[str] = []


def track(value: object) -> None:
    try:
        SAFE_STRINGS.append(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))
    except (TypeError, ValueError):
        SAFE_STRINGS.append(str(value))


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def read(self, limit: int) -> bytes:
        assert limit > 0
        return self._payload

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None


def http_error(code: int, body: bytes = b"{}") -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://opencode.ai/zen/v1/chat/completions", code, f"HTTP {code}", {}, io.BytesIO(body))


def main() -> None:
    # Constructor must never accept a raw secret as a plain argument.
    params = set(inspect.signature(OpenCodeZenProvider.__init__).parameters)
    assert "secret" not in params and "api_key" not in params and "apiKey" not in params, params

    # Defaults: name, base_url, model_id, credential_ref.
    defaulted = OpenCodeZenProvider(credential_resolver=lambda: SECRET)
    assert defaulted.name == "opencode-zen", defaulted.name
    assert defaulted.base_url == DEFAULT_BASE_URL == "https://opencode.ai/zen/v1"
    assert defaulted.model_id == DEFAULT_MODEL_ID == "muse-spark-1.3-contributor-free", defaulted.model_id
    assert defaulted.credential_ref == "opencode-zen", defaulted.credential_ref
    assert repr(defaulted) and SECRET not in repr(defaulted)

    # Catalog: credentialed -> all 87 known ids; keyless -> only the 14 free ids.
    live_catalog = defaulted.catalog()
    assert len(live_catalog) == 87 == len(KNOWN_MODEL_IDS), len(live_catalog)
    assert {d.model_id for d in live_catalog} == set(KNOWN_MODEL_IDS)
    assert all(d.provider == "opencode-zen" for d in live_catalog)

    keyless = OpenCodeZenProvider(credential_resolver=lambda: None)
    free_catalog = keyless.catalog()
    assert len(free_catalog) == 14 == len(FREE_MODEL_IDS), len(free_catalog)
    assert {d.model_id for d in free_catalog} == set(FREE_MODEL_IDS)
    assert set(FREE_MODEL_IDS) == {
        "big-pickle", "space-bunny-free", "longcat-2.5-preview-free", "step-5-preview-free",
        "exo-free", "mimo-v2.6-flash-free", "mimo-v2.5-free", "ling-3.1-flash-free",
        "ling-3.0-flash-fin-free", "nemotron-3-ultra-free", "nemotron-3.5-lightning-free",
        "muse-spark-1.3-contributor-free", "muse-spark-1.2-contributor-free", "jev-1.13-free",
    }

    # Capabilities: default text_generation; vision only for the vision id.
    assert set(defaulted.capabilities) == {"text_generation"}
    vision_provider = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="deepseek-v4-flash-vision-exp")
    assert set(vision_provider.capabilities) == {"text_generation", "vision"}
    vision_descriptors = [d for d in live_catalog if d.model_id == "deepseek-v4-flash-vision-exp"]
    assert len(vision_descriptors) == 1 and "vision" in vision_descriptors[0].capabilities
    assert all(set(d.capabilities) == {"text_generation"} for d in live_catalog if d.model_id != "deepseek-v4-flash-vision-exp")

    # Endpoint routing: 2 ids per style + unknown default (+ gemini google-style).
    assert endpoint_for_model("gpt-5") == "responses"
    assert endpoint_for_model("grok-4.7") == "responses"
    assert endpoint_for_model("muse-spark-1.3-contributor-free") == "responses"
    assert endpoint_for_model("claude-sonnet-5") == "messages"
    assert endpoint_for_model("qwen3.8-flash") == "messages"
    assert endpoint_for_model("deepseek-v4-pro") == "chat"
    assert endpoint_for_model("big-pickle") == "chat"
    assert endpoint_for_model("gemini-3-flash") == "models"
    assert endpoint_for_model("gemini-3.5-flash") == "models"
    assert endpoint_for_model("some-future-model-9") == "chat"

    # 1. Responses style normalization (gpt-5).
    responses_provider = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="gpt-5")
    seen: dict[str, object] = {}

    def fake_responses(request, timeout=None):
        seen["responses_url"] = request.full_url
        seen["responses_auth"] = request.get_header("Authorization")
        seen["responses_body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        payload = {"id": "resp-1", "output": [{"type": "output_text", "text": "responses reply"}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_responses):
        result = responses_provider.invoke("text_generation", {"text": "say hi"}, "zen-resp-1")
    track(result)
    assert set(result) == {"output", "provider_policy"}, result
    assert result["output"]["text"] == "responses reply", result
    assert result["provider_policy"] == "opencode-zen-live", result
    assert str(seen["responses_url"]) == DEFAULT_BASE_URL + "/responses", seen
    assert seen["responses_auth"] == f"Bearer {SECRET}"
    body = json.loads(str(seen["responses_body"]))
    assert set(body) == {"model", "input", "max_output_tokens"}, body
    assert body["model"] == "gpt-5" and body["input"] == "say hi"

    # 1b. Responses style alternate shape: message content blocks.
    def fake_responses_msg(request, timeout=None):
        payload = {"id": "resp-2", "output": [{"type": "message", "content": [{"type": "output_text", "text": "msg reply"}]}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_responses_msg):
        alt = responses_provider.invoke("text_generation", {"text": "hi"}, "zen-resp-2")
    track(alt)
    assert alt["output"]["text"] == "msg reply", alt

    # 2. Messages style normalization (claude-sonnet-5).
    messages_provider = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="claude-sonnet-5")

    def fake_messages(request, timeout=None):
        seen["messages_url"] = request.full_url
        seen["messages_body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        payload = {"id": "msg-1", "content": [{"type": "text", "text": "messages reply"}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_messages):
        result = messages_provider.invoke("text_generation", {"text": "hello"}, "zen-msg-1")
    track(result)
    assert result["output"]["text"] == "messages reply", result
    assert result["provider_policy"] == "opencode-zen-live", result
    assert str(seen["messages_url"]) == DEFAULT_BASE_URL + "/messages", seen
    body = json.loads(str(seen["messages_body"]))
    assert set(body) == {"model", "max_tokens", "messages"}, body
    assert body["model"] == "claude-sonnet-5"
    assert body["messages"] == [{"role": "user", "content": "hello"}], body

    # 3. Chat style normalization (deepseek-v4-pro).
    chat_provider = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="deepseek-v4-pro")

    def fake_chat(request, timeout=None):
        seen["chat_url"] = request.full_url
        seen["chat_body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        payload = {"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": "chat reply"}}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_chat):
        result = chat_provider.invoke("text_generation", {"text": "hey"}, "zen-chat-1")
    track(result)
    assert result["output"]["text"] == "chat reply", result
    assert str(seen["chat_url"]) == DEFAULT_BASE_URL + "/chat/completions", seen
    assert "deepseek-v4-pro" in str(seen["chat_body"])

    # 4. Credential missing + paid model -> clean GatewayError, no network.
    paid_keyless = OpenCodeZenProvider(credential_resolver=lambda: None, model_id="gpt-5")
    assert paid_keyless.catalog() and all(d.model_id in FREE_MODEL_IDS for d in paid_keyless.catalog())
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted without credential")) as urlopen_mock:
        try:
            paid_keyless.invoke("text_generation", {"text": "x"}, "zen-no-cred")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("invoke without credential succeeded")
        urlopen_mock.assert_not_called()

    # 4b. Keyless free model -> attempt WITHOUT Authorization header (fail-soft).
    free_keyless = OpenCodeZenProvider(credential_resolver=lambda: None, model_id="exo-free")

    def fake_keyless(request, timeout=None):
        seen["keyless_auth"] = request.get_header("Authorization")
        seen["keyless_url"] = request.full_url
        payload = {"id": "chatcmpl-2", "choices": [{"message": {"role": "assistant", "content": "free reply"}}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_keyless):
        result = free_keyless.invoke("text_generation", {"text": "hi"}, "zen-free-1")
    track(result)
    assert result["output"]["text"] == "free reply", result
    assert seen["keyless_auth"] in (None, ""), seen
    assert str(seen["keyless_url"]).endswith("/chat/completions"), seen

    # 5. Auth failure mapping (401).
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=http_error(401)):
        try:
            responses_provider.invoke("text_generation", {"text": "x"}, "zen-auth")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("401 was not mapped")

    # 5b. FreeTierError (403, keyless on paid model): auth code, non-retryable,
    # server message preserved verbatim.
    freetier_body = json.dumps({"error": {"message": "FreeTierError: keyless access is only available inside the OpenCode CLI"}}).encode()
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=http_error(403, freetier_body)):
        try:
            free_keyless_paid = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="gpt-5")
            free_keyless_paid.invoke("text_generation", {"text": "x"}, "zen-freetier")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
            assert "FreeTierError" in exc.message, exc.message
        else:
            raise AssertionError("403 FreeTierError was not mapped")

    # 6. Quota mapping with retryable=True.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=http_error(429)):
        try:
            responses_provider.invoke("text_generation", {"text": "x"}, "zen-quota")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_QUOTA_EXCEEDED" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("429 was not mapped")

    # 7. Malformed JSON mapping.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", return_value=FakeResponse(b"not-json{{")):
        try:
            responses_provider.invoke("text_generation", {"text": "x"}, "zen-bad-json")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("malformed JSON was accepted")

    # 7b. Malformed schema mapping (valid JSON, wrong contract).
    with patch("app.gateway.zen_provider.urllib.request.urlopen", return_value=FakeResponse(json.dumps({"unexpected": 1}).encode())):
        try:
            messages_provider.invoke("text_generation", {"text": "x"}, "zen-bad-schema")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_BAD_RESPONSE", exc.code
        else:
            raise AssertionError("malformed schema was accepted")

    # 8. Timeout mapping.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            responses_provider.invoke("text_generation", {"text": "x"}, "zen-timeout")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_TIMEOUT" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("timeout was not mapped")

    # 8b. Network error mapping.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=urllib.error.URLError("connection refused")):
        try:
            responses_provider.invoke("text_generation", {"text": "x"}, "zen-net")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_NETWORK_ERROR" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("network error was not mapped")

    # 9. Oversized response rejection.
    tiny = OpenCodeZenProvider(credential_resolver=lambda: SECRET, max_response_bytes=1024)

    class Oversized(FakeResponse):
        def read(self, limit: int) -> bytes:
            return b"x" * (limit + 1)

    with patch("app.gateway.zen_provider.urllib.request.urlopen", return_value=Oversized(b"")):
        try:
            tiny.invoke("text_generation", {"text": "x"}, "zen-huge")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_RESPONSE_TOO_LARGE", exc.code
        else:
            raise AssertionError("oversized response was accepted")

    # 10. Non-allowlisted host rejection before any network I/O.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted to forbidden host")) as urlopen_mock:
        try:
            OpenCodeZenProvider(credential_resolver=lambda: SECRET, base_url="https://evil.example/v1")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN", exc.code
        else:
            raise AssertionError("non-allowlisted host was accepted")
        urlopen_mock.assert_not_called()

    # 11. Custom model_id honored (request body + catalog descriptor).
    custom = OpenCodeZenProvider(credential_resolver=lambda: SECRET, model_id="gpt-5.4-mini")

    def fake_custom(request, timeout=None):
        seen["custom_body"] = request.data.decode("utf-8") if isinstance(request.data, bytes) else ""
        payload = {"id": "resp-3", "output": [{"type": "output_text", "text": "custom reply"}]}
        return FakeResponse(json.dumps(payload).encode())

    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_custom):
        custom_result = custom.invoke("text_generation", {"text": "hi"}, "zen-custom-1")
    track(custom_result)
    assert custom_result["output"]["text"] == "custom reply"
    assert "gpt-5.4-mini" in str(seen["custom_body"])

    # 12. Gateway-level: zen + openai-compatible both credentialed -> diversity 2.
    both = ModelGateway(
        providers=[
            OpenCodeZenProvider(credential_resolver=lambda: SECRET),
            OpenAICompatibleProvider(credential_resolver=lambda: "sk-openai-x"),
        ]
    )
    diversity = both.diversity()
    track(diversity)
    assert set(diversity["text_generation"]) == {"opencode-zen", "openai-compatible"}, diversity["text_generation"]
    both_health = both.health()
    track(both_health)
    assert "opencode-zen" in both_health["providers"] and "openai-compatible" in both_health["providers"], both_health
    assert both_health["simulator_only"] is False, both_health
    assert both_health["provider_source"] == "opencode-zen-live", both_health
    assert both_health["providers"][0] == "opencode-zen", both_health

    # 13. Keyless zen alone: free models advertised, gateway not simulator-only
    # by provider count but zen stays honest about paid dormancy.
    free_only = ModelGateway(providers=[MockModelProvider(), OpenCodeZenProvider(credential_resolver=lambda: None)])
    free_health = free_only.health()
    track(free_health)
    assert "opencode-zen" in free_health["providers"], free_health
    assert free_health["model_count"] == 5 + 14, free_health

    # 14. Live /models discovery: merges new ids, keeps the hardcoded baseline.
    def fake_models(request, timeout=None):
        assert request.full_url == DEFAULT_BASE_URL + "/models", request.full_url
        assert request.get_method() == "GET"
        assert request.get_header("Authorization") == f"Bearer {SECRET}"
        payload = {"data": [{"id": "gpt-6-astra"}, {"id": "brand-new-model-z9"}, {"id": "  "}, {"nope": 1}]}
        return FakeResponse(json.dumps(payload).encode())

    refreshable = OpenCodeZenProvider(credential_resolver=lambda: SECRET)
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_models):
        report = refreshable.refresh_catalog()
    track(report)
    assert report["added"] == ["brand-new-model-z9"], report
    assert report["total"] == 88, report
    assert report["baseline"] == 87, report
    assert len(refreshable.catalog()) == 88, len(refreshable.catalog())
    assert {d.model_id for d in refreshable.catalog()} >= set(KNOWN_MODEL_IDS) | {"brand-new-model-z9"}
    # Newly discovered ids route via the prefix fallback (unknown -> chat).
    assert endpoint_for_model("brand-new-model-z9") == "chat"
    # A second refresh with the same listing adds nothing.
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_models):
        again = refreshable.refresh_catalog()
    assert again["added"] == [] and again["total"] == 88, again

    # 15. Discovery failure surfaces honestly and leaves the baseline intact.
    def fake_401(request, timeout=None):
        raise urllib.error.HTTPError(request.full_url, 401, "Unauthorized", {}, io.BytesIO(b"{}"))

    failing = OpenCodeZenProvider(credential_resolver=lambda: SECRET)
    with patch("app.gateway.zen_provider.urllib.request.urlopen", side_effect=fake_401):
        try:
            failing.refresh_catalog()
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False
        else:
            raise AssertionError("401 on /models was not mapped")
    assert len(failing.catalog()) == 87, len(failing.catalog())

    # Raw secret must never appear in normalized outputs or error surfaces.
    for blob in SAFE_STRINGS:
        assert SECRET not in blob, blob

    print("zen_provider: passed")


if __name__ == "__main__":
    main()
