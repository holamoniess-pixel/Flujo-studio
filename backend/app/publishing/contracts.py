from __future__ import annotations

from typing import Any, Callable, Protocol


class PublishError(Exception):
    """Explicit publish failure: code, human message, and retry hint."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class PublishAdapter(Protocol):
    """Interface every platform adapter must satisfy.

    Conventions follow the Model Gateway provider adapters (M1/M7/M9):
    credentials arrive only as a resolver callable, the constructor never
    accepts a raw secret, and the adapter reports an honest
    "not configured" state until a credential reference exists.
    """

    def platform_id(self) -> str: ...
    def display_name(self) -> str: ...
    def adapter_version(self) -> str: ...
    def status(self) -> dict[str, Any]: ...
    def validate_bundle(self, bundle: dict[str, Any]) -> list[dict[str, Any]]: ...
    def upload(self, bundle: dict[str, Any], credential_resolver: Callable[[], str | None] | None = None) -> dict[str, Any]: ...


class MockPublishAdapter:
    """Deterministic platform-shaped simulator for prep/upload tests.

    TEST DOUBLE ONLY: never registered in the production adapter registry
    (see backend/app/main.py, which registers only YouTubePublishAdapter).
    Mirrors the gateway MockModelProvider: zero network, stable receipt
    shape. A second platform can register beside the reference adapter
    without changing any existing code.
    """

    TEST_DOUBLE = True

    def __init__(self, *, platform_id: str = "mock-video-platform", configured: bool = True) -> None:
        self._platform_id = platform_id[:80]
        self._configured = bool(configured)

    def platform_id(self) -> str:
        return self._platform_id

    def display_name(self) -> str:
        return "Mock Video Platform"

    def adapter_version(self) -> str:
        return "mock.publish.v1"

    def status(self) -> dict[str, Any]:
        return {
            "platform_id": self.platform_id(),
            "display_name": self.display_name(),
            "adapter_version": self.adapter_version(),
            "configured": self._configured,
            "credential_reference": None,
            "notes": "Test double only. Never performs external network calls.",
        }

    def validate_bundle(self, bundle: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if not isinstance(bundle, dict) or not bundle.get("media_path"):
            findings.append({"severity": "blocking", "code": "BUNDLE_MEDIA_MISSING", "message": "The bundle does not reference a media payload."})
        if not isinstance(bundle, dict) or not bundle.get("title"):
            findings.append({"severity": "blocking", "code": "BUNDLE_TITLE_MISSING", "message": "The bundle does not declare a title."})
        return findings

    def upload(self, bundle: dict[str, Any], credential_resolver: Callable[[], str | None] | None = None) -> dict[str, Any]:
        if not self._configured:
            raise PublishError("ADAPTER_NOT_CONFIGURED", f"Platform '{self.platform_id()}' is not configured.", retryable=False)
        findings = self.validate_bundle(bundle)
        if findings:
            raise PublishError("BUNDLE_INVALID", findings[0]["message"], retryable=False)
        return {
            "platform": self.platform_id(),
            "receipt_id": f"mock-upload-{bundle.get('run_id', 'unknown')}",
            "status": "uploaded",
            "simulator": True,
        }


class PublishAdapterRegistry:
    """Holds platform adapters by platform id; adding a platform is register-only."""

    def __init__(self) -> None:
        self._adapters: dict[str, PublishAdapter] = {}

    def register(self, adapter: PublishAdapter) -> None:
        platform_id = adapter.platform_id()
        if not platform_id or platform_id in self._adapters:
            raise PublishError("ADAPTER_REGISTRATION_INVALID", f"Platform id '{platform_id}' is empty or already registered.", retryable=False)
        self._adapters[platform_id] = adapter

    def get(self, platform_id: str) -> PublishAdapter:
        adapter = self._adapters.get(platform_id or "")
        if adapter is None:
            raise PublishError("ADAPTER_NOT_FOUND", f"No publish adapter is registered for platform '{platform_id}'.", retryable=False)
        return adapter

    def statuses(self) -> list[dict[str, Any]]:
        return [adapter.status() for adapter in self._adapters.values()]

    def platform_ids(self) -> list[str]:
        return sorted(self._adapters)
