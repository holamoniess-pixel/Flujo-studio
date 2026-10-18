# FFmpeg / FFprobe Notices

Flujo Studio now stages verified Windows x64 FFmpeg and FFprobe binaries for local engine verification. **This does not yet constitute external-release legal approval or production readiness.** Media execution remains gated by the compiled Rust secure executor, consent and Guard controls, and the final forensic/legal audit.

## Staged BtbN Windows x64 LGPL candidate

The staged archive is the exact GitHub release asset `ffmpeg-n9.0.1-6-g9d4ca21220-win64-lgpl-9.0.zip` from BtbN’s `autobuild-2026-08-24-13-10` release. The archive SHA-256 was verified against the release-published `checksums.sha256` asset:

```text
c7f6ae32a0c2e36b21091fb0216b905f09521b6ba7d5c9b3a205fbfd76061e73  ffmpeg-n9.0.1-6-g9d4ca21220-win64-lgpl-9.0.zip
```

The extracted package includes `LICENSE.txt` identifying the GNU Lesser General Public License, version 3. The package is staged as an LGPL static Windows x64 build. The exact executable records are:

| Binary | Reported version | SHA-256 | Staged location |
|---|---|---|---|
| `ffmpeg.exe` | `n9.0.1-6-g9d4ca21220-20260824` | `aa6cdeec90cbc60dce4652d3b15213b49f104b2b9e64d41cc0d5268eb8b0f30f` | `resources/media/windows-x64/ffmpeg.exe` |
| `ffprobe.exe` | `n9.0.1-6-g9d4ca21220-20260824` | `87cc45803ac1a0e976042a07ddc526a8c42a499235e85e715039a62d6d4e5693` | `resources/media/windows-x64/ffprobe.exe` |

The release source and asset URLs are [BtbN release metadata](https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/autobuild-2026-08-24-13-10), [the exact archive asset](https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-24-13-10/ffmpeg-n9.0.1-6-g9d4ca21220-win64-lgpl-9.0.zip), and [the published checksum asset](https://github.com/BtbN/FFmpeg-Builds/releases/download/autobuild-2026-08-24-13-10/checksums.sha256). The source build commit recorded by the release is `9d4ca21220`.

The resource directory contains the extracted `LICENSE.txt`, and the manifest records the archive checksum, executable checksums, version, source, LGPL declaration, and `external_release_approved: false`. Before any external installer is approved, Flujo must complete a legal review of the selected static/shared LGPL configuration, all bundled and optional component notices, the applicable corresponding-source obligations, and the final redistribution/source-offer record. A license file alone is not treated as legal sign-off.

## Rejected GPLv3 candidate

A Gyan.dev Windows x64 essentials archive was previously downloaded and verified locally. Its package README identified `ffmpeg-9.0.1-essentials_build-www.gyan.dev`, source commit `bf1b838f2a`, and **GPL v3** licensing, with GPL-sensitive libraries including `libx264` and `libx265`. That candidate is not shipped by Flujo and is retained only as rejected research evidence.

## Runtime policy

`resources/media/windows-x64/manifest.json` must match the binaries actually present. A missing or mismatched entry must produce `MEDIA_DEPENDENCY_CORRUPT`, not silent fallback. The Python media engine does not invoke FFmpeg directly. It requires the authenticated, manifest-verified Rust secure executor; the executor remains supervised by the Electron-launched local engine and is disabled unless `FLUJO_SECURE_EXECUTOR_ENABLED=1` is explicitly set by the verified local runtime.

## Authoritative references

- [FFmpeg official downloads](https://ffmpeg.org/download.html) — upstream release and Windows build-source reference.
- [FFmpeg legal information](https://www.ffmpeg.org/legal.html) — licensing obligations for the selected build.
- [BtbN FFmpeg-Builds](https://github.com/BtbN/FFmpeg-Builds) — Windows build distribution and source repository.
- [BtbN release metadata](https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/tags/autobuild-2026-08-24-13-10) — exact tag, assets, and published checksums.
- [Electron application distribution](https://www.electronjs.org/docs/latest/tutorial/application-distribution) — packaging prebuilt application resources.
