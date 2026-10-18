# Flujo Studio - Forensic Audit of Simulated / Faked Components (M11 Phase 1)

**Repo:** `~/workspace/flujo-build` · **Commit:** `02dd758`
**Date:** 2026-10-12 · **Scope:** read-only audit of every simulator, mock, fixture, stub,
fake, placeholder, canned-data return, and TODO/FIXME/XXX/HACK marker.
**Goal:** enumerate everything still faked so M11 can "de-simulate" and make
NVIDIA the default live AI provider.

Method: case-insensitive grep over `simulat, mock, fixture, stub, fake, placeholder,
canned, dummy, hardcoded, lorem, example.com, not.?implemented, not_implemented,
TODO, FIXME, XXX, HACK` in `backend/app`, `src`, `electron` (101 source files),
plus manual inspection of provider selection, governor consult routing, media
fallbacks, doctor checks, frontend route contracts, and the Electron shell.
No test-only files (`*.test.*`) counted as product fakes. No network calls made.

### The headline, in one paragraph

The entire AI brain is a simulator. `ModelGateway()` (backend/app/main.py:131) boots
with only `MockModelProvider` + `MockSecondaryProvider`; the real adapters for Meta,
OpenAI-compatible, and OpenCode Zen are registered but advertise zero live models
until a credential is stored, and **no NVIDIA adapter exists anywhere in the backend**
even though the frontend displays NVIDIA models (Nemotron 3 Ultra, Kimi K2.6,
Cosmos Reason 2, Qwen Image) as its headline catalog. Worse than the gateway itself:
the main user-visible flow - `run_vertical_slice` (backend/app/main.py:791) behind
Creative Chat - ignores what the user typed and writes **hardcoded claims, memory,
Governor decisions, genomes, blueprints, compile plans, and the assistant reply**
into the database on every run. A user chatting with Flujo is reading fiction,
not AI output.

---

