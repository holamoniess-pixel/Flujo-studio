from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.media.engine import MEDIA_OPERATIONS, MediaEngine


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="flujo-media-policy-") as directory_name:
        root = Path(directory_name)
        references = root / "references"
        renders = root / "renders"
        references.mkdir()
        renders.mkdir()
        unicode_input = references / ("fixture-" + ("long-name-" * 12) + "-ünicode 日本語.mp4")
        unicode_input.write_bytes(b"deterministic media fixture")
        engine = MediaEngine(root)

        expected_aliases = {"probe", "duration", "frames", "audio_extraction", "audio_normalization", "subtitle_probe", "validate", "checksum"}
        assert expected_aliases.issubset(MEDIA_OPERATIONS)
        for operation in expected_aliases - {"checksum"}:
            output = None if operation in {"probe", "duration", "subtitle_probe", "validate"} else renders / f"{operation}.out"
            command, args = engine._command(operation, Path("ffmpeg.exe"), Path("ffprobe.exe"), [unicode_input], output, {})
            assert command.name in {"ffmpeg.exe", "ffprobe.exe"} and str(unicode_input) in args

        checksum = engine.run("checksum", [unicode_input], trace_id="trace-media-policy", project_id="project_northstar", options={"workflow_id": "workflow-test", "job_id": "job-test", "user_id": "user-test"})
        assert checksum.status == "completed" and checksum.checksum and checksum.provenance
        assert checksum.provenance["project"] == "project_northstar" and checksum.provenance["workflow"] == "workflow-test" and checksum.provenance["job"] == "job-test" and checksum.provenance["user"] == "user-test"

        empty = references / "empty.mp4"
        empty.touch()
        empty_result = engine.run("checksum", [empty])
        assert empty_result.status == "blocked" and empty_result.error_code == "MEDIA_INPUT_EMPTY"

        outside = root.parent / "outside.mp4"
        outside.write_bytes(b"outside")
        try:
            outside_result = engine.run("probe", [outside])
            assert outside_result.status == "blocked" and outside_result.error_code == "MEDIA_PATH_FORBIDDEN"
        finally:
            outside.unlink(missing_ok=True)

        protected = engine.run("thumbnail", [unicode_input], renders / "config.json")
        assert protected.status == "blocked" and protected.error_code == "MEDIA_OUTPUT_PROTECTED"
        disk_block = engine.run("thumbnail", [unicode_input], renders / "disk.mp4", options={"minimum_free_bytes": 1024})
        with patch("backend.app.media.engine.shutil.disk_usage", return_value=SimpleNamespace(free=0)):
            disk_block = engine.run("thumbnail", [unicode_input], renders / "disk.mp4", options={"minimum_free_bytes": 1024})
        assert disk_block.status == "blocked" and disk_block.error_code == "MEDIA_DISK_PRESSURE"
        timeout_block = engine.run("thumbnail", [unicode_input], renders / "timeout.mp4", timeout_seconds=0)
        assert timeout_block.status == "blocked" and timeout_block.error_code == "MEDIA_TIMEOUT_INVALID"
        limit_block = engine.run("thumbnail", [unicode_input], renders / "limit.mp4", options={"max_output_bytes": 0})
        assert limit_block.status == "blocked" and limit_block.error_code == "MEDIA_OUTPUT_LIMIT_INVALID"

        qa_file = renders / "qa.mp4"
        qa_file.write_bytes(b"output")
        qa = engine._artifact_qa("thumbnail", qa_file, 1024)
        assert qa["exists"] and qa["non_empty"] and qa["within_output_limit"] and qa["checksum"] and qa["expected_type"] == "image"
        qa_file.unlink()
        print("media_policy_tests_passed=aliases_paths_limits_zero_byte_qa_provenance")


if __name__ == "__main__":
    main()
