from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.media.binaries import MediaBinaryManager
from app.media.engine import MediaEngine


class NoProbeManager(MediaBinaryManager):
    def resolve_ffprobe(self):  # type: ignore[override]
        return None


def make_engine(tmp: Path, manager=None) -> MediaEngine:
    return MediaEngine(tmp, manager or MediaBinaryManager())


def main() -> None:
    ffprobe = Path("/usr/bin/ffprobe")
    if not ffprobe.is_file():
        print("media_playability: SKIPPED (no ffprobe on this host)")
        return

    with tempfile.TemporaryDirectory(prefix="flujo-playability-") as directory:
        tmp = Path(directory)

        # 1. Real playable file: generate 1s of test video with the system ffmpeg.
        clip = tmp / "clip.mp4"
        proc = subprocess.run(
            ["/usr/bin/ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=10", "-pix_fmt", "yuv420p", str(clip)],
            capture_output=True,
            timeout=60,
        )
        assert proc.returncode == 0 and clip.stat().st_size > 0, proc.stderr.decode(errors="replace")[-500:]

        os.environ["FLUJO_DEVELOPER_MEDIA_FALLBACK"] = "1"
        try:
            engine = make_engine(tmp)
            qa = engine._artifact_qa("transcode", clip, 64 * 1024 * 1024)
        finally:
            del os.environ["FLUJO_DEVELOPER_MEDIA_FALLBACK"]
        assert qa["exists"] is True and qa["non_empty"] is True, qa
        assert qa["playable"] is True, qa
        assert "video" in qa["playability_note"], qa

        # 2. Corrupt container with a media extension: playable is False, never None.
        fake = tmp / "fake.mp4"
        fake.write_bytes(b"\x00\x01\x02not-a-video" * 64)
        os.environ["FLUJO_DEVELOPER_MEDIA_FALLBACK"] = "1"
        try:
            engine = make_engine(tmp)
            qa = engine._artifact_qa("transcode", fake, 64 * 1024 * 1024)
        finally:
            del os.environ["FLUJO_DEVELOPER_MEDIA_FALLBACK"]
        assert qa["playable"] is False, qa
        assert qa["playability_note"], qa

        # 3. ffprobe unavailable: playable is None with an honest note (not a pass).
        engine = make_engine(tmp, NoProbeManager())
        qa = engine._artifact_qa("transcode", clip, 64 * 1024 * 1024)
        assert qa["playable"] is None, qa
        assert "not verified" in qa["playability_note"], qa

        # 4. Non-media outputs skip the probe without claiming anything.
        doc = tmp / "notes.txt"
        doc.write_text("hello", encoding="utf-8")
        qa = engine._artifact_qa("checksum", doc, 64 * 1024 * 1024)
        assert qa["playable"] is None, qa
        assert "only" in qa["playability_note"], qa

        # 5. Missing output: still exists=False, playable untouched.
        qa = engine._artifact_qa("transcode", tmp / "absent.mp4", 64 * 1024 * 1024)
        assert qa["exists"] is False and qa["playable"] is None, qa

    print("media_playability: passed")


if __name__ == "__main__":
    main()
