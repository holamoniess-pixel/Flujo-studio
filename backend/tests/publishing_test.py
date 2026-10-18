from __future__ import annotations

import inspect
import io
import json
import sys
import urllib.error
import zipfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.publishing import (
    PREP_STEP_NAMES,
    MockPublishAdapter,
    PublishAdapterRegistry,
    PublishError,
    PublishPrepPipeline,
    YouTubePublishAdapter,
)
from app.publishing.youtube_adapter import DEFAULT_CREDENTIAL_REF, PLATFORM_ID

SECRET = "yt-test-secret-000111222333444555"
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


def upload_body(video_id: str = "yt-video-123") -> bytes:
    return json.dumps({"kind": "youtube#video", "id": video_id}).encode()


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://www.googleapis.com/upload/youtube/v3/videos", code, f"HTTP {code}", {}, io.BytesIO(b"{}"))


class PassDecision:
    allowed = True
    status = "PASS"
    reason = "Guard passed deterministic local checks."
    policy_version = "guard.policy.v2"


class BlockDecision:
    allowed = False
    status = "BLOCK"
    reason = "Guard blocked content under the versioned local policy."
    policy_version = "guard.policy.v2"


class FakeGuard:
    def __init__(self, decision: object) -> None:
        self._decision = decision
        self.calls = 0

    def publish_gate(self, *, artifact_summary: str, modality: str, consent: dict) -> object:
        self.calls += 1
        assert isinstance(artifact_summary, str) and artifact_summary
        assert modality in {"text", "image", "video", "audio", "ocr", "metadata", "document"}
        return self._decision


class MediaResult:
    def __init__(self, status: str, error_code: str | None = None, message: str = "", checksum: str | None = None) -> None:
        self.status = status
        self.error_code = error_code
        self.message = message
        self.checksum = checksum


def media_run_factory(thumbnail: MediaResult, checksum: MediaResult, files: dict[str, Path]) -> object:
    def run(operation: str, inputs: list[Path], output: Path | None, options: dict) -> MediaResult:
        result = thumbnail if operation == "thumbnail" else checksum
        if operation == "thumbnail" and result.status == "completed" and output is not None:
            output.write_bytes(b"fake-jpeg-bytes")
        if operation == "checksum":
            files["sealed"] = inputs[0]
        return result

    return run


