# V2 compatibility plan

Evidence base: `lib/services/scan_service.dart`, `lib/services/wellar_v2_client.dart`, `lib/models/scan_result.dart`, `lib/models/wellar_v2_result.dart`, Directus snapshot/flow exports, `D:\flutter\last\ai-server`, and `D:\flutter\last\wellar-ai-v2`. Runtime behavior is UNVERIFIED; no services were run.

## Recommended thinnest compatibility layer

Add a server-side legacy facade at the existing `/process` URL. Flutter can continue creating a Directus scan, uploading files and requesting legacy processing. The facade authenticates the caller/service, verifies the scan belongs to the authenticated tenant, resolves Directus file IDs using service credentials, enforces consent and V2 size/format limits, stages the media to V2, and submits `/api/v1/scans`. Keep a durable mapping from Directus `wellness_scans.id` to V2 scan ID. A worker or callback then reads V2 result and updates Directus scan status. Flutter continues polling Directus.

Do not manufacture legacy readiness, fatigue, focus, risk or confidence scores when V2 does not produce validated equivalent fields. Mark unsupported columns null and add a neutral `analysis_mode`/`result_status` compatibility field, or keep V2 outputs in a separate JSON field/collection. The Flutter V2 result screen already communicates that validated fatigue/vigilance prediction is unavailable. Product approval of legacy UI semantics is a release gate.

Directus flow `WallerAi` must be changed to call only one orchestrator. If facade performs V2 submission, disable/replace any parallel Directus AI call to avoid duplicate jobs. Avoid giving Flutter V2 JWT/service credentials.

## Ordered implementation tasks

1. **Freeze contract** — Export a sanitized, exact table of `/process` request/response, scan/result fields, and active `WallerAi` operations. Confirm which records are written by AI versus Directus flow. Prompt: “Read-only: document exact deployed legacy `/process` multipart/JSON contract and Directus flow writes from checked-in code; list unknowns; do not edit files.”
2. **Define mapping and unsupported fields** — Agree which V2 evidence fields can populate Directus, and null semantics for legacy-only scores. Prompt: “Draft a versioned mapping from V2 result schema to Directus scan result; never infer unsupported fatigue/readiness scores; include nullable migration fields and tenant constraints.”
3. **Implement facade/auth** — Add `/process` adapter with idempotency keyed by Directus scan ID, authorization/tenant checks, safe errors and V2 auth. Prompt: “Implement `/process` compatibility facade with idempotency, authenticated scan ownership checks, V2 JWT service auth and redacted logs; preserve legacy status response.”
4. **Bridge media** — Resolve Directus file access and stage V2 upload; enforce caps, MIME, duration and PCM WAV requirements. Prompt: “Implement bounded Directus-to-V2 media staging for JPEG/MP4/PCM WAV; reject unsupported/oversize files before job creation; clean temporary copies on all paths.”
5. **Persist and poll** — Map V2 queued/processing/failed/completed to Directus scan lifecycle and write supported results; make updates idempotent. Prompt: “Implement V2 result worker to update wellness_scans and scan_results idempotently; retain legacy IDs and nullable unsupported metrics.”
6. **Directus orchestration** — Retarget or remove `WallerAi` so only facade/worker owns submission; review policies for tenant isolation and service role. Prompt: “Update sanitized Directus flow to invoke the compatibility facade once; preserve accountability and tenant scoping; document service permissions.”
7. **Flutter delta only if required** — Prefer no Flutter change initially. If V2 status cannot be reflected in Directus, switch status/result polling to `WellarV2Client` and map UI to `WellarV2Result`; change consent text before V2 research/media consent semantics diverge. Prompt: “Change Flutter scan polling to V2 only if Directus projection is unavailable; preserve existing capture and make unsupported legacy scores visibly unavailable.”
8. **Acceptance** — Verify synthetic media only, contract parity, duplicate submission, rejection/retake, deletion, tenant isolation and latency budget. Do not use real user media for acceptance without approved consent/data protocol. Prompt: “Run isolated synthetic contract checks for legacy facade ↔ V2 ↔ Directus projection, including idempotency, auth, cleanup, failures and timing; report measured p50/p95.”

## User-visible behavior and risks

- V2 result is descriptive evidence, not the legacy readiness/fatigue/focus/risk prediction; users may lose legacy score cards or see “unavailable”. Source: `wellar_v2_result_screen.dart` and V2 result model.
- Consent distinction: legacy scan currently sends `consent_granted`; V2 client uses media-processing true and research-use false. UI must accurately describe each purpose.
- V2 caps (MP4 <20 MiB, JPEG <5 MiB, PCM WAV <8 MiB) are stricter than legacy Flutter video hard cap 80 MiB. Retake/upload failures may increase unless app compresses before staging.
- Auth translation and tenant ownership are critical; a facade that accepts arbitrary Directus IDs could expose cross-tenant media/results.
- Async job retries can duplicate writes without idempotency; Directus flow plus facade can enqueue duplicate scans if both remain active.
- V2 scoring is not interchangeable with legacy calibration. Carrying legacy labels forward as if equivalent would mislead users/managers and corrupt reports.

## Seven-second target

The legacy app uploads media to Directus before calling AI, and video upload permits up to 3 minutes. Directus flow may add an additional external-request hop. The Flutter AI trigger itself allows 30s receive. V2 client uses a 20s timeout and staged upload, so neither path proves a 7-second end-to-end result. Network upload, Directus-to-V2 copy, queue wait, cold model startup and inference all count. At 5 MiB the transfer alone exceeds seven seconds on sufficiently slow mobile uplinks. Measure capture-finished-to-result p50/p95 with representative synthetic media and target network; optimize video duration/bitrate, avoid serial duplicate orchestration, warm inference workers and return async status promptly. Required target throughput and hardware are UNVERIFIED.

