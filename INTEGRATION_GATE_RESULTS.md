# Wellar AI V2 Integration Gate Results

**Assessment date:** 2026-10-14  
**Scope:** Read-only integration assessment. No client or backend code was changed. This report is the only file created.

## Gate decision

Both client families exist locally, but neither currently submits a scan directly to Wellar AI V2. Flutter owns the capture flow and currently finalizes scans through Directus plus the legacy `/process` endpoint. Angular reads scan and result records from Directus; no Angular capture or V2 scan client was found. V2 offers a separate authenticated media-reference and queued scan API. A real end-to-end path therefore requires a client contract change and an identity bridge, not just a base URL swap.

The shortest safe first demonstration is a synthetic-token API smoke run using V2's own documented commands and generated non-human media (or a reaction-only scan). A real employee scan cannot be represented as a model prediction today: V2 explicitly returns `insufficient_evidence` and has no validated model. Do not treat this system as production ready.

## Verified project paths

| Role | Candidate project | Evidence and caveat |
|---|---|---|
| Standalone V2 API | `D:\flutter\last\wellar-ai-v2` | Current workspace; `README.md`, `app/`, `tests/`, `.venv/`. |
| Angular web | `D:\flutter\wellar-pro` | `angular.json`, `package.json`, Angular source and Directus-backed dashboard/history. A second Angular app exists at `D:\flutter\landingpage\wellar-ui`; additional `wellar-ui-main`, `wellar-ui-final-release`, `wellar-pro`, and worktree copies are present. The canonical/current web checkout is not provable from directory names alone. |
| Flutter mobile | `D:\flutter\waller_app` | `pubspec.yaml`, `lib/`, scan flow and tests. `D:\flutter\wellar_mobile_release` and other `waller_app_*` copies also exist. `waller_app/README.md` identifies the separate mobile client. |

There are no `AGENTS.md` files under these selected project roots. `D:\flutter\waller_app` has a very large dirty working tree (modified scan, auth, config, UI and platform files plus untracked artifacts); it must be treated as an in-progress checkout, not a pristine baseline. The Angular and Flutter candidates are separate from the V2 workspace. Confirming which duplicate checkout is authoritative remains an owner-level unknown.

## What exists today

### Angular web

- Angular 21 app with npm scripts (`D:\flutter\wellar-pro\package.json`) and environment files under `src/environments/`.
- Existing sign-in and Directus REST integration are configured against `https://dash.conntinuity.com` (`src/environments/environment*.ts`).
- `src/app/services/dashboard.service.ts` queries Directus `wellness_scans` and `scan_results`, with selectable result-field variants and degraded-access handling.
- `src/app/Pages/history/history.ts` maps the legacy result fields (`overall_state`, summaries, recommendation and focus score) into the history display.
- Web surfaces scan requests and scan history; no browser camera/microphone capture, reaction task runner, V2 API client, media upload flow, or V2 status polling path was found in the inspected app source. The web app is therefore a consumer of legacy persisted results today, not a scan-producing client.

### Flutter mobile

- Flutter app has a complete on-device capture sequence in `lib/screens/scan_flow_impl_normalized.dart`: consent, permissions, camera video, microphone audio and a timed focus/reaction-style task. Camera recording logs target H.264 MP4 at 1280x720. It records mono AAC-LC at 44.1 kHz (`AudioEncoder.aacLc`), which V2 does not accept as audio (V2 accepts WAV only). The 15-second focus task randomly activates tiles and counts correct taps (`_handleFocusTap`); inspected submission code writes `task_metrics` through Directus, not a V2 `ReactionTask` trial timeline. This requires an explicit timing/event adaptation if the task is sent to V2.
- `lib/services/scan_finalize_service.dart` sequences Directus file uploads, creation/update of Directus scan records and then legacy process triggering.
- `lib/services/scan_service.dart` uses Directus `POST /files` multipart uploads and `POST /items/wellness_scans`; status and result history are read from Directus collections. The legacy process call posts `{ "scan_id": ... }` to `/process` with the current Directus bearer token.
- `lib/config/app_config.dart` sets `AI_SERVER_URL` to the legacy hosted `/process` endpoint by default. Directus, Google mobile exchange, public web URLs and push configuration are separately defined with `String.fromEnvironment` values.
- The Flutter `ScanResult` and display helpers (`lib/models/scan_result.dart`, `lib/utils/scan_result_display.dart`) understand persisted legacy result records and readiness labels. They do not directly parse the V2 `ScanResult` response shape.