def main() -> None:
    # 1. Constructor must never accept a raw secret as a plain argument.
    params = set(inspect.signature(YouTubePublishAdapter.__init__).parameters)
    assert "secret" not in params and "api_key" not in params and "apiKey" not in params, params

    # 2. Registry accepts the reference adapter plus a second platform with no rework.
    registry = PublishAdapterRegistry()
    youtube = YouTubePublishAdapter(credential_resolver=lambda: None)
    registry.register(youtube)
    registry.register(MockPublishAdapter())
    assert registry.platform_ids() == ["mock-video-platform", "youtube"]
    try:
        registry.register(MockPublishAdapter(platform_id="youtube"))
    except PublishError as exc:
        assert exc.code == "ADAPTER_REGISTRATION_INVALID"
    else:
        raise AssertionError("duplicate platform registration succeeded")
    try:
        registry.get("vimeo")
    except PublishError as exc:
        assert exc.code == "ADAPTER_NOT_FOUND"
    else:
        raise AssertionError("unknown platform lookup succeeded")

    # 3. Disabled adapter is fail-closed without any network attempt.
    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", side_effect=AssertionError("network attempted while disabled")) as urlopen_mock:
        try:
            youtube.upload({"title": "t", "media_path": "/tmp/x.zip"})
        except PublishError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "ADAPTER_DISABLED" and exc.retryable is False
        else:
            raise AssertionError("disabled adapter uploaded")
        urlopen_mock.assert_not_called()

    # 4. Enabled but uncredentialed adapter refuses honestly, still no network.
    enabled_no_cred = YouTubePublishAdapter(credential_resolver=lambda: None, enabled=True)
    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", side_effect=AssertionError("network attempted without credential")) as urlopen_mock:
        try:
            enabled_no_cred.upload({"title": "t", "media_path": "/tmp/x.zip"})
        except PublishError as exc:
            track({"code": exc.code, "message": exc.message})
            assert exc.code == "ADAPTER_CREDENTIAL_UNAVAILABLE" and exc.retryable is False
        else:
            raise AssertionError("uncredentialed upload succeeded")
        urlopen_mock.assert_not_called()
    status = enabled_no_cred.status()
    track(status)
    assert status["platform_id"] == PLATFORM_ID == "youtube"
    assert status["configured"] is False
    assert status["credential_reference"] == DEFAULT_CREDENTIAL_REF == "youtube"
    assert SECRET not in json.dumps(status)

    # 5. Configured adapter uploads with mocked HTTP; secret never leaks into tracked output.
    live = YouTubePublishAdapter(credential_resolver=lambda: SECRET, enabled=True)
    assert live.status()["configured"] is True
    seen: dict[str, object] = {}

    def fake_upload(request, timeout=None):
        seen["url"] = request.full_url
        seen["auth"] = request.get_header("Authorization")
        seen["content_type"] = request.get_header("Content-type")
        return FakeResponse(upload_body("yt-live-1"))

    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", side_effect=fake_upload):
        receipt = live.upload({"title": "Flujo test export", "description": "d", "tags": ["flujo"], "privacy": "unlisted", "media_path": "/tmp/x.zip"})
    track(receipt)
    assert receipt == {"platform": "youtube", "receipt_id": "yt-live-1", "status": "uploaded", "simulator": False}, receipt
    assert str(seen["url"]).startswith("https://www.googleapis.com/upload/youtube/v3/videos"), seen
    assert seen["auth"] == f"Bearer {SECRET}"
    assert "multipart/related" in str(seen["content_type"])

    # 6. Bundle validation is blocking before any network.
    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", side_effect=AssertionError("network attempted with invalid bundle")) as urlopen_mock:
        for bundle, code in (({"media_path": "/tmp/x.zip"}, "BUNDLE_TITLE_MISSING"), ({"title": "t"}, "BUNDLE_MEDIA_MISSING"), ({"title": "t" * 101, "media_path": "/tmp/x.zip"}, "BUNDLE_TITLE_TOO_LONG")):
            try:
                live.upload(bundle)
            except PublishError as exc:
                assert exc.code == "BUNDLE_INVALID", exc.code
            else:
                raise AssertionError("invalid bundle uploaded")
        urlopen_mock.assert_not_called()

    # 7. HTTP error mapping.
    for code, mapped, retryable in ((401, "ADAPTER_AUTH_FAILED", False), (403, "ADAPTER_AUTH_FAILED", False), (429, "ADAPTER_QUOTA_EXCEEDED", True), (500, "ADAPTER_HTTP_ERROR", True)):
        with patch("app.publishing.youtube_adapter.urllib.request.urlopen", side_effect=http_error(code)):
            try:
                live.upload({"title": "t", "media_path": "/tmp/x.zip"})
            except PublishError as exc:
                assert exc.code == mapped and exc.retryable is retryable, (exc.code, mapped)
            else:
                raise AssertionError(f"HTTP {code} was not mapped")
    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", return_value=FakeResponse(b"not-json{{")):
        try:
            live.upload({"title": "t", "media_path": "/tmp/x.zip"})
        except PublishError as exc:
            assert exc.code == "ADAPTER_RESPONSE_INVALID"
        else:
            raise AssertionError("malformed JSON was accepted")
    with patch("app.publishing.youtube_adapter.urllib.request.urlopen", return_value=FakeResponse(json.dumps({"kind": "youtube#video"}).encode())):
        try:
            live.upload({"title": "t", "media_path": "/tmp/x.zip"})
        except PublishError as exc:
            assert exc.code == "ADAPTER_RESPONSE_INVALID"
        else:
            raise AssertionError("response without video id was accepted")

    # 8. Prep pipeline happy path: guard PASS, all steps deterministic.
    import tempfile

    with tempfile.TemporaryDirectory(prefix="flujo-prep-") as directory:
        bundle_root = Path(directory)
        payload = bundle_root / "media" / "scene.mp4"
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"fake-video-bytes" * 100)
        files: dict[str, Path] = {}
        pipeline = PublishPrepPipeline(
            guard=FakeGuard(PassDecision()),
            media_run=media_run_factory(MediaResult("completed"), MediaResult("completed", checksum="abc123"), files),
            bundle_root=bundle_root,
            now=lambda: "2026-10-12T00:00:00+00:00",
        )
        result = pipeline.run(
            run_id="prep_happy",
            artifact={"id": "artifact_1", "name": "Citrus at Dusk / Cut 01", "status": "ready", "path": str(payload), "media_path": str(payload), "metadata": {}},
            modality="video",
            consent={},
            overrides={"privacy": "unlisted"},
        )
        assert result.status == "completed", [step.as_dict() for step in result.steps]
        assert [step.name for step in result.steps] == list(PREP_STEP_NAMES), [step.name for step in result.steps]
        assert all(step.status == "passed" for step in result.steps), [step.as_dict() for step in result.steps]
        bundle_path = Path(result.bundle_path or "")
        assert bundle_path.is_file()
        with zipfile.ZipFile(bundle_path) as archive:
            names = set(archive.namelist())
        assert names == {"metadata.json", "captions.srt", "thumbnail.jpg", "manifest.json"}, names
        metadata = json.loads((bundle_root / "prep_happy" / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["title"] == "Citrus at Dusk / Cut 01" and metadata["privacy"] == "unlisted"
        assert metadata["tags"] and "citrus" in metadata["tags"], metadata["tags"]
        assert result.manifest["checksum"] == "abc123"
        assert files["sealed"].name == "thumbnail.jpg"
        track(result.as_dict())

    # 9. Guard BLOCK stops the run before any media or file work beyond the gate.
    with tempfile.TemporaryDirectory(prefix="flujo-prep-") as directory:
        bundle_root = Path(directory)
        blocked_pipeline = PublishPrepPipeline(
            guard=FakeGuard(BlockDecision()),
            media_run=lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("media ran after guard block")),
            bundle_root=bundle_root,
        )
        result = blocked_pipeline.run(run_id="prep_blocked", artifact={"id": "artifact_2", "name": "x", "status": "ready", "path": "/nonexistent"}, modality="document")
        assert result.status == "blocked" and result.bundle_path is None
        assert len(result.steps) == 1 and result.steps[0].name == "guard_gate" and result.steps[0].status == "blocked"

    # 10. Thumbnail honestly skipped when the executor boundary is unavailable.
    with tempfile.TemporaryDirectory(prefix="flujo-prep-") as directory:
        bundle_root = Path(directory)
        payload = bundle_root / "scene.mp4"
        payload.write_bytes(b"fake-video-bytes")
        files2: dict[str, Path] = {}
        skip_pipeline = PublishPrepPipeline(
            guard=FakeGuard(PassDecision()),
            media_run=media_run_factory(MediaResult("blocked", "SECURE_EXECUTOR_UNAVAILABLE", "executor disabled"), MediaResult("completed", checksum="def456"), files2),
            bundle_root=bundle_root,
        )
        result = skip_pipeline.run(run_id="prep_skip", artifact={"id": "artifact_3", "name": "Night Market Cut", "status": "ready", "path": str(payload)}, modality="video")
        assert result.status == "completed", [step.as_dict() for step in result.steps]
        by_name = {step.name: step for step in result.steps}
        assert by_name["thumbnail"].status == "skipped" and "SECURE_EXECUTOR_UNAVAILABLE" in by_name["thumbnail"].detail
        assert by_name["render_finalize"].status == "passed"
        assert not (bundle_root / "prep_skip" / "thumbnail.jpg").exists()

    # 11. Guard decision can be handed in pre-evaluated (no double evaluation).
    with tempfile.TemporaryDirectory(prefix="flujo-prep-") as directory:
        bundle_root = Path(directory)
        guard = FakeGuard(PassDecision())
        single_pipeline = PublishPrepPipeline(guard=guard, media_run=lambda *a, **k: MediaResult("completed", checksum="x"), bundle_root=bundle_root)
        single_pipeline.run(run_id="prep_single", artifact={"id": "a", "name": "n", "status": "ready", "path": "/nonexistent"}, modality="document", guard_decision=PassDecision())
        assert guard.calls == 0

    # 12. The tracked secret must never appear in any tracked output.
    leaked = [entry for entry in SAFE_STRINGS if SECRET in entry]
    assert not leaked, f"secret leaked into tracked output: {leaked[:1]}"
    print("publishing: passed; adapter credential gating, upload mapping, prep pipeline steps, guard authority, honest media skips, registry extension, and secret isolation verified")


if __name__ == "__main__":
    main()
