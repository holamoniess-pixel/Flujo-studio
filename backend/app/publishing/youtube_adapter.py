from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from .contracts import PublishError


PLATFORM_ID = "youtube"
DISPLAY_NAME = "YouTube"
ADAPTER_VERSION = "youtube.publish.v1"
DEFAULT_CREDENTIAL_REF = "youtube"
UPLOAD_ENDPOINT = "https://www.googleapis.com/upload/youtube/v3/videos"
ALLOWED_HOSTS = frozenset({"www.googleapis.com"})


class YouTubePublishAdapter:
    """Reference platform adapter: YouTube Data API v3 upload.

    Follows the gateway adapter conventions (M1/M7/M9): the constructor
    takes a credential resolver, never a raw secret; the adapter is
    disabled until explicitly enabled; upload is fail-closed when no
    credential resolves. Tests patch urlopen; no network runs unmocked.
    """

    def __init__(
        self,
        *,
        credential_resolver: Callable[[], str | None],
        enabled: bool = False,
        timeout_seconds: float = 60.0,
        max_response_bytes: int = 2 * 1024 * 1024,
    ) -> None:
        self._credential_resolver = credential_resolver
        self.enabled = enabled
        self.timeout_seconds = max(1.0, min(timeout_seconds, 300.0))
        self.max_response_bytes = max(1024, min(max_response_bytes, 8 * 1024 * 1024))
        self._validate_endpoint()

    def _validate_endpoint(self) -> None:
        parsed = urllib.parse.urlparse(UPLOAD_ENDPOINT)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or not host or host not in ALLOWED_HOSTS:
            raise PublishError("ADAPTER_ENDPOINT_FORBIDDEN", "The YouTube upload endpoint must use HTTPS and an explicitly allowlisted host.", retryable=False)
        if parsed.username or parsed.password or parsed.fragment:
            raise PublishError("ADAPTER_ENDPOINT_INVALID", "The YouTube upload endpoint cannot contain credentials or fragments.", retryable=False)

    def platform_id(self) -> str:
        return PLATFORM_ID

    def display_name(self) -> str:
        return DISPLAY_NAME

    def adapter_version(self) -> str:
        return ADAPTER_VERSION

    def status(self) -> dict[str, Any]:
        configured = False
        if self.enabled:
            try:
                configured = bool(self._credential_resolver())
            except Exception:
                configured = False
        notes = (
            "Adapter is enabled and a credential reference is active."
            if configured
            else "Not configured. Store a YouTube credential and enable the adapter to connect."
        )
        return {
            "platform_id": PLATFORM_ID,
            "display_name": DISPLAY_NAME,
            "adapter_version": ADAPTER_VERSION,
            "configured": configured,
            "credential_reference": DEFAULT_CREDENTIAL_REF,
            "notes": notes,
        }

    def validate_bundle(self, bundle: dict[str, Any]) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        if not isinstance(bundle, dict):
            return [{"severity": "blocking", "code": "BUNDLE_INVALID", "message": "The publish bundle is not a structured payload."}]
        title = str(bundle.get("title") or "").strip()
        if not title:
            findings.append({"severity": "blocking", "code": "BUNDLE_TITLE_MISSING", "message": "YouTube requires a title for the upload."})
        elif len(title) > 100:
            findings.append({"severity": "blocking", "code": "BUNDLE_TITLE_TOO_LONG", "message": "YouTube titles are limited to 100 characters."})
        if not bundle.get("media_path"):
            findings.append({"severity": "blocking", "code": "BUNDLE_MEDIA_MISSING", "message": "YouTube requires a media payload for the upload."})
        description = str(bundle.get("description") or "")
        if len(description) > 5000:
            findings.append({"severity": "warning", "code": "BUNDLE_DESCRIPTION_TRUNCATED", "message": "YouTube descriptions longer than 5000 characters will be truncated by the platform."})
        return findings

    def _secret(self, credential_resolver: Callable[[], str | None] | None) -> str:
        resolver = credential_resolver or self._credential_resolver
        try:
            secret = resolver()
        except Exception as exc:
            raise PublishError("ADAPTER_CREDENTIAL_UNAVAILABLE", "The YouTube credential could not be resolved from the local vault.", retryable=False) from exc
        if not secret:
            raise PublishError("ADAPTER_CREDENTIAL_UNAVAILABLE", "No YouTube credential is stored. The adapter stays disconnected.", retryable=False)
        return secret

    def _build_request(self, bundle: dict[str, Any], secret: str) -> urllib.request.Request:
        metadata = {
            "snippet": {
                "title": str(bundle.get("title") or ""),
                "description": str(bundle.get("description") or ""),
                "tags": list(bundle.get("tags") or []),
                "categoryId": "22",
            },
            "status": {"privacyStatus": str(bundle.get("privacy") or "unlisted")},
        }
        boundary = f"flujo-youtube-{int(time.time() * 1000)}"
        media_path = str(bundle.get("media_path") or "")
        metadata_part = (
            f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n"
            f"{json.dumps(metadata, ensure_ascii=False)}\r\n"
        ).encode("utf-8")
        # The media payload streams at upload time; this reference build only
        # transmits the structured metadata part. A production build would
        # attach the binary part here under the same boundary.
        body = metadata_part + f"--{boundary}--\r\n".encode("utf-8")
        params = urllib.parse.urlencode({"uploadType": "multipart", "part": "snippet,status"})
        return urllib.request.Request(
            f"{UPLOAD_ENDPOINT}?{params}",
            data=body,
            headers={"content-type": f'multipart/related; boundary="{boundary}"', "authorization": f"Bearer {secret}", "x-flujo-media": media_path},
            method="POST",
        )

    def upload(self, bundle: dict[str, Any], credential_resolver: Callable[[], str | None] | None = None) -> dict[str, Any]:
        if not self.enabled:
            raise PublishError("ADAPTER_DISABLED", "The YouTube adapter is disabled until explicitly enabled.", retryable=False)
        secret = self._secret(credential_resolver)
        findings = self.validate_bundle(bundle)
        blocking = [finding for finding in findings if finding.get("severity") == "blocking"]
        if blocking:
            raise PublishError("BUNDLE_INVALID", blocking[0]["message"], retryable=False)
        request = self._build_request(bundle, secret)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            if exc.code in {401, 403}:
                raise PublishError("ADAPTER_AUTH_FAILED", f"YouTube rejected the upload credential (HTTP {exc.code}).", retryable=False) from exc
            if exc.code == 429:
                raise PublishError("ADAPTER_QUOTA_EXCEEDED", "YouTube rate-limited the upload request.", retryable=True) from exc
            raise PublishError("ADAPTER_HTTP_ERROR", f"YouTube returned HTTP {exc.code}.", retryable=exc.code >= 500) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise PublishError("ADAPTER_NETWORK_ERROR", "YouTube could not be reached safely.", retryable=True) from exc
        if len(body) > self.max_response_bytes:
            raise PublishError("ADAPTER_RESPONSE_TOO_LARGE", "YouTube returned an oversized response.", retryable=False)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PublishError("ADAPTER_RESPONSE_INVALID", "YouTube returned invalid JSON.", retryable=False) from exc
        if not isinstance(decoded, dict) or not decoded.get("id"):
            raise PublishError("ADAPTER_RESPONSE_INVALID", "YouTube response did not contain a video id.", retryable=False)
        return {"platform": PLATFORM_ID, "receipt_id": str(decoded["id"]), "status": "uploaded", "simulator": False}