### Wellar AI V2 API

Verified in `app/api/v1.py`, `app/scans/schemas.py`, `app/security/auth.py`, `app/media/storage.py`, `app/core/config.py`, `app/workers/entrypoint.py` and `README.md`:

1. Authenticate requests with `Authorization: Bearer …`.
2. Upload raw bytes separately to `POST /api/v1/media/{video|image|audio}` with accepted content type. Response contains an opaque, tenant/subject-bound `media_ref`.
3. Submit `POST /api/v1/scans` with required `Idempotency-Key` and JSON schema version `1.0`, `media_refs`, optional `reaction_task`, and required affirmative `consent` when media or reaction data is supplied.
4. Accepted response is HTTP 202 with `scan_id`, `status`, `created`; `Location` points to `/api/v1/scans/{scan_id}`.
5. Poll `GET /api/v1/scans/{scan_id}`; retrieve result from `GET /api/v1/scans/{scan_id}/result`. A result can be unavailable until processing finishes (404 `result_unavailable`).

Media accepted by local V2 storage is MP4/AVI, JPEG/PNG, and PCM WAV (`app/media/storage.py`). It validates MIME and file signatures, caps size/duration/frames/pixels and quota, and does not accept a multipart form for this API. V2 reaction input is structured event data (`task_type` `pvt` or `simple_reaction`; ordered timestamped trials), not the legacy aggregate `task_metrics` record. Client-provided timing is explicitly unverified.

Authentication supports a development map of explicitly configured synthetic tokens and an OIDC/JWKS verifier (`app/security/auth.py`). `.env.example` contains only a synthetic local token example. V2 does not configure Directus or an identity provider. Whether current Directus-issued mobile/web tokens satisfy a separately configured OIDC issuer, audience, signing-key and tenant-claim contract is unknown and must be decided before direct authenticated client integration.

V2 result schema is version `2.0`: `scan_id`, `status`, nullable `model_version`, `model_invocation_status`, `targets`, `quality`, `features`, and `message` (`app/scans/schemas.py`). The documented default worker gives `insufficient_evidence`; V2 reports no validated sleepiness/vigilance prediction. Do not map this into legacy `Stable`/`Low Focus`/`Elevated Fatigue`/`High Risk` labels or fabricate a score.

## Main incompatibilities and required changes

