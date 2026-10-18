from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PORT = 47826
TOKEN = "media-operation-matrix-token"
BASE = f"http://127.0.0.1:{PORT}"
FIXTURE = ROOT / "references" / "executor-fixture.mp4"


def request(path: str, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    req.add_header("content-type", "application/json")
    req.add_header("x-flujo-app-token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def wait_for_health() -> dict:
    for _ in range(100):
        try:
            status, result = request("/health")
            if status == 200:
                return result
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(0.2)
    raise AssertionError("media matrix backend did not become healthy")


def create_media_fixtures(ffmpeg_path: Path, audio_fixture: Path, subtitles: Path, embedded_fixture: Path) -> None:
    common = [str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y"]
    audio_command = common + [
        "-f", "lavfi", "-i", "color=c=blue:s=320x180:r=25:d=1",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=1",
        "-shortest", "-c:v", "mpeg4", "-c:a", "aac", "-pix_fmt", "yuv420p", str(audio_fixture),
    ]
    subprocess.run(audio_command, check=True, timeout=45, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    embedded_command = common + [
        "-i", str(audio_fixture), "-i", str(subtitles),
        "-map", "0:v:0", "-map", "0:a:0?", "-map", "1:0",
        "-c:v", "copy", "-c:a", "copy", "-c:s", "mov_text", str(embedded_fixture),
    ]
    subprocess.run(embedded_command, check=True, timeout=45, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)


def assert_terminal(name: str, status: int, result: dict, expected: set[str]) -> dict:
    assert status == 200, f"{name}: HTTP {status}: {result}"
    assert result.get("status") in expected, f"{name}: unexpected result {result}"
    if result["status"] == "completed":
        assert result.get("operation_id"), f"{name}: completed result lacks operation identity"
        assert result.get("trace_id") or name in {"corrupt_input", "missing_input", "path_escape", "operation_escape", "wrong_codec", "corrupt_binary", "invalid_output"}, f"{name}: completed result lacks trace correlation"
        if name not in {"probe", "detect_duration", "checksum", "unicode_input", "embedded_subtitle_probe"}:
            assert result.get("resource_usage", {}).get("executor") == "rust", f"{name}: media execution provenance is not Rust"
        outputs = [Path(item) for item in result.get("output_artifacts", [])]
        for output in outputs:
            assert output.is_file(), f"{name}: output missing: {output}"
            assert output.stat().st_size > 0, f"{name}: output empty: {output}"
        assert result.get("checksum") or name in {"probe", "detect_duration", "unicode_input", "embedded_subtitle_probe", "large_file_probe"}, f"{name}: completed output lacks checksum"
    return {"status": result.get("status"), "error_code": result.get("error_code"), "checksum": result.get("checksum")}


def main() -> None:
    if not FIXTURE.is_file():
        # Environment-limited, not a code bug: needs the deterministic MP4 fixture
        # plus Windows FFmpeg/executor binaries, none of which exist on Linux.
        print("media_matrix: SKIPPED (needs deterministic fixture references/executor-fixture.mp4 and Windows FFmpeg/executor binaries)")
        return
    assert FIXTURE.is_file(), "The deterministic MP4 fixture must exist."
    with tempfile.TemporaryDirectory(prefix="flujo-media-matrix-") as directory:
        data_root = Path(directory)
        render_root = ROOT / "renders"
        outputs = {
            "thumbnail": render_root / "matrix-thumbnail.png",
            "extract_frames": render_root / "matrix-frame.png",
            "trim": render_root / "matrix-trim.mp4",
            "concat": render_root / "matrix-concat.mp4",
            "crop": render_root / "matrix-crop.mp4",
            "resize": render_root / "matrix-resize.mp4",
            "transcode": render_root / "matrix-transcode.mp4",
            "extract_audio": render_root / "matrix-audio.wav",
            "normalize_audio": render_root / "matrix-normalized.wav",
            "add_subtitles": render_root / "matrix-subtitles.mp4",
        }
        concat_file = ROOT / "references" / "matrix-concat.txt"
        subtitles = ROOT / "references" / "matrix-subtitles.srt"
        concat_file.write_text(
            f"file '{FIXTURE.as_posix()}'\nfile '{FIXTURE.as_posix()}'\n",
            encoding="utf-8",
        )
        subtitles.write_text(
            "1\n00:00:00,000 --> 00:00:00,800\nFlujo matrix\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env.update(
            {
                "FLUJO_PROJECT_ROOT": str(ROOT),
                "FLUJO_DATA_DIR": str(data_root / ".flujo"),
                "FLUJO_MEDIA_DIR": str(ROOT / "resources" / "media"),
                "FLUJO_EXECUTOR_PATH": str(ROOT / "resources" / "executor" / "windows-x64" / "FlujoSecureExecutor.exe"),
                "FLUJO_EXECUTOR_TOKEN": "media-matrix-parent-token",
                "FLUJO_SECURE_EXECUTOR_ENABLED": "1",
                "FLUJO_PORT": str(PORT),
                "FLUJO_APP_TOKEN": TOKEN,
                "FLUJO_LOG_LEVEL": "warning",
            }
        )
        process = subprocess.Popen(
            [sys.executable, "-u", str(ROOT / "backend" / "run.py")],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        results: dict[str, dict] = {}
        try:
            health = wait_for_health()
            assert health["media"]["status"] == "healthy"
            audio_fixture = ROOT / "references" / "matrix-audio-bearing.mp4"
            embedded_subtitle_fixture = ROOT / "references" / "matrix-embedded-subtitles.mp4"
            large_fixture = ROOT / "references" / "matrix-large.mp4"
            ffmpeg_path = ROOT / "resources" / "media" / "windows-x64" / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
            assert ffmpeg_path.is_file()
            create_media_fixtures(ffmpeg_path, audio_fixture, subtitles, embedded_subtitle_fixture)
            subprocess.run([str(ffmpeg_path), "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=1280x720:rate=25", "-t", "8", "-c:v", "mpeg4", "-pix_fmt", "yuv420p", str(large_fixture)], check=True, timeout=90, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            valid_cases = [
                ("probe", {"operation": "probe", "inputs": [str(FIXTURE)]}),
                ("detect_duration", {"operation": "detect_duration", "inputs": [str(FIXTURE)]}),
                ("thumbnail", {"operation": "thumbnail", "inputs": [str(FIXTURE)], "output": str(outputs["thumbnail"]), "options": {"at": 0}}),
                ("extract_frames", {"operation": "extract_frames", "inputs": [str(FIXTURE)], "output": str(outputs["extract_frames"]), "options": {"fps": 1}}),
                ("trim", {"operation": "trim", "inputs": [str(FIXTURE)], "output": str(outputs["trim"]), "options": {"start": 0, "end": 0.5}}),
                ("concat", {"operation": "concat", "inputs": [str(FIXTURE)], "output": str(outputs["concat"]), "options": {"concat_file": str(concat_file)}}),
                ("crop", {"operation": "crop", "inputs": [str(FIXTURE)], "output": str(outputs["crop"]), "options": {"width": 160, "height": 90}}),
                ("resize", {"operation": "resize", "inputs": [str(FIXTURE)], "output": str(outputs["resize"]), "options": {"width": 160, "height": 90}}),
                ("transcode", {"operation": "transcode", "inputs": [str(FIXTURE)], "output": str(outputs["transcode"]), "options": {"video_codec": "mpeg4", "audio_codec": "pcm_s16le"}}),
                ("extract_audio", {"operation": "extract_audio", "inputs": [str(audio_fixture)], "output": str(outputs["extract_audio"])}),
                ("normalize_audio", {"operation": "normalize_audio", "inputs": [str(audio_fixture)], "output": str(outputs["normalize_audio"])}),
                ("add_subtitles", {"operation": "add_subtitles", "inputs": [str(audio_fixture)], "output": str(outputs["add_subtitles"]), "options": {"subtitles": str(subtitles)}}),
                ("embedded_subtitle_probe", {"operation": "probe", "inputs": [str(embedded_subtitle_fixture)]}),
                ("large_file_probe", {"operation": "probe", "inputs": [str(large_fixture)]}),
                ("checksum", {"operation": "checksum", "inputs": [str(FIXTURE)]}),
            ]
            for name, payload in valid_cases:
                status, result = request("/api/media/operation", "POST", {**payload, "project_id": "project_northstar", "trace_id": f"trace_matrix_{name}"})
                results[name] = assert_terminal(name, status, result, {"completed", "failed"})
                if name == "embedded_subtitle_probe":
                    assert result.get("status") == "completed", result
                    assert '"subtitle"' in result.get("stdout", ""), result
                if name == "large_file_probe":
                    assert result.get("status") == "completed", result
                    assert '"duration": "8.000000"' in result.get("stdout", ""), result
            assert results["extract_audio"]["status"] == "completed", results["extract_audio"]
            assert results["normalize_audio"]["status"] == "completed", results["normalize_audio"]

            corrupt = ROOT / "references" / "matrix-corrupt.mp4"
            corrupt.write_bytes(b"not-a-media-file")
            status, result = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(corrupt)], "project_id": "project_northstar"})
            results["corrupt_input"] = assert_terminal("corrupt_input", status, result, {"failed"})

            status, result = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(ROOT / "references" / "matrix-missing.mp4")], "project_id": "project_northstar"})
            assert status == 200 and result.get("error_code") == "MEDIA_INPUT_MISSING"
            results["missing_input"] = {"status": result.get("status"), "error_code": result.get("error_code")}

            status, result = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(Path(directory) / "outside.mp4")], "project_id": "project_northstar"})
            assert status == 200 and result.get("error_code") == "MEDIA_PATH_FORBIDDEN"
            results["path_escape"] = {"status": result.get("status"), "error_code": result.get("error_code")}

            status, result = request("/api/media/operation", "POST", {"operation": "not_allowlisted", "inputs": [str(FIXTURE)], "project_id": "project_northstar"})
            assert status == 200 and result.get("error_code") == "MEDIA_OPERATION_FORBIDDEN"
            results["operation_escape"] = {"status": result.get("status"), "error_code": result.get("error_code")}

            unicode_fixture = ROOT / "references" / "matrix-ünicode 名.mp4"
            unicode_fixture.write_bytes(FIXTURE.read_bytes())
            status, result = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(unicode_fixture)], "project_id": "project_northstar"})
            results["unicode_input"] = assert_terminal("unicode_input", status, result, {"completed"})
            unicode_fixture.unlink(missing_ok=True)

            status, result = request("/api/media/operation", "POST", {"operation": "transcode", "inputs": [str(FIXTURE)], "output": str(outputs["transcode"]), "options": {"video_codec": "not-a-codec", "audio_codec": "aac"}, "project_id": "project_northstar"})
            assert status == 200 and result.get("error_code") == "MEDIA_OPERATION_FAILED"
            results["wrong_codec"] = {"status": result.get("status"), "error_code": result.get("error_code")}

            corrupt_binary = ROOT / "resources" / "media" / "windows-x64" / "ffprobe.exe"
            original_binary = corrupt_binary.read_bytes()
            corrupt_binary.write_bytes(original_binary[: max(1, len(original_binary) // 2)])
            try:
                status, result = request("/api/media/operation", "POST", {"operation": "probe", "inputs": [str(FIXTURE)], "project_id": "project_northstar"})
                assert status == 200 and result.get("error_code") == "MEDIA_DEPENDENCY_CORRUPT"
                results["corrupt_binary"] = {"status": result.get("status"), "error_code": result.get("error_code")}
            finally:
                corrupt_binary.write_bytes(original_binary)

            output_directory = ROOT / "renders" / "matrix-output-directory"
            output_directory.mkdir(exist_ok=True)
            status, result = request("/api/media/operation", "POST", {"operation": "thumbnail", "inputs": [str(FIXTURE)], "output": str(output_directory), "project_id": "project_northstar"})
            assert status == 200 and result.get("status") in {"blocked", "failed"}
            results["invalid_output"] = {"status": result.get("status"), "error_code": result.get("error_code")}
            output_directory.rmdir()
            print(json.dumps({"media_matrix": "passed", "cases": results}, sort_keys=True))
        finally:
            for output in outputs.values():
                output.unlink(missing_ok=True)
            concat_file.unlink(missing_ok=True)
            subtitles.unlink(missing_ok=True)
            (ROOT / "references" / "matrix-corrupt.mp4").unlink(missing_ok=True)
            (ROOT / "references" / "matrix-audio-bearing.mp4").unlink(missing_ok=True)
            (ROOT / "references" / "matrix-embedded-subtitles.mp4").unlink(missing_ok=True)
            (ROOT / "references" / "matrix-large.mp4").unlink(missing_ok=True)
            if process.poll() is None:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/pid", str(process.pid), "/t", "/f"], check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    process.terminate()
            process.wait(timeout=20)


if __name__ == "__main__":
    main()

