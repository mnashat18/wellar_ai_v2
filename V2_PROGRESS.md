# Wellar V2 Ship Hardening Progress

Updated: 2026-10-16

## Current status

| Stage | Status | Evidence / remaining work |
|---|---|---|
| S1 Deletion (F-01) | PASS | Commit `773c88f`; local/fake S3 cleanup, poison payloads, retry, crash/restart reconciliation. |
| S2 Killable decode (F-10/F-15) | PASS | Commit `3a2ec18`; wall-time and RSS supervised child process, synthetic failure fixtures. |
| S3 Config + baseline freshness (F-08/F-19) | PASS | Commit `ddfce5d`; strict config validation/startup guard, timestamped 90-day freshness with reason codes. |
| S4 Dependencies | PASS WITH RESIDUAL ADVISORY | Commit `9649d35`; pinned updates and lock audit. One protobuf DoS advisory remains because patched protobuf 5 conflicts with the current MediaPipe `solutions` API. |
| S5 Directus-pull facade | PASS FOR LOCAL FAKE GATE | Commit `e5552c6`; checked-in maps, `/process` durable enqueue, worker ID-based file fetch, ownership/tenant/attachment checks, fixed-origin SSRF/redirect protection, legacy projection, idempotent write/delete, `deletion_proven`. Safe suite at that stage: 219 passed, 4 skipped. Live staging contract remains OWNER ACTION. |
| S6 EasyPanel deploy kit | PASS FOR LOCAL GATE | Commits `63d40e7` and `97881f4`. Names-only env template, concise deployment/migration steps, verifier supports loopback and exact-host HTTPS staging with read-only probes, OpenAPI regenerated and drift-tested. Safe suite: 228 passed, 4 skipped. No staging endpoint was contacted. |
| S7 Final | PASS FOR SAFE-SUITE GATE | Commit `bc10488`; final safe suite: 228 passed, 4 skipped, 0 failed, 3 warnings. Final report reconciled to evidence and owner actions. |

## S5 record

- Backups: `backups/ship-v1-20261016/S5/` contains pre-edit copies of each existing file changed in this stage. The two copied compatibility maps are newly added files in `docs/`.
- Maps found and copied read-only from `D:\flutter_app\wellar-flutter-app`: `LEGACY_AI_INTEGRATION_MAP.md`, `V2_COMPATIBILITY_PLAN.md`. They specify the JSON body and Directus collection relationships.
- `POST /process` (also `/api/v1/process`) validates the legacy JSON, checks authenticated subject equals `user_id`, queues durably with scan ID as idempotency key, and returned 202 in <200 ms in the local endpoint test. Worker does remote work.
- `DirectusPullProcessor` accesses only `/assets/{file_id}` and Directus collection endpoints built under `WELLAR_DIRECTUS_URL`; it forbids redirects, enforces public DNS outside injected tests, validates scan owner/business profile/status and linked `scan_media` IDs, then enforces MP4/JPEG/WAV MIME/signature/size limits.
- Successful worker flow uses the ordinary processor, writes only schema-supported Directus result fields with null score values and a full projection in `internal_analysis`, writes scan status after media removal, deletes V2 copies and the three Directus file IDs with retries, and stores `deletion_proven` in the completed V2 result and Directus `internal_analysis`.
- Tests added: `tests/test_directus_pull.py` (12 tests) uses protocol fakes plus `httpx.MockTransport` as a local fake Directus server. Covers fast idempotent queue acceptance, owner/tenant/file mismatch, media staging, corrupt/oversize, HTTP redirect/private address rejection, Directus outage/retry, deletion retry and idempotent result writing. No existing tests changed.
- Targeted S5 tests: 12 passed. Safe suite command: `.venv\Scripts\python.exe -m pytest -q --ignore=tests/test_research_image_drowsiness.py --ignore=tests/test_research_evaluation.py --ignore=tests/test_research_drowsiness_worker.py --ignore=tests/test_owned_pilot_workflow.py --ignore=tests/test_ml_training_pipeline.py --ignore=tests/test_dataset_qualification.py --ignore=tests/test_real_media_acceptance.py --ignore=tests/research` — final run: 219 passed, 4 skipped, 0 failed, 3 warnings.
- One preceding full safe run had a single failure in synthetic AVI extraction; the same test passed alone and in the next two full safe runs (one 219-pass run occurred before a small Directus cleanup-order adjustment; the final full safe run also passed). Treat as a transient/flaky inference test; no test was edited.
- Four skips are two MinIO and two PostgreSQL integration tests without configured live endpoints. No training, pilot media, production service, or live Directus was used.
- Deployment owner action: verify staging Directus permissions, field names/status enums, and that V2 `tenant_id` maps to Directus `business_profile`; run `tools/verify_deployment.py` in S6 when staging credentials are supplied. This does not block S6 local kit implementation.
- Commit: `e5552c6`.

## S7 record

- Backups: `backups/ship-v1-20261016/S7/` contains pre-edit `V2_PROGRESS.md` and `V2_FINAL_REPORT.md`.
- Final safe-suite command was rerun unchanged after S6: **228 passed, 4 skipped, 0 failed, 3 warnings**. Four skips remain two MinIO and two PostgreSQL live-integration cases because endpoints were not configured.
- Rewrote `V2_FINAL_REPORT.md` as a current-state report with F-01..F-20 statuses and evidence, dependencies/advisory, tests, non-validation limits, and exact owner actions. No code/test changes in S7; no tests edited in S1–S7.
- No production/staging calls, training, dataset searches, or pilot media were used.
- Commit: `bc10488`.

## S6 record

- Backup: `backups/ship-v1-20261016/S6/` contains pre-edit versions of the existing docs, EasyPanel README, verifier, OpenAPI artifact, and reports.
- Added `deployment/easypanel/.env.template` with variable names only. Replaced the EasyPanel README with seven concise staging/deployment/migration/rollback steps. Updated environment variable names and operational notes to describe the supervised decoder, bounded drain, reconciliation/queued-media retention behavior, and verifier correctly.
- Recreated `tools/verify_deployment.py` after the prior interrupted delete. It performs read-only `/health`, `/ready`, `/openapi.json`, and authenticated measurements-contract requests. Non-loopback targets require HTTPS and exact `V2_VERIFY_ALLOWED_HOST`; redirect following is disabled. It does not upload media or create labels and prints status only, not response bodies or credentials.
- Added `tests/test_verify_deployment_tool.py` with loopback, exact-host allowlist, scheme, credential, path, query and unauthorized-host cases. No existing tests changed.
- Regenerated `openapi.json` with `tools/export_openapi.py`. Focused verifier + runtime contract tests: 22 passed. Safe suite: 228 passed, 4 skipped, 0 failed, 3 warnings. Skips are two MinIO and two PostgreSQL live tests with no endpoint configured.
- No live staging URL was contacted. Owner staging action: set `V2_VERIFY_BASE_URL`, `V2_VERIFY_BEARER_TOKEN`, `V2_VERIFY_ALLOW_STAGING=true`, and `V2_VERIFY_ALLOWED_HOST` in a staging-only PowerShell session, then run `rtk proxy .\\.venv\\Scripts\\python.exe tools/verify_deployment.py`.
- Commit: `63d40e7` plus `97881f4` (ignored template included in follow-up stage commit).