| Area | Existing client contract | V2 contract | Minimum change |
|---|---|---|---|
| Identity | Directus access token used for Directus reads/writes and legacy process endpoint. | Synthetic dev-token map locally; configured OIDC JWT verification for an external identity provider. | Agree on issuer/audience/JWKS and a trusted `sub` plus `tenant_id` claim, or implement an authorized backend identity bridge. Do not reuse a token by assumption. Tenant and subject must be derived and scoped server-side. |
| Media intake | Flutter sends multipart `file` to Directus `/files`; Directus IDs are stored in scan records. Video is MP4; audio is recorded as mono AAC-LC/44.1 kHz. | Separate raw-body uploads; opaque V2 media refs; PCM WAV only for audio. | Add V2 upload API client with bearer auth and correct `Content-Type`; encode/convert capture audio to valid PCM WAV; keep media refs and cleanup behavior explicit. Do not pass Directus file IDs as V2 refs. |
| Scan submission | Legacy finalizer creates `wellness_scans` and related `scan_media`, patches media-ready state, then calls legacy `/process` with a scan ID. Its 15-second focus task counts correct random-tile taps; it does not currently submit per-trial stimulus/response timestamps. | `POST /api/v1/scans` with V2 media refs, consent, optional structured reaction task and mandatory idempotency key. | Replace the legacy process path with a V2 submission service and preserve idempotency across safe retries. Capture V2-required ordered trial timestamps only if this is intended to be a PVT/simple reaction task; current tap count alone is insufficient. Decide how any Directus workflow/history record is associated without making it a prerequisite for V2 acceptance. |
| Status/results | Clients poll/read Directus scan and result collections using Directus status, fields and IDs. | V2 scan ID/status resource and a separate result resource returning V2 schema. | Poll V2's URLs using returned ID; model queueing, transient 404 result, terminal/failure and retryable states. Add distinct UI state for `insufficient_evidence` and unavailability. |
| Schema/UI | Legacy outputs include readiness labels, summary, action and focus fields. | `targets`, `quality`, `features`, message; no validated prediction. | Add V2-specific response model/projection. Display the factual processing/evidence state only; keep legacy clinical/operational labels hidden unless a validated, governed mapping exists. |
| Web feature | Angular displays Directus history and requests; no scan capture flow found. | V2 requires media upload or reaction task submission. | Fastest browser path is not to duplicate capture: first use web for a V2-backed status/result view only if IDs/results are made available to it. A web-initiated real scan requires a new capture/consent flow and is a separate feature. |
| Errors | Existing clients normalize Directus/Dio errors and have legacy polling policies. | V2 errors use an `error` object with code/message and HTTP statuses including 401, 404, 409/422, 429, 413, 503. | Normalize V2 error envelope and preserve retryability distinctions; never blindly retry non-idempotent or consent/validation failures. |
| Network/security | Angular dev uses localhost:4200 but calls hosted Directus. Flutter defaults to hosted Directus and hosted legacy AI. | Browser needs CORS for its origin; device/emulator needs a reachable API address (not `localhost` from device). | Configure explicit development API base URLs, CORS allowlist, emulator/phone routing and HTTPS for any non-loopback connection. Do not hard-code dev tokens in app builds. |

## Fastest safe integration path

1. **Select canonical client checkouts and freeze the contract.** Resolve duplicate Angular/Flutter directories and review the dirty Flutter changes. Define endpoint base URL, supported media MIME/codec, consent fields, request idempotency and status/result states.
2. **Prove V2 locally first with synthetic identity.** Run the existing V2 API and worker on loopback with `.env.example` synthetic token. Submit no-media synthetic request and poll status/result. Exercise synthetic, generated non-human image/audio/video only if needed. Expect `insufficient_evidence`; this validates transport and queue only.
3. **Establish identity for clients.** Configure a trusted OIDC issuer or a narrowly scoped authenticated gateway that issues/validates V2 credentials. Verify claims and tenant isolation without real employee data. This is an external design/configuration dependency; no provider is present locally.
4. **Integrate Flutter first.** Preserve capture/consent/reaction UI; adapt audio recording to PCM WAV, upload each modality to V2, submit refs plus consent and idempotency key, poll status/result, and represent V2's non-predictive output. Remove the `/process` trigger only as part of the explicit V2 route switch. Keep Directus uses for unrelated app workflows.
5. **Add a V2 result adapter for Angular.** Short-term choice: expose authorized V2 status/results to web through a safe backend integration or an explicit API client and identity contract. Angular currently has no V2 scan ID association and uses Directus result rows, so the mapping/visibility decision must be made. Do not write synthetic V2 results into legacy result collections.
6. **Verify on local clients and devices.** Run API contract tests and client unit tests using generated fixtures; then a local browser and emulator/device test. Only after the identity and network path works can a real, consented scan demonstrate media transport; its output remains evidence status, not a validated health/readiness prediction.

## What can be demonstrated offline

- Static review of all local contracts and client flows.
- V2 API + worker + local SQLite queue + local media store on the same machine, using synthetic auth token and loopback. V2 docs include a no-media synthetic submission. No Internet is needed for this if Python dependencies are already installed and no startup code fetches external assets.
- Unit/contract tests with synthetic/generated non-human media. The V2 test suite and Angular/Flutter test files exist locally; this assessment did not execute them.
- Angular build/serve and Flutter build/run may work offline only if their local package caches and SDK/platform components are already present.