## 1. Gateway (AI provider layer)

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| G1 | backend/app/gateway/contracts.py:91-117 | simulator | `MockModelProvider` answers every capability with canned hash-derived text: `"simulated text_generation response <hash>"`, `"simulated transcript …"`, `"simulated-image-plan"`, a fixed judge score of **0.82** with empty findings, and synthetic 12-dim embeddings. Every Governor consult and chat plan built on this is fabricated. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY via a new NVIDIA provider adapter (M11 goal); alternately the existing Meta/OpenAI/Zen credential gates | MAKE REAL (build NVIDIA adapter; delete mocks from the production provider pool; keep mocks only as test doubles) |
| G2 | backend/app/gateway/contracts.py:120-121 | mock | `MockSecondaryProvider` - a second mock with the same fake outputs and hand-tuned fake scores (quality 0.78, novelty 0.72, cost 0.88…) used to make gateway "diversity" and fallback logic look multi-provider. | LOCAL CODE (remove) | MAKE REAL (remove from default pool) |
| G3 | backend/app/gateway/contracts.py:194 | simulator | `ModelGateway.__init__` defaults to `[MockModelProvider(), MockSecondaryProvider()]`, so any `ModelGateway()` - including the app's global one before credential checks - is mock-only. | LOCAL CODE (default to credential-gated live providers) | MAKE REAL |
| G4 | backend/app/main.py:131-153, 192-193, 236-237 | simulator | The global `GATEWAY = ModelGateway()` starts mock-only; real adapters (Meta, OpenAI-compatible, OpenCode Zen) are appended but each is **simulator-by-default**: with no stored credential they advertise zero models, so `gateway.health()` reports `simulator_only: true` and every call falls back to the mocks. | EXTERNAL KEY/SERVICE: stored credential for meta / openai(-compatible) / opencode-zen; for M11 the new NVIDIA adapter | KEY-GATE |
| G5 | backend/app/gateway/meta_provider.py:84, :123 | simulator | Meta adapter: no credential → empty catalog and `GATEWAY_AUTH_FAILED` ("staying on the simulator"). Honest fail-closed, but the adapter is dormant/dead code in practice (M9 retired Meta as default). | EXTERNAL KEY/SERVICE: Meta credential (likely never coming) | KEY-GATE (or remove; Meta is superseded by the NVIDIA plan) |
| G6 | backend/app/gateway/openai_provider.py:95, :134 | simulator | Same simulator-by-default pattern for the OpenAI-compatible adapter (base URL/model from `OPENAI_COMPATIBLE_BASE_URL` / `OPENAI_COMPATIBLE_MODEL_ID`). | EXTERNAL KEY/SERVICE: OpenAI-compatible API key | KEY-GATE |
| G7 | backend/app/gateway/zen_provider.py:273-277 | fixture | OpenCode Zen adapter's catalog is a **hardcoded** `KNOWN_MODEL_IDS` (87 ids) / `FREE_MODEL_IDS` list, not a live `/models` discovery; keyless mode still "attempts" free ids and paid models stay dormant. The HTTP invoke path itself is real code. | EXTERNAL KEY/SERVICE: OPENCODE_API_KEY (paid); LOCAL CODE for live catalog discovery | MAKE REAL (live `/models` discovery) / KEY-GATE |
| G8 | backend/app/gateway/ *(whole dir)* | todo | **No NVIDIA provider adapter exists.** M11's stated goal ("NVIDIA as the default AI provider") has zero backend code; the frontend (src/RefinedSurfaces.tsx:41) pretends NVIDIA models are the primary catalog. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY; LOCAL CODE: new provider adapter module | MAKE REAL |
| G9 | src/RefinedSurfaces.tsx:41 | fixture | Settings → Models hardcodes provider rows (NVIDIA: Nemotron 3 Ultra, Kimi K2.6, Cosmos Reason 2, Qwen Image; OpenAI: FLUX.2…) all labeled `Simulator-only` - a static list unrelated to the actual gateway catalog. | LOCAL CODE (bind to `/api/models`) | MAKE REAL |
| G10 | src/RefinedSurfaces.tsx:46 | fixture | "Fetch all models" maps any backend health into static `Simulator-only` / `Unavailable` labels and announces *"Model Gateway catalog loaded from the local simulator"* - it does not surface the real provider state. | LOCAL CODE | MAKE REAL |
| G11 | src/RefinedSurfaces.tsx:49-51 | fixture | API Usage tab shows fixture numbers ($2.52 cost, 1,284 requests, "Not a real charge", "Local estimate", "Fixture usage grouped by provider"); Provider "Test" button announces *"test remained simulator-only; no external provider call was made"*. | LOCAL CODE (real usage accounting) | MAKE REAL (usage ledger) |
| G12 | src/LiveSurfaces.tsx:38 | simulator | Live Model Registry banner admits *"The current catalog is intentionally simulator-only"* and *"Real NVIDIA/OpenAI/Google adapters remain disabled"* - honest labeling of G1-G4, but the surface's content is still the mock catalog. | (same as G1) | MAKE REAL (via G1/G8) |

## 2. Governor (advisor society)

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| GV1 | backend/app/governor/live_consult.py:30-43 | simulator | `simulator_consult()` returns a canned answer for every advisor: `"simulated {role} recommendation for the requested task"`, fixed confidence **0.76**, and `simulator-evidence:<hash>` as its "evidence". Any Governor decision made in simulator mode is fiction. (Frontend faces: F15, F16 below.) | EXTERNAL KEY/SERVICE: any live provider credential (NVIDIA per M11) | KEY-GATE (delete simulator path once NVIDIA is live; until then keep explicitly labeled) |
| GV2 | backend/app/governor/live_consult.py:112-116 | simulator | `gateway_consult()` auto-routes to the simulator whenever `gateway.health()["simulator_only"]` is true - which is the permanent steady state today (see G4). The live prompt path (lines 124-163) is real code but unreachable without a credential. | (same as GV1) | KEY-GATE |

## 3. Creative Chat vertical slice (the biggest fake in the product)

