# Wellar AI V2 legacy compatibility result

## Verdict

**URL-ONLY SWITCH POSSIBLE: NO.** The existing Flutter scan flow cannot be completed by pointing its AI server URL at V2. A safe `/process` adapter cannot submit these Directus-owned scans to the current V2 queue and then return a result through the Flutter app's existing Directus result reads. The checked-in Directus schema also cannot represent V2's honest `insufficient_evidence` output. No Directus write-back or scan-state mutation was attempted.

This is a compatibility gate and evidence report, not a completed drop-in adapter. Creating a route that reports `202` without durable work, marking scans complete without a result, or mapping abstention to one of the four scored labels would misrepresent what happened. Returning a failure would also cause Flutter's current finalizer to treat the request as failed and patch its Directus scan.

## Observed request, identity, media, and result contract

- The Flutter flow creates `wellness_scans` directly in Directus. It uploads media through Directus `/files`, creates `scan_media` with `scan_id`, `business_profile`, `video_file`, optional `audio_file` and thumbnail relations, then patches the scan to `media_ready`.
- The AI request is `POST /process`, JSON `{"scan_id":"<Directus wellness_scans id>"}`, with `Authorization: Bearer <Directus access token>`. Flutter accepts the process request only when the HTTP status is `202`; it then polls Directus `wellness_scans` and reads `scan_results` directly from Directus. The AI server response is not the result payload consumed by the result screen.
- The legacy server validates the Directus token by resolving the active user, verifies that the scan belongs to that user and that an active membership matches the scan's organization/member scope, requires a media-ready scan and the configured `scan_media` relations, transitions the record to `processing`, and schedules background work. It handles existing processing/completed/failed states idempotently and recovers a completed scan when a result row exists. It writes a `scan_results` row and transitions `wellness_scans` to `completed`; terminal errors write failure status/reason.
- V2 currently exposes `/api/v1/media/*` and `/api/v1/scans*`, not `/process`. V2 requires V2-owned opaque media references and a V2 `Idempotency-Key`; the queue assigns a new V2 scan ID. It does not accept Directus file IDs or bind an existing Directus scan through an HTTP contract. Its worker result is `insufficient_evidence` while predictive targets are unavailable.
- V2's existing Directus authentication validates the caller and active membership, but that identity check alone does not prove ownership of an arbitrary submitted Directus `scan_id`, authorize linked Directus files, or create a durable binding to the existing scan. The V2 result endpoint is also not used by this Flutter flow.

## Directus abstention incompatibility

Evidence is from the Flutter repository's checked-in snapshot files, not a live Directus instance:

- `scan_results.risk_level` is marked required and offers only `stable`, `low_focus`, `elevated_fatigue`, and `high_risk`.
- `scan_results.explanation` and `suggested_action` are marked required. `readiness_score` is nullable and marked optional, so the numeric field alone is not the blocker.
- There is no `insufficient_evidence` choice and no `v2_result`, `result_json`, or `analysis_status` abstention envelope.
- `scan_media.video_file` and `audio_file` are Directus file relations. V2 object-store media references cannot be written into those relations as though they were Directus file IDs.
- There is no V2 scan correlation field on `wellness_scans` for idempotent recovery from a lost response.
- The permission snapshot is a collection-level export, not proof of active deployed policies or a least-privilege V2 service identity. Its observed actions are not sufficient authorization evidence for enabling writes.

The required `risk_level` has no truthful value for V2's current abstention. Writing a scored category, fabricated numeric score, or fabricated confidence is therefore prohibited. V2 must stop before result write-back and must not mark the Directus scan completed.

## Changes and verification

- Modified `deployment/directus_snapshot_compat.py` to report the observed `risk_level` choices, whether the legacy enum can represent `insufficient_evidence`, and a specific blocker. It now parses permission snapshots represented as either a JSON list or a wrapped object. The gate continues to report `safe_to_enable_writeback: false`; snapshot checks never authorize live writes.
- Modified `tests/test_directus_snapshot_compat.py` with a regression test for the scored-only legacy enum and list-form permission exports.
- Added pre-edit copies of both modified files under `backups/legacy-drop-in-preedit-20261016/`.
- No legacy, Flutter, Directus, production, or persistent-volume files were changed. No `/process` endpoint, Directus writer, or partial queue bridge was added because it could not safely complete this current Flutter contract.

Commands and results:

- `python deployment/directus_snapshot_compat.py --schema <Flutter Directus schema snapshot JSON> --permissions <Flutter Directus app permissions JSON>` — `safe_to_enable_writeback=false`; found Directus file relations, no abstention envelope, and no V2 correlation field. The permission export is snapshot-only and not authoritative.
- `.venv\Scripts\python.exe -m pytest tests\test_directus_snapshot_compat.py tests\test_directus_sync.py -q` — **17 passed**, one existing Starlette/AnyIO deprecation warning.
- `.venv\Scripts\python.exe -m pytest -q` — **203 passed, 4 skipped**, one existing Starlette/AnyIO deprecation warning. The skipped tests require live PostgreSQL or MinIO configuration and were not run against production services.
- The first test command using system Python could not import `python-dotenv`; rerunning with the repository virtual environment resolved the environment mismatch.

## Release blocker and next action

URL-only replacement remains blocked by both (1) a V2 bridge that securely resolves and binds the caller-authorized Directus scan and its existing Directus media into the durable V2 queue, and (2) a Directus result representation that allows explicit abstention without requiring a scored `risk_level`. The current Flutter client also requires the Directus scan/result persistence path to complete, so an isolated V2 result API is insufficient.

**Next action:** approve a versioned Directus result contract that represents `insufficient_evidence` and establish a least-privilege, staging-verified scan/media binding and idempotency mechanism; then implement and test `/process` against that contract before considering an AI server URL switch.
