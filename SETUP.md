# Flujo Studio - Test Setup (Windows)

This package contains the Flujo Studio source with eleven build milestones applied on top of the original `content creator` baseline. Everything below was tested on the build machine; Windows notes are included where paths/commands differ.

## What's new vs the original baseline

| Milestone | Change |
|---|---|
| M1 | Real **Meta provider adapter** (`muse-spark-1.3`) behind the Model Gateway. Simulator remains the default; the gateway goes live only when a credential is configured. Health now reports `provider_source: "simulator"` or `"meta-live"`. |
| M2 | All test suites green: Electron 17/17, frontend 21/21, backend 29/30 files (5 honest env-skips, Doctor intentionally blocked). Fixed real bugs: missing `uvicorn` dep, unbuilt `dist/`, broken smoke test, vitest flake. |
| M3 | Governor advisor consultations route through the **live gateway** when credentialed - real model opinions instead of simulator fixtures. Honesty states (CONFIDENT/UNCERTAIN/UNRESOLVED/BLOCKED) still enforced. |
| M4 | `chat` and `setup` frontend contracts migrated to REAL (both were already calling live backend endpoints). Chat now refetches the real assistant reply after sending instead of showing a canned message. |
| M11 | **NVIDIA is the default live provider** (`nvidia/nemotron-3-ultra-550b-a55b` via `https://integrate.api.nvidia.com/v1`). The production gateway pool is the labeled simulator plus credential-gated NVIDIA, Meta, OpenAI-compatible, and OpenCode Zen adapters; mocks are test doubles only. Creative Chat runs a real pipeline (gateway planning, model-derived analysis claims, real memory lookup, real Governor consult, derived genome/blueprint/compile plan, model-written reply) and uses the labeled simulator fallback (saying so) when no provider key is configured. Zen gained live `/models` discovery with the hardcoded 87-model list as baseline. |

## Prerequisites (Windows)

- **Python 3.12** - https://www.python.org/downloads/ (check "Add python.exe to PATH")
- **Node.js 24 LTS** - https://nodejs.org/
- **Git** - https://git-scm.com/

## 1. Backend

```powershell
cd flujo-build
py -m pip install -r backend\requirements.txt
py backend\run.py
```

The engine starts on `http://127.0.0.1:47821`. Check `http://127.0.0.1:47821/health` - you want `"database": true` and `"gateway": { "simulator_only": true, ... }` until a provider credential is added (step 3).

### Run the backend tests

```powershell
py backend\tests\nvidia_provider_test.py     # M11: NVIDIA adapter (mocked HTTP, no network) + default selection
py backend\tests\vertical_slice_test.py      # M11: chat pipeline honesty against an isolated backend
py backend\tests\publishing_captions_test.py # M11: prep caption hook (model vs deterministic fallback)
py backend\tests\media_playability_test.py   # M11: real ffprobe playability probe
py backend\tests\meta_provider_test.py      # M1: real adapter (mocked HTTP, no network)
py backend\tests\governor_live_consult_test.py  # M3: live-consult routing (mocked)
py backend\tests\governor_test.py
py backend\tests\gateway_provider_test.py
py backend\tests\contract_test.py            # needs FFmpeg binaries for the media assertion; that one skips honestly without them
py backend\tests\publishing_test.py         # M10: adapter credential gating + prep pipeline (mocked, no network)
py backend\tests\publishing_api_test.py     # M10: publish API contract against an isolated backend
```

Each test prints `<name>: passed` (or `SKIPPED (reason)`) on success.

## 2. Frontend + Electron shell

```powershell
cd flujo-build
npm install
npm run test:frontend     # vitest - 24 tests
npm run test:electron     # node --test - 17 tests
npm run dev               # Vite dev server for the renderer
```

In a second terminal, with the backend running:

```powershell
npm run electron:dev      # builds renderer + launches Electron against the live backend
```

Open **Creative Chat**, send a brief - it is queued as a real idempotent job and the thread refetches the recorded reply. The header chip reads **"Local backend"**.

## 3. Going live: real AI (optional, needs a key)

The default live provider is **NVIDIA** (`nvidia/nemotron-3-ultra-550b-a55b` served from `https://integrate.api.nvidia.com/v1`). The gateway stays on the labeled simulator until you store a provider credential:

1. Create a key at https://build.nvidia.com → sign in → **API keys** (keys look like `nvapi-...`).
2. Add it to the local credential store under the provider ref **`"nvidia"`** (Settings → Credentials in the app, or `POST /api/credentials` with `"provider": "nvidia"`). The `NvidiaModelProvider` resolves the opaque `credential_ref "nvidia"` at call time; the raw secret is never logged or persisted.
3. Restart the backend and check `/health` → `"gateway": { "simulator_only": false, "provider_source": "nvidia-live", "default_provider": "nvidia", ... }`.

From that moment, Governor advisor consultations, the Creative Chat pipeline (analysis claims, reply drafting), and publishing captions call the real model. **Watch your billing**: the Monitor's spend ceilings are your safety net; a halt can only be cleared by a named human.

Optional env overrides: `NVIDIA_BASE_URL` (default `https://integrate.api.nvidia.com/v1`), `NVIDIA_MODEL_ID` (default `nvidia/nemotron-3-ultra-550b-a55b`). Any custom base URL must use HTTPS and its host is allowlisted automatically.

### What works without a key

Everything still runs: nothing is silently simulated. With no provider credential configured, `/health` reports `"gateway": { "simulator_only": true, "provider_source": "simulator", "default_provider": "simulator", "reason": "no provider key configured", ... }`. Creative Chat runs the full pipeline shape (Guard, references, memory lookup, Governor council, genome, blueprint, compiler, media gates, QA, lineage) but analysis claims are skipped instead of invented, and the assistant reply says plainly that the model stages used the labeled simulator fallback. Governor consultations return explicitly labeled simulator opinions. Publishing captions fall back to the deterministic local generator and label the steps as such.