`run_vertical_slice` (backend/app/main.py:791) is what Creative Chat actually runs when
a user sends a brief. The pipeline shape (Guard → gateway → analysis → memory → council
→ genome → blueprint → compiler → permissions → media → QA → lineage) is real code with
real SQLite persistence - but **every stage's content is hardcoded**; the user's message
only reaches the mock gateway (G1). Items are one logical fake split per stage for M11
work-item granularity:

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| C1 | backend/app/main.py:821-831 | fake | "AdaptiveAnalysis" inserts 4 fixed claims ("Gesture-first reveal" 0.91, "Warm directional lighting" 0.87, "Direct-response opening hook" 0.84, "Short tactile pacing" 0.79) for *any* brief, stamped `model_id="local-deterministic"`. The Evidence Graph fills with invented findings. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY (LLM analysis) or LOCAL CODE honest "not analyzed" state | MAKE REAL |
| C2 | backend/app/main.py:832-834 | fake | "MemoryLookupWithProvenance" writes a fixed memory entry ("Warm cinematic language… 0.88") every run instead of querying memory. | LOCAL CODE (real memory query) | MAKE REAL |
| C3 | backend/app/main.py:836-838 | fake | "GovernorCouncil" persists a fixed decision - *"Keep tactile opening; protect identity continuity."*, confidence 0.86, `advisor_count: 3` - without consulting any advisor. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY via `gateway_consult` (GV2) | MAKE REAL |
| C4 | backend/app/main.py:839-841 | fake | "CreativeGenome" writes a fixed genome ("Cinematic Food / Tactile Response" v0.1, hooks/visual/editing/cta patterns) regardless of input. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY | MAKE REAL |
| C5 | backend/app/main.py:842-845 | fake | "CreativeBlueprint" inserts a fixed one-scene blueprint ("Citrus at Dusk / Scene 01", "tactile opening"). | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY | MAKE REAL |
| C6 | backend/app/main.py:846-848 | fake | "CreativeCompiler" stores a fixed compile plan (`render_remotion`, "Creative Director"/"Quality Judge"). | LOCAL CODE (real compiler from blueprint) + EXTERNAL KEY where LLM needed | MAKE REAL |
| C7 | backend/app/main.py:893 | fake | The assistant's chat reply is a fixed string (*"The local vertical slice reached Artifact Lineage…"*) - the user never receives a response to what they actually wrote. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY | MAKE REAL |
| - | backend/app/main.py:811-819 | (honest) | Gateway call + `simulator_only` audit logging are real and honestly labeled - not faked; listed so M11 keeps them. | - | KEEP |

## 4. Media engine

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| M1 | backend/app/media/engine.py (`_artifact_qa`) | placeholder | Artifact QA returns `"playable": None` with the note *"Playability requires the approved executor QA probe"* - existence/size/checksum are real, but playability (can this file actually play?) is never verified. | LOCAL CODE (executor-side QA probe, e.g. ffprobe stream check on outputs) | MAKE REAL |
| M2 | backend/app/publishing/prep.py:65, :77 | placeholder | `derive_tags()` (title word-split) and `build_srt()` (title+description chopped into 4-second SRT blocks) are deterministic stand-ins for real caption generation. Honestly docstringed ("no model or network") but user-visible as if captions were generated. | EXTERNAL KEY/SERVICE: NVIDIA_API_KEY (or local transcription) for real captions | MAKE REAL |
| - | backend/app/media/binaries.py | (honest) | `MediaBinaryManager`: missing/corrupt FFmpeg+FFprobe → **blocked, not simulated** (main.py:855, :861 make this explicit to users). `FLUJO_DEVELOPER_MEDIA_FALLBACK=1` PATH fallback is an explicitly labeled dev escape hatch. | - | KEEP AS LABELED FALLBACK |

## 5. System Doctor

