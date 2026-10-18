from __future__ import annotations

import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PREP_STEP_NAMES = (
    "guard_gate",
    "metadata_package",
    "captions",
    "thumbnail",
    "render_finalize",
    "bundle",
)

STEP_PASSED = "passed"
STEP_SKIPPED = "skipped"
STEP_BLOCKED = "blocked"
STEP_FAILED = "failed"

_WORD_PATTERN = re.compile(r"[a-z0-9]{3,}")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class PrepStepResult:
    name: str
    status: str
    detail: str
    artifacts: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "status": self.status, "detail": self.detail, "artifacts": list(self.artifacts)}


@dataclass
class PublishPrepResult:
    run_id: str
    status: str
    steps: list[PrepStepResult]
    bundle_path: str | None
    manifest: dict[str, Any]
    guard_decision: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "steps": [step.as_dict() for step in self.steps],
            "bundle_path": self.bundle_path,
            "manifest": self.manifest,
            "guard_status": getattr(self.guard_decision, "status", None),
            "guard_policy_version": getattr(self.guard_decision, "policy_version", None),
        }


def derive_tags(title: str, limit: int = 10) -> list[str]:
    """Deterministic tag derivation from the title; no model or network."""
    tokens = _WORD_PATTERN.findall(str(title or "").lower())
    seen: list[str] = []
    for token in tokens:
        if token not in seen:
            seen.append(token)
        if len(seen) >= limit:
            break
    return seen


def build_srt(title: str, description: str) -> str:
    """Deterministic caption file from title and description."""
    lines: list[str] = [str(title or "").strip()]
    for sentence in re.split(r"(?<=[.!?])\s+", str(description or "").strip()):
        if sentence:
            lines.append(sentence)
    return lines_to_srt(lines)


def lines_to_srt(lines: list[str]) -> str:
    """Lay caption lines out as timed SRT blocks (4 seconds each)."""

    def stamp(total_seconds: int) -> str:
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},000"

    chunks: list[str] = []
    for line in lines:
        line = str(line or "")
        while len(line) > 84:
            chunks.append(line[:84].rstrip())
            line = line[84:].lstrip()
        chunks.append(line)
    blocks = []
    cursor = 0
    for index, chunk in enumerate([part for part in chunks if part], start=1):
        blocks.append(f"{index}\n{stamp(cursor)} --> {stamp(cursor + 4)}\n{chunk}\n")
        cursor += 4
    return "\n".join(blocks).strip() + "\n"