## What needs external infrastructure or Internet

- Directus login, Directus-backed dashboard/history/workspace workflows, Google mobile exchange, Firebase authentication/push, and current legacy AI `/process` are hosted dependencies.
- V2 OIDC/JWKS auth requires a real configured issuer and reachable JWKS unless tests inject a local authenticator. A clean app-to-V2 login flow is not present.
- Browser/device connectivity across machines needs LAN routing, host firewall setup and HTTPS for non-loopback deployment; web CORS must allow the local origin.
- PostgreSQL, S3-compatible object storage, managed secrets, hosted API/worker availability and operational monitoring are not locally established. V2 local SQLite/filesystem mode is development-only.
- Validated model predictions require separately authorized, independently validated models and governance; unavailable in this repo's documented default behavior.

## Local commands and tools

No commands below were run as part of this read-only assessment. V2 README documents the following local run path (dependencies already installed):

```powershell
Set-Location D:\flutter\last\wellar-ai-v2
Copy-Item .env.example .env   # only if .env does not already exist; keep its synthetic token local
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --env-file .env
```

In a second terminal, run the local worker:

```powershell
Set-Location D:\flutter\last\wellar-ai-v2
.\.venv\Scripts\python.exe -m app.workers.entrypoint
```

Then follow the synthetic request in `README.md`, poll the returned `scan_id` at `/api/v1/scans/{scan_id}`, and request `/api/v1/scans/{scan_id}/result`. To use tests after the assessment, V2 documents `python -m pytest -q`; Angular scripts are `npm start`, `npm test`, `npm run build` in `D:\flutter\wellar-pro`; Flutter commands are `flutter pub get`, `flutter test`, `flutter run` in `D:\flutter\waller_app`. Do not run dependency-install commands under the no-download constraint.

Locally detected: Python 3.10.11, Node 24.16.0, npm 11.13.0, Flutter and Dart commands on PATH, RTK. A V2 `.venv` directory exists. `docker` and standalone `uvicorn` were not found by the shell command lookup. Whether V2 dependencies are already installed in `.venv`, Angular `node_modules` are present at the selected root, Flutter package cache/platform SDKs are complete, or a local database/container service is running was not verified. No internet, download, tests, application run, or external service call was made.

## Existing blockers

- No agreed identity issuer/tenant-claim integration between Directus-authenticated clients and V2 OIDC.
- Flutter legacy scan workflow is tightly coupled to Directus file IDs and Directus scan records; V2 media refs and scan IDs are a separate lifecycle.
- Flutter's microphone output must be confirmed/converted to PCM WAV; current V2 rejects other audio formats. Capture timing metadata is not currently demonstrated to match V2's `ReactionTask` event schema.
- Angular has no scan capture/submission feature and its result UI requires legacy readiness fields; no V2 record association is present.
- V2's worker does not provide a validated model prediction. This is a hard limit for demonstrating existing result labels, not a wiring bug.
- Multiple candidate checkouts and dirty Flutter working tree make it unsafe to assume which exact client source is intended for changes.
- Local Python/API process, dependency readiness, CORS behavior and emulator/browser network route have not been exercised.

## Inspection boundary: verified vs unknown

**Inspected:** V2 API routes, schemas, auth adapters, upload MIME/storage validation, env example, README, Angular project manifests/environment files/dashboard scan queries/history projection, Flutter config/scan capture/finalization/service/result model and selected local tests/tool manifests. File references above are repository-relative to each verified root.

**Not established:** canonical identity provider or its claims/JWKS; actual local secrets or `.env`; whether selected apps are the authoritative/latest copies; whether V2 dependencies are installed; exact recorded audio codec/bytes on each target device; runtime CORS/firewall/emulator reachability; live Directus/Firebase availability; production storage/worker operations; correctness/validity of any predictive model. No runtime behavior or external endpoint was verified.