## 3b. Other providers (Meta, OpenAI-compatible, OpenCode Zen)

All three stay registered and selectable behind the same credential-gated pattern; NVIDIA wins default selection whenever it is credentialed.

- **Meta** (`meta`, model `muse-spark-1.3`): create a key at https://dev.meta.ai → API keys, store it under provider ref `"meta"`. Health then reports `"provider_source": "meta-live"`.
- **OpenAI-compatible** (`openai-compatible`): any OpenAI-style endpoint. Store the key under provider ref `"openai-compatible"` (fallback ref `"openai"`), and optionally set `OPENAI_COMPATIBLE_BASE_URL` / `OPENAI_COMPATIBLE_MODEL_ID`.
- **OpenCode Zen** (`opencode-zen`, 87 models across four endpoint styles): sign in at https://opencode.ai → add billing → create an API key, store it under provider ref `"opencode-zen"` (fallback ref `"opencode"`). Optional env overrides: `OPENCODE_ZEN_BASE_URL`, `OPENCODE_ZEN_MODEL_ID`. At boot the adapter attempts a best-effort live `/models` discovery (short timeout, failures swallowed); the hardcoded 87-model list remains the baseline catalog.

Free models (no key needed for the catalog; 14 ids): `big-pickle`,
`space-bunny-free`, `longcat-2.5-preview-free`, `step-5-preview-free`, `exo-free`,
`mimo-v2.6-flash-free`, `mimo-v2.5-free`, `ling-3.1-flash-free`,
`ling-3.0-flash-fin-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`,
`muse-spark-1.3-contributor-free`, `muse-spark-1.2-contributor-free`,
`jev-1.13-free`.

Honest note: keyless free-tier models are advertised in the catalog, but the gateway does **not** count them as live until a credential is configured - `simulator_only` stays `true` and consults use the labeled simulator fallback. Keyless direct calls are still attempted if you invoke a free model id explicitly, but OpenCode's server only honors keyless use from **inside the OpenCode CLI itself**: direct calls are rejected by OpenCode's server, and the adapter surfaces that rejection verbatim instead of working around it. For reliable live use, configure an API key.

## 4. Run the builder yourself (opencode + free Muse Spark 1.3)

The milestones in this package were built with [opencode](https://opencode.ai) using the **free** `muse-spark-1.3-contributor-free` model (no API key needed; note the contributor tier's data-may-train terms):

```powershell
# install
curl -fsSL https://opencode.ai/install | bash   # or: npm install -g opencode-ai
```

`~/.config/opencode/opencode.jsonc`:
```json
{
  "$schema": "https://opencode.ai/config.json",
  "model": "opencode/muse-spark-1.3-contributor-free"
}
```

Then, from this directory:
```powershell
opencode --auto
```

Give it a milestone brief the same way: what to build, which tests must pass, what not to touch, and "commit when done". The git history in this package (`git log`) shows the shape of briefs that worked.

## 5. Publishing prep (M10: prep only, no platform credentials required)

Flujo Studio can now **prepare** an artifact for release without any platform account connected. The flow is local and credential-free until the very last step:

```powershell
py backend\tests\publishing_test.py       # M10: adapter gating + prep pipeline (mocked, no network)
py backend\tests\publishing_api_test.py    # M10: API contract against an isolated backend
```

What M10 built:

- `POST /api/projects/{id}/publish/prep` takes a publishable artifact (status `created` or `ready`) and runs a six-step prep pipeline: **guard_gate -> metadata_package -> captions -> thumbnail -> render_finalize -> bundle**. The Guard publish gate runs first and stays authoritative (Constitution article 3); anything but PASS blocks the run with HTTP 423 and records the decision. When a provider credential is configured, tags and captions are model-generated through the gateway (M11) and the steps say which provider produced them; without a key, metadata and captions (.srt) are deterministic local files and the steps are labeled as the local fallback, never as model output. The thumbnail goes through the real media executor boundary and is honestly *skipped* when FFmpeg/the executor is unavailable, never faked.
- `GET /api/publish/adapters` lists platform adapters. Only one exists: **YouTube (Data API v3)** as the reference adapter, following the same conventions as the model gateway providers (credential resolver from the local vault, explicit "not configured" state, no raw secret in the constructor). It reports `configured: false` until you store a YouTube credential.
- `POST /api/publish/prep/{run_id}/upload` hands a completed prep run to the adapter. With no credential it fails closed with `ADAPTER_CREDENTIAL_UNAVAILABLE` (HTTP 423). Adding a second platform later is a register-only change: implement the `PublishAdapter` protocol and register it.
- The **Publishing** surface in the app now reads adapter status and the prep checklist from the local backend instead of showing a missing-backend stub.

To actually connect YouTube later (on Windows): store a credential with provider `youtube` in the Credentials surface, then retry the upload. No accounts are created by Flujo Studio.

## Still not done (honest list)

- **Signed installer** - needs a code-signing identity on your machine; the portable/unpacked build flow is in `package.json` (`npm run desktop:build`).
- **Legal** - FFmpeg LGPL redistribution review + trademark clearance for the "Flujo" name (a live product called Flujo exists).
- **Publishing platform credentials** - prep works locally; the YouTube adapter needs your API credential before any upload can run.
- Windows-only verification (packaged `.exe` launch, DPAPI paths, Job Object containment) must be re-run on your machine - the Linux CI here skips those honestly.