class PublishPrepPipeline:
    """Local publishing preparation. Guard gate is step zero and authoritative.

    `guard` must expose `publish_gate(artifact_summary=, modality=, consent=)`
    returning a decision with `allowed`, `status`, `reason`, and
    `policy_version`. `media_run(operation, inputs, output, options)` must
    return a media-operation-shaped result with `status`, `error_code`,
    `message`, and `checksum` attributes (the real MediaEngine satisfies this).
    `caption_generate(title, description)` is an optional model-backed caption
    hook returning `{"tags": [...], "caption_lines": [...], "provider": ...,
    "model_id": ...}` or None. When it returns None (no provider key
    configured, or the model call failed), the pipeline falls back to the
    deterministic local functions and labels the steps as such.
    """

    def __init__(
        self,
        *,
        guard: Any,
        media_run: Callable[..., Any],
        bundle_root: Path,
        now: Callable[[], str] | None = None,
        caption_generate: Callable[[str, str], dict[str, Any] | None] | None = None,
    ) -> None:
        self.guard = guard
        self.media_run = media_run
        self.bundle_root = Path(bundle_root)
        self._now = now or _now_iso
        self._caption_generate = caption_generate

    def _generate_captions(self, title: str, description: str) -> dict[str, Any] | None:
        """Ask the model hook for tags/captions. Never raises; None means the
        deterministic fallback applies and must be labeled."""
        if self._caption_generate is None:
            return None
        try:
            generated = self._caption_generate(title, description)
        except Exception:
            return None
        if not isinstance(generated, dict):
            return None
        tags = [str(tag)[:40] for tag in generated.get("tags", []) if str(tag).strip()][:15]
        caption_lines = [str(line)[:200] for line in generated.get("caption_lines", []) if str(line).strip()][:40]
        if not tags and not caption_lines:
            return None
        return {
            "tags": tags,
            "caption_lines": caption_lines,
            "provider": str(generated.get("provider", "provider"))[:80],
            "model_id": str(generated.get("model_id", "unknown model"))[:200],
        }

    def run(
        self,
        *,
        run_id: str,
        artifact: dict[str, Any],
        modality: str = "document",
        consent: dict[str, Any] | None = None,
        overrides: dict[str, Any] | None = None,
        guard_decision: Any | None = None,
    ) -> PublishPrepResult:
        overrides = dict(overrides or {})
        consent = dict(consent or {})
        steps: list[PrepStepResult] = []
        workdir = self.bundle_root / run_id
        workdir.mkdir(parents=True, exist_ok=True)

        summary = json.dumps(
            {
                "artifact_id": artifact.get("id"),
                "name": artifact.get("name"),
                "status": artifact.get("status"),
                "metadata": artifact.get("metadata") or {},
            },
            ensure_ascii=False,
        )
        # The Guard publish gate is step zero and authoritative (Constitution
        # article 3). Callers may hand in a decision they already evaluated
        # and recorded so the gate is never evaluated twice for one run.
        decision = guard_decision if guard_decision is not None else self.guard.publish_gate(artifact_summary=summary, modality=modality, consent=consent)
        if not getattr(decision, "allowed", False):
            steps.append(PrepStepResult("guard_gate", STEP_BLOCKED, f"Guard publish gate stopped preparation: {getattr(decision, 'reason', 'not allowed')}."))
            return PublishPrepResult(run_id, "blocked", steps, None, {"run_id": run_id, "artifact_id": artifact.get("id")}, decision)
        steps.append(PrepStepResult("guard_gate", STEP_PASSED, f"Guard publish gate passed (policy {getattr(decision, 'policy_version', 'unknown')})."))

        title = str(overrides.get("title") or artifact.get("name") or "Untitled Flujo export").strip()[:100]
        description = str(overrides.get("description") or f"Prepared in Flujo Studio from {artifact.get('name') or 'an artifact'}.").strip()
        generated = self._generate_captions(title, description)
        override_tags = overrides.get("tags")
        if override_tags:
            tags = [str(tag)[:40] for tag in override_tags if str(tag).strip()][:15]
            tags_note = "caller-supplied tags"
        elif generated and generated["tags"]:
            tags = generated["tags"]
            tags_note = f"model-generated via {generated['provider']} ({generated['model_id']})"
        else:
            tags = derive_tags(title)
            tags_note = "deterministic local fallback (no provider key configured or model call failed); not model-generated"
        privacy = str(overrides.get("privacy") or "unlisted")
        if privacy not in {"unlisted", "private", "public"}:
            privacy = "unlisted"
        metadata = {
            "title": title,
            "description": description,
            "tags": tags[:15],
            "privacy": privacy,
            "artifact_id": artifact.get("id"),
            "artifact_name": artifact.get("name"),
            "artifact_status": artifact.get("status"),
            "modality": modality,
            "guard_status": getattr(decision, "status", None),
            "guard_policy_version": getattr(decision, "policy_version", None),
            "created_at": self._now(),
        }
        metadata_path = workdir / "metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8")
        steps.append(PrepStepResult("metadata_package", STEP_PASSED, f"Metadata package written with {len(metadata['tags'])} tags ({tags_note}).", [str(metadata_path)]))

        captions_path = workdir / "captions.srt"
        if generated and generated["caption_lines"]:
            captions_path.write_text(lines_to_srt(generated["caption_lines"]), encoding="utf-8")
            captions_note = f"Caption file generated by {generated['provider']} ({generated['model_id']}) from the title and description."
        else:
            captions_path.write_text(build_srt(title, description), encoding="utf-8")
            captions_note = "Caption file written from the local deterministic fallback (no provider key configured or model call failed); not model-generated."
        steps.append(PrepStepResult("captions", STEP_PASSED, captions_note, [str(captions_path)]))

        thumbnail_path = workdir / "thumbnail.jpg"
        media_input = self._media_input(artifact)
        if media_input is None:
            steps.append(PrepStepResult("thumbnail", STEP_SKIPPED, "No media input was associated with this artifact, so no thumbnail was produced."))
        else:
            result = self.media_run("thumbnail", [media_input], thumbnail_path, {"width": 1280})
            if getattr(result, "status", None) == "completed" and thumbnail_path.is_file():
                steps.append(PrepStepResult("thumbnail", STEP_PASSED, "Thumbnail rendered through the media executor boundary.", [str(thumbnail_path)]))
            else:
                steps.append(PrepStepResult("thumbnail", STEP_SKIPPED, f"Thumbnail unavailable and not faked: {getattr(result, 'error_code', 'UNKNOWN')}: {getattr(result, 'message', 'no detail')}."))
                thumbnail_path.unlink(missing_ok=True)

        seal_candidates = [thumbnail_path, captions_path, metadata_path]
        seal_file = next((path for path in seal_candidates if path.is_file()), None)
        if seal_file is None:
            steps.append(PrepStepResult("render_finalize", STEP_FAILED, "No payload file existed to finalize."))
            return PublishPrepResult(run_id, "blocked", steps, None, metadata, decision)
        seal = self.media_run("checksum", [seal_file], None, {})
        checksum = getattr(seal, "checksum", None)
        if getattr(seal, "status", None) != "completed" or not checksum:
            steps.append(PrepStepResult("render_finalize", STEP_FAILED, f"Finalize checksum failed: {getattr(seal, 'error_code', 'UNKNOWN')}: {getattr(seal, 'message', 'no detail')}."))
            return PublishPrepResult(run_id, "blocked", steps, None, metadata, decision)
        steps.append(PrepStepResult("render_finalize", STEP_PASSED, f"Payload finalized through the media boundary (checksum of {seal_file.name})."))

        manifest = dict(metadata)
        manifest.update({"run_id": run_id, "checksum": checksum, "sealed_file": seal_file.name, "steps": [step.as_dict() for step in steps]})
        manifest_path = workdir / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        bundle_path = self.bundle_root / f"{run_id}.zip"
        with zipfile.ZipFile(bundle_path, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in sorted(workdir.iterdir()):
                archive.write(item, item.name)
        steps.append(PrepStepResult("bundle", STEP_PASSED, f"Single export bundle written with {len(list(workdir.iterdir()))} prepared files.", [str(bundle_path)]))
        return PublishPrepResult(run_id, "completed", steps, str(bundle_path), manifest, decision)

    def _media_input(self, artifact: dict[str, Any]) -> Path | None:
        for key in ("media_path", "path"):
            candidate = artifact.get(key)
            if not candidate:
                continue
            path = Path(str(candidate))
            if path.is_file():
                return path
        return None