Most checks are real local probes. The fakes:

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| D1 | backend/app/doctor/checks.py (`_engine_check`, ~line 79) | fake | Always returns `"ok"` / *"Backend engine is running (this diagnostic executed in-process)"* - self-attestation, not a probe. A wedged engine that can still serve the doctor endpoint reports healthy. | LOCAL CODE (real liveness signals: worker heartbeat, schema sanity, event-loop lag) | MAKE REAL |
| D2 | backend/app/doctor/checks.py (`_ports_check`) | stub | Hardcodes `"ok"` / *"Port 47821 is the configured local backend port served by this process"* - never actually checks port occupancy or reachability. | LOCAL CODE (socket bind/probe) | MAKE REAL |
| D3 | backend/app/doctor/checks.py (`_processes_check`) | stub | Static `"unknown"` / *"verifiable on packaged Windows builds only"* - always unknown on every host, forever. | LOCAL CODE (supervised-process listing on Windows; on Linux, the actual child-process table) | MAKE REAL or KEEP AS LABELED FALLBACK |
| D4 | backend/app/doctor/checks.py (`_electron_check`) | stub | Static `"unknown"` / *"Reported via frontend heartbeat"* - the backend has the heartbeat data path available but never consults it. | LOCAL CODE (read last frontend heartbeat timestamp) | MAKE REAL |
| - | backend/app/doctor/checks.py (`_gateway_check`) | (honest) | Reports simulator mode honestly (*"responding in simulator mode (N local models; no live provider credential configured)"*) - not faked. | - | KEEP |

## 6. Publishing

| # | file:line | CAT | What it fakes (user-visible behavior) | Real requires | Disposition |
|---|-----------|-----|----------------------------------------|---------------|-------------|
| P1 | backend/app/publishing/contracts.py:33-85 | mock | `MockPublishAdapter` ("Mock Video Platform") returns a fake upload receipt `mock-upload-<run_id>` with `"simulator": True`. **Not registered at runtime** (only `YouTubePublishAdapter` is registered, main.py:281) - pure test double, but importable and one refactor away from being wired in. | LOCAL CODE (remove from `__all__` or gate behind a test-only flag) | KEEP AS LABELED FALLBACK (test-only) |
| P2 | backend/app/publishing/youtube_adapter.py:164 | (real, key-gated) | Real YouTube upload path; credential-gated, fails closed without a stored credential. Not faked. | EXTERNAL KEY/SERVICE: YouTube OAuth / API credential | KEY-GATE |
| - | backend/app/publishing/prep.py:196 | (honest) | Thumbnail step explicitly refuses to fake: *"Thumbnail unavailable and not faked"* - the honest pattern M11 should copy. | - | KEEP |

## 7. Frontend surfaces (route contracts)

Per src/surfaceContracts.ts:13-15, 19 - 12 routes are `REAL` (backend-wired), 1 is
`CONTRACT_STUB` (settings), 4 are `MISSING_BACKEND` (automation, calendar,
sourceMonitor, socialAccounts), and **41 routes are `DEMO_ONLY`** - hardcoded demo
content that must stay visibly labeled. Each row below is one fixture surface; the
generic fake is "renders hardcoded demo data instead of backend state". Disposition
is MAKE REAL per route (bind to the SQLite-backed APIs that already exist for most
of them) unless noted.

