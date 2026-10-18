from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .binaries import MediaBinaryManager
from .executor import SecureExecutorClient, SecureExecutorUnavailable


_PLAYABLE_EXTENSIONS = frozenset({".mp4", ".mov", ".mkv", ".webm", ".avi", ".wav", ".mp3", ".m4a", ".flac", ".opus", ".png", ".jpg", ".jpeg", ".webp"})


MEDIA_OPERATIONS = {
    "probe",
    "validate",
    "subtitle_probe",
    "duration",
    "frames",
    "audio_extraction",
    "audio_normalization",
    "extract_audio",
    "extract_frames",
    "detect_duration",
    "trim",
    "concat",
    "crop",
    "resize",
    "normalize_audio",
    "add_subtitles",
    "transcode",
    "thumbnail",
    "checksum",
}


@dataclass(frozen=True)
class MediaOperationResult:
    operation_id: str
    operation: str
    status: str
    error_code: str | None
    message: str
    input_artifacts: list[str]
    output_artifacts: list[str]
    tool_version: str | None
    arguments: list[str]
    duration_ms: int
    exit_code: int | None
    stdout: str
    stderr: str
    resource_usage: dict[str, Any]
    trace_id: str | None = None
    checksum: str | None = None
    provenance: dict[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class MediaEngine:
    PROTECTED_OUTPUT_NAMES = {"manifest.json", "manifest.lock", "config.json", "settings.json", "flujo.sqlite3", "flujo.sqlite3-wal", "flujo.sqlite3-shm"}

    def __init__(self, project_root: Path, manager: MediaBinaryManager | None = None) -> None:
        self.project_root = project_root.resolve()
        self.manager = manager or MediaBinaryManager()
        self.executor = SecureExecutorClient(self.project_root)
        self.input_roots = tuple((self.project_root / name).resolve() for name in ("references", "assets", "generated", "renders", "exports"))
        self.output_roots = tuple((self.project_root / name).resolve() for name in ("generated", "renders", "exports"))

    def _operation_id(self, operation: str, inputs: list[Path], output: Path | None, options: dict[str, Any] | None = None) -> str:
        value = json.dumps({"operation": operation, "inputs": [str(item) for item in inputs], "output": str(output) if output else None, "options": options or {}}, sort_keys=True, default=str)
        return f"mediaop_{hashlib.sha256(value.encode()).hexdigest()[:24]}"

    def _resolve_project_path(self, value: str | Path) -> Path:
        path = Path(value)
        return (self.project_root / path if not path.is_absolute() else path).resolve()

    def _inside(self, path: Path, roots: tuple[Path, ...]) -> bool:
        return any(path == root or root in path.parents for root in roots)

    def _checksum(self, path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def _blocked(self, operation_id: str, operation: str, inputs: list[Path], message: str, code: str, trace_id: str | None) -> MediaOperationResult:
        return MediaOperationResult(operation_id, operation, "blocked", code, message, [str(item) for item in inputs], [], None, [], 0, None, "", "", {}, trace_id)

    def _bounded_int(self, options: dict[str, Any], key: str, minimum: int = 1, maximum: int = 16384) -> int:
        value = int(options[key])
        if value < minimum or value > maximum:
            raise ValueError(f"{key} is outside the permitted range")
        return value

    def _expected_type(self, operation: str, output: Path) -> str:
        extension = output.suffix.lower()
        if operation in {"thumbnail"}:
            return "image"
        if operation in {"extract_audio", "audio_extraction", "normalize_audio", "audio_normalization"}:
            return "audio"
        if operation in {"extract_frames", "frames"}:
            return "image-sequence"
        if operation in {"probe", "validate", "subtitle_probe", "duration", "detect_duration", "checksum"}:
            return "diagnostic"
        if extension in {".mp4", ".mov", ".mkv", ".webm", ".avi"}:
            return "video"
        if extension in {".wav", ".mp3", ".m4a", ".flac", ".opus"}:
            return "audio"
        if extension in {".png", ".jpg", ".jpeg", ".webp"}:
            return "image"
        return "media"

    def _playability_probe(self, output: Path) -> tuple[bool | None, str]:
        """Real playability probe: run ffprobe stream analysis on the output.

        Returns (playable, note). playable is None when ffprobe is unavailable
        (honest "not verified"), True when at least one audio/video stream with
        a positive duration or frame count is found, False otherwise. This is a
        local postcondition on the finished file, at the same trust level as
        the in-process checksum.
        """
        ffprobe = self.manager.resolve_ffprobe()
        if ffprobe is None:
            return None, "ffprobe is not available; playability was not verified"
        try:
            result = subprocess.run(
                [str(ffprobe), "-v", "error", "-show_format", "-show_streams", "-of", "json", str(output)],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return None, f"playability probe could not run: {exc}"
        if result.returncode != 0:
            detail = (result.stderr or "").strip()[:200]
            note = "ffprobe could not parse the output file"
            if detail:
                note += f": {detail}"
            return False, note
        try:
            decoded = json.loads(result.stdout)
        except ValueError:
            return False, "ffprobe returned unparsable output"
        if not isinstance(decoded, dict):
            return False, "ffprobe returned an unexpected document"
        format_info = decoded.get("format")
        format_duration = self._safe_float(format_info.get("duration")) if isinstance(format_info, dict) else None
        playable_kinds: set[str] = set()
        for stream in decoded.get("streams") or []:
            if not isinstance(stream, dict):
                continue
            codec_type = stream.get("codec_type")
            if codec_type not in ("video", "audio"):
                continue
            duration = self._safe_float(stream.get("duration")) or format_duration
            frames = self._safe_float(stream.get("nb_frames"))
            if (duration is not None and duration > 0) or (frames is not None and frames > 0):
                playable_kinds.add(codec_type)
        if playable_kinds:
            return True, f"ffprobe found playable stream(s): {', '.join(sorted(playable_kinds))}"
        return False, "ffprobe found no audio/video stream with a positive duration or frame count"

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            result = float(value)
        except (TypeError, ValueError):
            return None
        if result != result:  # NaN
            return None
        return result

    def _artifact_qa(self, operation: str, output: Path, max_output_bytes: int) -> dict[str, Any]:
        exists = output.is_file()
        size_bytes = output.stat().st_size if exists else 0
        non_empty = exists and size_bytes > 0
        playable: bool | None = None
        playability_note = "Playability is probed only for media file outputs."
        if non_empty and output.suffix.lower() in _PLAYABLE_EXTENSIONS:
            playable, playability_note = self._playability_probe(output)
        return {
            "exists": exists,
            "non_empty": non_empty,
            "size_bytes": size_bytes,
            "within_output_limit": size_bytes <= max_output_bytes,
            "expected_type": self._expected_type(operation, output),
            "extension": output.suffix.lower(),
            "checksum": self._checksum(output) if non_empty else None,
            "playable": playable,
            "playability_note": playability_note,
        }

    def _provenance(self, operation: str, inputs: list[Path], output: Path | None, arguments: list[str], tool_version: str | None, trace_id: str | None, project_id: str | None, options: dict[str, Any]) -> dict[str, Any]:
        return {
            "input": [str(item) for item in inputs],
            "operation": operation,
            "arguments": arguments,
            "tool_version": tool_version,
            "output": [str(output)] if output else [],
            "workflow": options.get("workflow_id"),
            "job": options.get("job_id"),
            "user": options.get("user_id"),
            "project": project_id,
            "trace_id": trace_id,
            "timestamp": time.time(),
        }

    def _command(self, operation: str, ffmpeg: Path, ffprobe: Path, inputs: list[Path], output: Path | None, options: dict[str, Any]) -> tuple[Path, list[str]]:
        if operation in {"probe", "validate", "subtitle_probe", "duration", "detect_duration"}:
            return ffprobe, ["-v", "error", "-show_format", "-show_streams", "-of", "json", str(inputs[0])]
        if output is None:
            raise ValueError("This media operation requires an output artifact")
        if operation in {"extract_audio", "audio_extraction"}:
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-vn", "-c:a", "pcm_s16le", str(output)]
        if operation in {"extract_frames", "frames"}:
            fps = float(options.get("fps", 1))
            if fps <= 0 or fps > 120:
                raise ValueError("fps is outside the permitted range")
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-vf", f"fps={fps:g}", str(output)]
        if operation == "trim":
            return ffmpeg, ["-y", "-ss", str(options.get("start", 0)), "-to", str(options.get("end", 1)), "-i", str(inputs[0]), "-c", "copy", str(output)]
        if operation == "concat":
            concat_file = self._resolve_project_path(options["concat_file"])
            if not self._inside(concat_file, self.input_roots) or not concat_file.is_file():
                raise ValueError("concat file is outside the permitted media roots")
            return ffmpeg, ["-y", "-f", "concat", "-safe", "0", "-i", str(concat_file), "-c", "copy", str(output)]
        if operation == "crop":
            width = self._bounded_int(options, "width")
            height = self._bounded_int(options, "height")
            x = self._bounded_int(options, "x", 0, 16384) if "x" in options else 0
            y = self._bounded_int(options, "y", 0, 16384) if "y" in options else 0
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-vf", f"crop={width}:{height}:{x}:{y}", str(output)]
        if operation == "resize":
            width = self._bounded_int(options, "width")
            height = self._bounded_int(options, "height")
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-vf", f"scale={width}:{height}", str(output)]
        if operation in {"normalize_audio", "audio_normalization"}:
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", str(output)]
        if operation in {"add_subtitles", "subtitle_burn"}:
            subtitles = self._resolve_project_path(options["subtitles"])
            if not self._inside(subtitles, self.input_roots) or not subtitles.is_file():
                raise ValueError("subtitle file is outside the permitted media roots")
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-vf", f"subtitles={subtitles}", str(output)]
        if operation == "thumbnail":
            return ffmpeg, ["-y", "-ss", str(options.get("at", 0)), "-i", str(inputs[0]), "-frames:v", "1", str(output)]
        if operation == "transcode":
            codec = str(options.get("video_codec", "libx264"))
            audio_codec = str(options.get("audio_codec", "aac"))
            if codec not in {"mpeg4", "libx264", "libx265", "libvpx-vp9", "h264_nvenc"} or audio_codec not in {"aac", "libopus", "pcm_s16le"}:
                raise ValueError("codec is not allowlisted")
            return ffmpeg, ["-y", "-i", str(inputs[0]), "-c:v", codec, "-c:a", audio_codec, str(output)]
        raise ValueError(f"Unsupported media operation: {operation}")

    def run(self, operation: str, inputs: list[Path], output: Path | None = None, options: dict[str, Any] | None = None, trace_id: str | None = None, timeout_seconds: int = 300, project_id: str | None = None) -> MediaOperationResult:
        options = options or {}
        started = time.perf_counter()
        resolved_inputs = [self._resolve_project_path(item) for item in inputs]
        resolved_output = self._resolve_project_path(output) if output else None
        operation_id = self._operation_id(operation, resolved_inputs, resolved_output, options)
        if timeout_seconds < 1 or timeout_seconds > 1800:
            return self._blocked(operation_id, operation, resolved_inputs, "The media timeout is outside the permitted range.", "MEDIA_TIMEOUT_INVALID", trace_id)
        if resolved_output:
            try:
                minimum_free_bytes = int(options.get("minimum_free_bytes", 64 * 1024 * 1024))
                if minimum_free_bytes < 0 or minimum_free_bytes > 16 * 1024 * 1024 * 1024:
                    raise ValueError("minimum_free_bytes is outside the permitted range")
                if shutil.disk_usage(resolved_output.parent).free < minimum_free_bytes:
                    return self._blocked(operation_id, operation, resolved_inputs, "Insufficient free disk space for the requested media output.", "MEDIA_DISK_PRESSURE", trace_id)
            except (OSError, ValueError) as exc:
                return self._blocked(operation_id, operation, resolved_inputs, f"Media resource preflight failed safely: {exc}", "MEDIA_RESOURCE_PREFLIGHT_FAILED", trace_id)
        if operation not in MEDIA_OPERATIONS:
            return self._blocked(operation_id, operation, resolved_inputs, "The media operation is not allowlisted.", "MEDIA_OPERATION_FORBIDDEN", trace_id)
        if not resolved_inputs or any(not self._inside(item, self.input_roots) for item in resolved_inputs):
            return self._blocked(operation_id, operation, resolved_inputs, "Media input is outside the permitted project media roots.", "MEDIA_PATH_FORBIDDEN", trace_id)
        if any(not item.is_file() for item in resolved_inputs):
            return self._blocked(operation_id, operation, resolved_inputs, "A required media input does not exist.", "MEDIA_INPUT_MISSING", trace_id)
        if any(item.stat().st_size == 0 for item in resolved_inputs):
            return self._blocked(operation_id, operation, resolved_inputs, "A required media input is empty.", "MEDIA_INPUT_EMPTY", trace_id)
        if resolved_output and not self._inside(resolved_output, self.output_roots):
            return self._blocked(operation_id, operation, resolved_inputs, "Media output is outside the permitted generated-media roots.", "MEDIA_PATH_FORBIDDEN", trace_id)
        if resolved_output and resolved_output.name.lower() in self.PROTECTED_OUTPUT_NAMES:
            return self._blocked(operation_id, operation, resolved_inputs, "The requested media output targets a protected Flujo file.", "MEDIA_OUTPUT_PROTECTED", trace_id)
        if resolved_output and resolved_output.exists() and resolved_output.is_dir():
            return self._blocked(operation_id, operation, resolved_inputs, "Media output must be a file path, not an existing directory.", "MEDIA_OUTPUT_INVALID", trace_id)
        max_output_bytes = 2 * 1024 * 1024 * 1024
        output_existed_before = bool(resolved_output and resolved_output.exists())
        if resolved_output:
            try:
                max_output_bytes = int(options.get("max_output_bytes", max_output_bytes))
                if max_output_bytes < 1 or max_output_bytes > 4 * 1024 * 1024 * 1024:
                    raise ValueError("max_output_bytes is outside the permitted range")
            except (TypeError, ValueError) as exc:
                return self._blocked(operation_id, operation, resolved_inputs, f"Media output limit is invalid: {exc}", "MEDIA_OUTPUT_LIMIT_INVALID", trace_id)
        if operation == "checksum":
            started = time.perf_counter()
            checksum = self._checksum(resolved_inputs[0])
            return MediaOperationResult(operation_id, operation, "completed", None, "Checksum calculated.", [str(item) for item in resolved_inputs], [], None, [], int((time.perf_counter() - started) * 1000), 0, "", "", {"input_size_bytes": resolved_inputs[0].stat().st_size, "input_checksum": checksum}, trace_id, checksum, self._provenance(operation, resolved_inputs, None, [], None, trace_id, project_id, options))
        if not self.executor.token or os.environ.get("FLUJO_SECURE_EXECUTOR_ENABLED", "0") != "1":
            return self._blocked(operation_id, operation, resolved_inputs, "The secure executor boundary is not enabled; host-level media execution remains disabled.", "SECURE_EXECUTOR_UNAVAILABLE", trace_id)
        health = self.manager.health_check()
        if health["status"] == "corrupt":
            return self._blocked(operation_id, operation, resolved_inputs, "A media binary failed verification.", "MEDIA_DEPENDENCY_CORRUPT", trace_id)
        if health["status"] != "healthy":
            return self._blocked(operation_id, operation, resolved_inputs, "FFmpeg and FFprobe are not both available and verified.", "MEDIA_DEPENDENCY_MISSING", trace_id)
        try:
            command_path, args = self._command(operation, Path(health["ffmpeg"]["path"]), Path(health["ffprobe"]["path"]), resolved_inputs, resolved_output, options)
            result = self.executor.execute(
                operation_id=operation_id,
                operation=operation,
                trace_id=trace_id,
                project_id=project_id,
                binary=command_path,
                args=args,
                input_paths=resolved_inputs,
                output_paths=[resolved_output] if resolved_output else [],
                allowed_roots=list(self.input_roots + self.output_roots),
                binary_roots=[self.manager.platform_root],
                timeout_seconds=timeout_seconds,
            )
            status = result.status if result.status in {"completed", "failed", "blocked"} else "failed"
            checksum = None
            error_code = result.error_code
            message = result.message
            qa = None
            if status == "completed" and resolved_output:
                qa = self._artifact_qa(operation, resolved_output, max_output_bytes)
                if not qa["exists"] or not qa["non_empty"]:
                    status = "failed"
                    error_code = "MEDIA_OUTPUT_MISSING"
                    message = "The executor completed, but the declared media output was missing or empty."
                elif qa["playable"] is False:
                    status = "failed"
                    error_code = "MEDIA_OUTPUT_UNPLAYABLE"
                    message = f"The executor completed, but the media output failed the playability probe: {qa['playability_note']}"
                elif not qa["within_output_limit"]:
                    status = "blocked"
                    error_code = "MEDIA_OUTPUT_TOO_LARGE"
                    message = "The media output exceeded the configured artifact-size limit."
                    if not output_existed_before:
                        resolved_output.unlink(missing_ok=True)
                else:
                    checksum = qa["checksum"]
            tool_version = health["ffprobe"]["version"] if operation in {"probe", "validate", "subtitle_probe", "duration", "detect_duration"} else health["ffmpeg"]["version"]
            total_duration_ms = int((time.perf_counter() - started) * 1000)
            disk_free_bytes = shutil.disk_usage(resolved_output.parent).free if resolved_output else None
            return MediaOperationResult(
                operation_id=operation_id,
                operation=operation,
                status=status,
                error_code=error_code,
                message=message,
                input_artifacts=[str(item) for item in resolved_inputs],
                output_artifacts=[str(resolved_output)] if resolved_output else [],
                tool_version=tool_version,
                arguments=args,
                duration_ms=total_duration_ms,
                exit_code=result.exit_code,
                stdout=result.stdout,
                stderr=result.stderr,
                resource_usage={"executor": "rust", "executor_operation_id": result.operation_id, "disk_free_bytes": disk_free_bytes, "input_size_bytes": sum(item.stat().st_size for item in resolved_inputs), "output_size_bytes": qa["size_bytes"] if qa else None, "qa": qa},
                trace_id=trace_id,
                checksum=checksum,
                provenance=self._provenance(operation, resolved_inputs, resolved_output, args, tool_version, trace_id, project_id, options),
            )
        except SecureExecutorUnavailable as exc:
            return self._blocked(operation_id, operation, resolved_inputs, str(exc), "SECURE_EXECUTOR_UNAVAILABLE", trace_id)
        except (OSError, KeyError, ValueError) as exc:
            return self._blocked(operation_id, operation, resolved_inputs, f"Media operation could not start safely: {exc}", "MEDIA_OPERATION_FAILED", trace_id)
