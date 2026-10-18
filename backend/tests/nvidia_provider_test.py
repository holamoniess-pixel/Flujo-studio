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

from app.gateway.contracts import GatewayError, ModelGateway, MockModelProvider, SimulatorProvider
from app.gateway.nvidia_provider import DEFAULT_ALLOWED_HOSTS, DEFAULT_BASE_URL, DEFAULT_MODEL_ID, NvidiaModelProvider

SECRET = "nvapi-test-secret-not-real"
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


def chat_body(text: str = "hello nvidia") -> bytes:
    return json.dumps({"id": "chatcmpl-1", "choices": [{"message": {"role": "assistant", "content": text}}]}).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://integrate.api.nvidia.com/v1/chat/completions", code, f"HTTP {code}", {}, io.BytesIO(b"{}"))


def main() -> None:
    assert DEFAULT_BASE_URL == "https://integrate.api.nvidia.com/v1", DEFAULT_BASE_URL
    assert DEFAULT_MODEL_ID == "nvidia/nemotron-3-ultra-550b-a55b", DEFAULT_MODEL_ID
    assert DEFAULT_ALLOWED_HOSTS == frozenset({"integrate.api.nvidia.com"}), DEFAULT_ALLOWED_HOSTS

    # Constructor must never accept a raw secret as a plain argument.
    params = set(inspect.signature(NvidiaModelProvider.__init__).parameters)
    assert "secret" not in params and "api_key" not in params and "apiKey" not in params, params

    live = NvidiaModelProvider(credential_resolver=lambda: SECRET)
    assert live.catalog() and live.catalog()[0].model_id == DEFAULT_MODEL_ID
    assert live.catalog()[0].provider == "nvidia"
    assert set(live.catalog()[0].capabilities) == {"text_generation", "vision", "judge"}
    assert repr(live) and SECRET not in repr(live)

    # Credential-gated catalog: no reference -> no live models advertised.
    assert NvidiaModelProvider(credential_resolver=lambda: None).catalog() == []

    # 1. Successful text_generation normalization.
    seen: dict[str, object] = {}

    def fake_success(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["body"] = json.loads(request.data.decode("utf-8"))
        return ChatResponse(chat_body("live nemotron reply"))

    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=fake_success):
        result = live.invoke("text_generation", {"text": "say hi"}, "nvidia-ok-1")
    track(result)
    assert set(result) == {"output", "provider_policy"}, result
    assert result["output"]["text"] == "live nemotron reply", result
    assert result["provider_policy"] == "nvidia-live", result
    assert str(seen["url"]) == DEFAULT_BASE_URL + "/chat/completions", seen
    assert seen["auth"] == f"Bearer {SECRET}"
    assert seen["body"]["model"] == DEFAULT_MODEL_ID, seen["body"]

    # 2. Credential missing -> clean GatewayError with no network attempt.
    missing = NvidiaModelProvider(credential_resolver=lambda: None)
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted without credential")) as urlopen_mock:
        try:
            missing.invoke("text_generation", {"text": "x"}, "nvidia-no-cred")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False
        else:
            raise AssertionError("invoke without credential succeeded")
        urlopen_mock.assert_not_called()

    # 3. Auth failure mapping.
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=http_error(401)):
        try:
            live.invoke("text_generation", {"text": "x"}, "nvidia-auth")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_AUTH_FAILED" and exc.retryable is False, (exc.code, exc.retryable)
        else:
            raise AssertionError("401 was not mapped")

    # 4. Quota mapping with retryable=True.
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=http_error(429)):
        try:
            live.invoke("text_generation", {"text": "x"}, "nvidia-quota")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_QUOTA_EXCEEDED" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("429 was not mapped")

    # 5. Server error mapping with retryable=True.
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=http_error(500)):
        try:
            live.invoke("text_generation", {"text": "x"}, "nvidia-500")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_NETWORK_ERROR" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("500 was not mapped")

    # 6. Timeout mapping with retryable=True.
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        try:
            live.invoke("text_generation", {"text": "x"}, "nvidia-timeout")
        except GatewayError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "GATEWAY_TIMEOUT" and exc.retryable is True, (exc.code, exc.retryable)
        else:
            raise AssertionError("timeout was not mapped")

    # 7. Unsupported capability.
    try:
        live.invoke("image_generation", {"text": "x"}, "nvidia-unsupported")
    except GatewayError as exc:
        assert exc.code == "UNSUPPORTED_CAPABILITY"
    else:
        raise AssertionError("unsupported capability was accepted")

    # 8. Non-allowlisted host rejected before any network I/O.
    with patch("app.gateway.nvidia_provider.urllib.request.urlopen", side_effect=AssertionError("network attempted")) as urlopen_mock:
        try:
            NvidiaModelProvider(credential_resolver=lambda: SECRET, base_url="https://evil.example.test/v1")
        except GatewayError as exc:
            assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN"
        else:
            raise AssertionError("non-allowlisted host was accepted")
        urlopen_mock.assert_not_called()

    # 9. NVIDIA is the default selection among credentialed live providers.
    gateway = ModelGateway(
        providers=[
            SimulatorProvider(),
            NvidiaModelProvider(credential_resolver=lambda: SECRET),
            MockModelProvider(),
        ],
        credential_probe=lambda: True,
    )
    provider, model = gateway._select("text_generation")
    assert model.provider == "nvidia" and model.model_id == DEFAULT_MODEL_ID, (model.provider, model.model_id)
    health = gateway.health()
    track(health)
    assert health["simulator_only"] is False, health
    assert health["provider_source"] == "nvidia-live", health
    assert health["default_provider"] == "nvidia", health
    assert health["reason"] is None, health
    assert health["credentialed"] is True, health
    assert health["providers"][0] == "nvidia", health["providers"]

    # 10. Uncredentialed gateway: honest labeled simulator fallback.
    sim_gateway = ModelGateway(providers=[SimulatorProvider()], credential_probe=lambda: False)
    sim_health = sim_gateway.health()
    track(sim_health)
    assert sim_health["simulator_only"] is True, sim_health
    assert sim_health["provider_source"] == "simulator", sim_health
    assert sim_health["default_provider"] == "simulator", sim_health
    assert sim_health["reason"] == "no provider key configured", sim_health
    result = sim_gateway.invoke("text_generation", {"text": "x"}, "nvidia-sim-1")
    assert result.provider == "simulator", result.provider
    assert result.output.get("simulator") is True, result.output
    assert result.output.get("reason") == "no provider key configured", result.output

    # 11. Default-constructed gateway carries no mocks: mocks are test doubles only.
    assert ModelGateway().providers == []
    assert ModelGateway().health()["model_count"] == 0

    # Raw secret must never appear in normalized outputs or error surfaces.
    for blob in SAFE_STRINGS:
        assert SECRET not in blob, blob

    print("nvidia_provider: passed")


if __name__ == "__main__":
    main()