| # | route | file:line | CAT | Notable fake detail |
|---|-------|-----------|-----|---------------------|
| F1 | command | src/App.tsx:51 | fixture | Hero labeled `DEMO STATE`; `start()` (src/App.tsx:42) runs a `setTimeout` job-stage animation announcing *"No external services were called."* |
| F2 | projects | src/MissingSurfaces.tsx:16 | fixture | Hardcoded "Northstar Food Stories / Studio Experiments / Field Notes" cards |
| F3 | campaigns | src/MissingSurfaces.tsx:19 | fixture | Hardcoded campaign rows |
| F4 | library | src/MissingSurfaces.tsx:21 | fixture | Fake KPIs (42 artifacts, 18 generated) and artifact cards |
| F5 | intake | src/App.tsx:56 | fixture | Banner: *"Add reference creates a simulated record"*; "Metadata only in demo mode" |
| F6 | analysis | src/App.tsx:58 | fixture | Hardcoded findings |
| F7 | evidence | src/App.tsx:60 | fixture | Hardcoded evidence graph |
| F8 | entities | src/MissingSurfaces.tsx:23 | fixture | Hardcoded entities |
| F9 | genome | src/App.tsx:61 | fixture | Hardcoded genome patterns |
| F10 | genomeCompare | src/MissingSurfaces.tsx:41 | fixture | Hardcoded comparison |
| F11 | blueprint | src/App.tsx:64 | fixture | Hardcoded "Citrus at Dusk - 30s Cut" scenes |
| F12 | compiler | src/App.tsx:65 | fixture | *"Mock adapter · local only"*, *"generated fixture"*, *"Gateway status: Disconnected by design"* |
| F13 | qa | src/App.tsx:70 | fixture | Demo scorecard ("82 demo"), *"Run verification"* only re-announces demo state |
| F14 | reviewQueue | src/MissingSurfaces.tsx:75 | fixture | Hardcoded queue |
| F15 | governor | src/App.tsx:66 | fixture | Governor Society: hardcoded advisor cards ("Protect the tactile opening"…), *"This recommendation is simulated"*; Model Gateway panel lists "Storyboard adapter / Ready fixture" | MAKE REAL (bind to `/api/governor/decide` + GV1/GV2) |
| F16 | council | src/MissingSurfaces.tsx:28 | fixture | Council Lab: "Ask Council" announces *"Council response assembled from local advisor fixtures"*; canned consensus ("Camera is static…"); advisors *"Local fixture · no provider call"* | MAKE REAL (wire to real `gateway_consult`) |
| F17 | toolLab | src/MissingSurfaces.tsx:30 | fixture | Hardcoded tool drafts |
| F18 | mediaEngine | src/MissingSurfaces.tsx:90 | fixture | Hardcoded worker/render rows |
| F19 | mediaTools | src/MissingSurfaces.tsx:92 | fixture | Hardcoded FFmpeg op list |
| F20 | composition | src/MissingSurfaces.tsx:94 | fixture | Hardcoded composition |
| F21 | experiments | src/MissingSurfaces.tsx:66 | fixture | Hardcoded A/B rows |
| F22 | performance | src/App.tsx:71 | fixture | Hardcoded signals |
| F23 | analytics | src/MissingSurfaces.tsx:69 | fixture | Hardcoded outcome charts |
| F24 | drift | src/MissingSurfaces.tsx:73 | fixture | Hardcoded drift alerts |
| F25 | adaptation | src/App.tsx:72 | fixture | Hardcoded mutations |
| F26 | adaptationCenter | src/MissingSurfaces.tsx:36 | fixture | Hardcoded promotions |
| F27 | skills | src/App.tsx:73 | fixture | Hardcoded skill registry |
| F28 | recipes | src/MissingSurfaces.tsx:82 | fixture | Hardcoded recipes |
| F29 | memory | src/MissingSurfaces.tsx:78 | fixture | Hardcoded memory entries |
| F30 | evolution | src/MissingSurfaces.tsx:80 | fixture | Hardcoded loop state |
| F31 | sandbox | src/MissingSurfaces.tsx:64 | fixture | **Deliberate product simulator** ("Simulate generating three food ads", cost/time estimates) - this one is a feature, not a lie | KEEP AS LABELED FALLBACK |
| F32 | resources | src/MissingSurfaces.tsx:52 | fixture | Hardcoded budget/cost numbers |
| F33 | credentials | src/MissingSurfaces.tsx:54 | fixture | Hardcoded provider rows despite a real `/api/credentials` backend existing |
| F34 | security | src/MissingSurfaces.tsx:60 | fixture | Hardcoded posture rows |
| F35 | policies | src/MissingSurfaces.tsx:62 | fixture | Hardcoded policy hierarchy (real `constitution.py` exists) |
| F36 | controlPlane | src/MissingSurfaces.tsx:101 | fixture | Hardcoded manifest view |
| F37 | memorySources | src/MissingSurfaces.tsx:104 | fixture | Hardcoded adapter list |
| F38 | permissions | src/MissingSurfaces.tsx:106 | fixture | Hardcoded permission matrix (real `permission_checks` table exists) |
| F39 | copilot | src/MissingSurfaces.tsx:58 | fixture | *"Copilot explanation generated from local evidence fixtures"*; demo answer *"A connected backend will cite actual runs"* |
| F40 | skillDiff | src/MissingSurfaces.tsx:38 | fixture | Hardcoded diff |
| F41 | terminal | src/MissingSurfaces.tsx:96 | fixture | "Flujo Terminal" renders canned commands; no terminal backend exists |

