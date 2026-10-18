from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.gateway.contracts import GatewayError, HttpModelProvider, ModelDescriptor


MODEL = ModelDescriptor(
    "test-provider",
    "test-text",
    "1",
    ("text_generation",),
    ("text",),
    "test",
    1,
)


def main() -> None:
    try:
        HttpModelProvider(
            name="bad-http",
            endpoint="http://models.example.test/invoke",
            models=[MODEL],
            credential_resolver=lambda: "secret",
            allowed_hosts={"models.example.test"},
            enabled=True,
        )
    except GatewayError as exc:
        assert exc.code == "PROVIDER_ENDPOINT_FORBIDDEN"
    else:
        raise AssertionError("non-HTTPS provider endpoint was accepted")

    provider = HttpModelProvider(
        name="disabled-provider",
        endpoint="https://models.example.test/invoke",
        models=[MODEL],
        credential_resolver=lambda: None,
        allowed_hosts={"models.example.test"},
        enabled=False,
    )
    assert provider.catalog() == []
    try:
        provider.invoke("text_generation", {"text": "x"}, "gateway-disabled")
    except GatewayError as exc:
        assert exc.code == "PROVIDER_DISABLED"
    else:
        raise AssertionError("disabled provider invoked successfully")

    provider = HttpModelProvider(
        name="credential-provider",
        endpoint="https://models.example.test/invoke",
        models=[MODEL],
        credential_resolver=lambda: None,
        allowed_hosts={"models.example.test"},
        enabled=True,
    )
    assert provider.catalog() == [MODEL]
    try:
        provider.invoke("text_generation", {"text": "x"}, "gateway-no-credential")
    except GatewayError as exc:
        assert exc.code == "PROVIDER_CREDENTIAL_UNAVAILABLE"
    else:
        raise AssertionError("provider invoked without credential")

    class Response:
        def read(self, limit: int) -> bytes:
            assert limit > 0
            return json.dumps({"output": {"text": "normalized"}, "provider_policy": "reviewed"}).encode()

        def __enter__(self) -> "Response":
            return self

        def __exit__(self, *_: object) -> None:
            return None

    provider = HttpModelProvider(
        name="normalized-provider",
        endpoint="https://models.example.test/invoke",
        models=[MODEL],
        credential_resolver=lambda: "secret-value",
        allowed_hosts={"models.example.test"},
        enabled=True,
    )
    with patch("app.gateway.contracts.urllib.request.urlopen", return_value=Response()):
        result = provider.invoke("text_generation", {"text": "x"}, "gateway-normalized")
    assert result == {"output": {"text": "normalized"}, "provider_policy": "reviewed"}

    class OversizedResponse(Response):
        def read(self, limit: int) -> bytes:
            return b"x" * (limit + 1)

    with patch("app.gateway.contracts.urllib.request.urlopen", return_value=OversizedResponse()):
        try:
            provider.invoke("text_generation", {"text": "x"}, "gateway-large")
        except GatewayError as exc:
            assert exc.code == "PROVIDER_RESPONSE_TOO_LARGE"
        else:
            raise AssertionError("oversized provider response was accepted")

    print("gateway_provider: passed")


if __name__ == "__main__":
    main()