MISSING_BACKEND / CONTRACT_STUB routes (no backend at all - pure TODO):

| # | route | file:line | CAT | Disposition |
|---|-------|-----------|-----|-------------|
| F42 | automation | src/App.tsx:74 | todo | MAKE REAL (needs workflow scheduler backend) |
| F43 | calendar | src/MissingSurfaces.tsx:50 | todo | MAKE REAL |
| F44 | sourceMonitor | src/MissingSurfaces.tsx:45 | todo | MAKE REAL |
| F45 | socialAccounts | src/MissingSurfaces.tsx:48 | todo | MAKE REAL (OAuth per platform) |
| F46 | settings | src/RefinedSurfaces.tsx:40 | placeholder | SettingsHub: `CONTRACT_STUB` - local-only by design; provider tab is F-embedded fixtures | KEEP (local-only) but MAKE REAL the provider section (G9) |

Additional frontend items:

| # | file:line | CAT | What it fakes | Real requires | Disposition |
|---|-----------|-----|---------------|---------------|-------------|
| F47 | src/RefinedSurfaces.tsx:68 | fixture | SetupWizard: *"Everything here is local demo state"*; "Fetch fixture models" button; provider selection *"Nothing connects yet"*. Partially persists to `/api/installation/onboarding` (honest fallback). | LOCAL CODE | MAKE REAL |
| F48 | src/App.tsx:68 | fixture | `VideoPreview`: CSS-animated fake video stage (orbiting divs, fake timecode "00:18 / 00:30") used by the legacy demo Production surface. | LOCAL CODE (real artifact thumbnails / media player) | MAKE REAL |
| F49 | src/App.tsx:26, :30 | fixture | Navigation/command-palette metadata describes demo-only actions as real ("Run verification", "Start simulation") - action labels outrun backend capability on DEMO_ONLY routes. | LOCAL CODE | MAKE REAL (gate labels on contract) |

## 8. Electron shell

**Clean - no fakes found.** `electron/main.cjs` (403 lines): real sidecar supervision
(spawn → health probe → restart backoff → pid-file hygiene), real IPC contract
validation (`shellContracts.cjs`), packaged-renderer `loadFile` (no dev-server
`localhost:5173` bypass), per-launch random tokens, sandboxed renderer. The only
dev accommodation is running `backend/run.py` via `python3` when unpackaged -
legitimate dev behavior, not a fake backend.

## 9. Misc backend stubs (honest, fail-closed)

| # | file:line | CAT | What it is | Disposition |
|---|-----------|-----|------------|-------------|
| X1 | backend/app/repositories.py:180 | stub | `UnsupportedDomainRepository` raises `DOMAIN_REPOSITORY_UNAVAILABLE` ("not implemented in this local control-plane schema") - a named fail-closed stub, not a silent fake. | KEEP (implement per-domain as M11 reaches it) |
| X2 | backend/app/workflows/engine.py:83 | placeholder | `TemporalEngine` - "Deployment adapter placeholder; it fails closed until configured" (`TEMPORAL_ENGINE_NOT_CONFIGURED`). | KEEP AS LABELED FALLBACK |
| X3 | backend/app/guard/policy.py:180 | (honest) | `SIMULATOR_ONLY` provider-policy status is a first-class policy state (REVIEW, not PASS) - the policy engine treats simulated providers as untrusted. Not faked. | KEEP |

### Explicitly NOT faked (verified real during audit)

- Media execution boundary (binaries verify by sha256 + manifest; missing/corrupt → blocked, never simulated)
- Prep pipeline guard gate + "not faked" thumbnail behavior (prep.py:196)
- SQLite persistence layer, job engine, event/audit sink, doctor's DB/storage/memory/media checks
- Credential store (opaque references, masked values, never raw in UI)
- YouTube adapter (credential-gated, real HTTP)
- Zen adapter HTTP invoke path (credential-gated; catalog is fixture - G7)
- `Live*` surfaces that render real backend state (Production jobs, Doctor, Replay, Lineage, Guard, Monitor, Observability, Model Registry) - though their *content* is simulator-shaped until G1 is fixed

---

## Counts

### By category

| Category | Count |
|----------|-------|
| fixture | 49 (G7, G9-G12, F1-F41, F47-F49) |
| simulator | 7 (G1, G3, G4, G5, G6, GV1, GV2) |
| fake | 8 (C1-C7, D1) |
| stub | 4 (D2, D3, D4, X1) |
| placeholder | 4 (M1, M2, F46, X2) |
| mock | 2 (G2, P1) |
| todo | 5 (G8, F42-F45) |
| **Total faked items** | **79** |

### By area

| Area | Count |
|------|-------|
| Gateway | 12 (G1-G12) |
| Governor | 2 (GV1-GV2; frontend faces counted under Frontend as F15/F16) |
| Chat vertical slice | 7 (C1-C7) |
| Media | 2 (M1-M2) |
| Doctor | 4 (D1-D4) |
| Publishing | 1 (P1; P2 is real/key-gated) |
| Frontend surfaces | 49 (F1-F49, incl. 4 MISSING_BACKEND todos F42-F45) |
| Electron shell | 0 |
| Misc backend stubs | 2 (X1-X2) |
| **Total** | **79** |

### Disposition summary

| Disposition | Count |
|-------------|-------|
| MAKE REAL | 69 |
| KEY-GATE (needs credential/service) | 5 (G4, G5, G6, GV1, GV2) |
| KEEP AS LABELED FALLBACK | 3 (P1 test double, F31 sandbox feature, X2 Temporal placeholder) |
| KEEP (local-only by design) | 2 (F46 settings stub, X1 fail-closed repo) |
| **Total** | **79** |

Key external keys/services M11 needs: **NVIDIA_API_KEY** (new; no adapter exists yet),
OPENCODE_API_KEY (Zen paid models), OpenAI-compatible key + `OPENAI_COMPATIBLE_BASE_URL`,
Meta credential (likely drop), YouTube OAuth/API credential, per-platform OAuth for
socialAccounts (F45).

### Ambiguous / could not classify

1. **Dead code vs. alternate surfaces** - `src/MissingSurfaces.tsx` still exports
   `ModelRegistry` (:26), `Observability` (:86), `Replay` (:98), `Lineage` (:34),
   `CreativeChat` (:14), `WorkflowInspector` (:32), `Publishing` (:84), while
   `src/App.tsx:47` routes those names to `Live*`/`Refined*` components instead.
   Unclear whether the Missing exports are dead code or reachable via another path;
   several contain their own fixtures (e.g. the hardcoded NVIDIA model table at :26).
2. **Legacy demo components in `src/App.tsx`** - `Production` (:69), `Doctor` (:77),
   `Monitor` (:79) are superseded by Live components in the Page switch; `QA` (:70),
   `Blueprint` (:64), `Compiler` (:65), `Governor` (:66) are still routed. Whether the
   superseded ones are reachable is unverified.
3. **`credentials` / `permissions` / `policies` surfaces** (MissingSurfaces :54, :106,
   :62) render fixtures while real backends exist (`/api/credentials`,
   `permission_checks` table, `constitution.py`) - classified fixture/MAKE REAL, but
   the wiring gap size is unknown without checking each route's fetch code.
4. **`terminal` route** (MissingSurfaces :96) - no terminal backend endpoint exists;
   unclear if the intended M11 behavior is a real sandboxed shell or a read-only log
   viewer; classified fixture/todo pending product decision.
5. **`sandbox` route** (F31) - the one intentional simulator: de-simulating it would
   destroy the feature. Kept as labeled fallback by design.
6. **`settings` CONTRACT_STUB** (F46) - local-only settings are legitimate; only the
   provider/model sections (G9-G11) need de-simulation.
