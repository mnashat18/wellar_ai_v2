# Wellar V2 Ship Hardening Report

Updated: 2026-10-16

## Current release decision

**DO NOT SHIP.** S1–S7 local gates pass, but one protobuf advisory remains and staging/production deployment, live service integration, and capacity are unverified. No scientific validation or production performance is claimed.

## Stage status

| Stage | Status | Evidence |
|---|---|---|
| S1 Deletion (F-01) | PASS | Commit `773c88f`; local/fake-S3 deletion, retry, poison and crash/restart reconciliation tests. |
| S2 Killable decode (F-10/F-15) | PASS | Commit `3a2ec18`; child process deadline/RSS supervision and synthetic failure cases. |
| S3 Config + baseline freshness (F-08/F-19) | PASS | Commit `ddfce5d`; strict config and freshness-window tests. |
| S4 Dependencies | PASS WITH RESIDUAL ADVISORY | Commit `9649d35`; pins/lock updated; protobuf advisory remains due MediaPipe compatibility. |
| S5 Directus-pull facade | PASS FOR LOCAL FAKE GATE | Commit `e5552c6`; fake Directus API tests and safe suite. Staging contract unverified. |
| S6 EasyPanel deploy kit | PASS FOR LOCAL GATE | Commits `63d40e7` and `97881f4`; verifier/config/docs and contract tests pass; no staging URL called. |
| S7 Final | PASS FOR SAFE-SUITE GATE | Commit `bc10488`; final safe suite and report reconciliation complete. |

## Findings snapshot

| Finding | Current status | Evidence / limit |
|---|---|---|
| F-01 media deletion | FIXED FOR LOCAL GATE | S1 reconciliation, poison cleanup, retries, fake S3, subprocess restart tests. Real object storage remains unverified. |
| F-02 labels/consent | PARTIAL | Consent-gated feature-only KSS and withdrawal code/tests exist; live DB parity and purge remain unverified. |
| F-03 capacity | OWNER ACTION | No staging workload/hardware supplied; run approved load test on disposable staging before rollout. |
| F-04 deployment | OWNER ACTION | EasyPanel kit is present; owner must run its migration and staging verification procedure. |
| F-05 inference extraction | PARTIAL | Synthetic pipeline code/tests only; real-user measurement fidelity and scientific validity are not established. |
| F-06 API contract | PARTIAL | Runtime OpenAPI regenerated and drift test passes; live facade compatibility remains unverified. |
| F-07 naming/fidelity | PARTIAL | Short-clip naming and rule path were implemented earlier; no human-data validation. |
| F-08 configuration | FIXED FOR TESTED CONTRACT | Strict key/type/range/startup tests in S3. |
| F-09 label withdrawal/retention | PARTIAL | Endpoints and local behavior exist; no live Postgres purge/withdrawal verification. |
| F-10 killable decode | FIXED FOR LOCAL GATE | Supervised child wall-time/RSS test coverage. |
| F-11 reliability | PARTIAL | Retries/leases and local failure injection exist; distributed-service behavior unverified. |
| F-12 privacy/observability | PARTIAL | Existing bounded/sanitized logs and metrics have tests, but not a deployment-wide proof. |
| F-13 integration surface | OWNER ACTION | Directus maps/facade and fake tests exist; owner staging role/schema and tenant mapping verification required. |
| F-14 container/deployment | OWNER ACTION | EasyPanel instructions updated; owner must execute deployment, resource limits, backup/restore validation. |
| F-15 decoder abuse resistance | PARTIAL | Synthetic corrupt/oversize/deadline cases; real native decoder stress not performed. |
| F-16 dependency security | OWNER ACTION | One unrated protobuf advisory remains due current MediaPipe compatibility (details below); owner must approve risk or fund migration. |
| F-17 legacy compatibility | PARTIAL | Projection tests pass against copied map; actual Flutter/Directus parsing was not exercised. |
| F-18 quality/threshold calibration | OWNER ACTION | Human calibration evidence and approved threshold rationale are not available; no classifier/accuracy claim is made. |
| F-19 personal baseline | FIXED FOR LOCAL GATE | Freshness filtering, minimum sample and stale behavior tested. |
| F-20 dependency security tracking | OWNER ACTION | Audit is not clean; owner disposition for the protobuf advisory is required. |

## S4 dependency audit

- Pinned versions include FastAPI 0.143.2, Starlette 1.3.1, PyJWT 2.15.0, pytest 9.0.3, python-dotenv 1.2.2, psutil 7.2.2 and protobuf 4.25.9; lock regenerated.
- `pip-audit --requirement requirements.txt --disable-pip --no-deps --format json` found `PYSEC-2026-1805` / `CVE-2026-0994` in protobuf 4.25.9 (duplicate records represent one unique advisory). pip-audit did not provide severity: **UNRATED**. The issue concerns denial of service in nested `google.protobuf.json_format.ParseDict()` Any handling; fixed versions listed by the advisory are 5.29.6 and 6.33.5.
- MediaPipe 0.10.21 constrains protobuf below 5. A trial version that accommodates protobuf 5 removed `mediapipe.solutions` and broke the existing extraction API. No compatible patched combination was verified. Application source search found no direct `ParseDict`, `json_format`, or protobuf Any call; this does not prove absence of transitive exposure.

## S5/S6 contract and deployment details

- `POST /process` and `/api/v1/process` queue by scan ID. Worker uses Directus file IDs under a configured origin, checks scan owner/tenant/media attachments, rejects redirects/private DNS, validates media limits/signatures, and writes nullable legacy scores. Tests use a local fake (`tests/test_directus_pull.py`); real Directus roles and schema were not contacted.
- S6 deploy files: `deployment/easypanel/.env.template` contains names only; README documents staging, migration, API/worker separation, verification and rollback. `tools/verify_deployment.py` is a read-only probe for health/readiness/OpenAPI/measurements. It allows loopback HTTP or exact-host HTTPS staging, refuses redirects, and prints status only. No staging run was made.
- `openapi.json` was generated from the runtime application and `tests/test_integration_contracts.py::test_checked_openapi_matches_runtime` passed.

## Test record

- S5 safe suite: 219 passed, 4 skipped, 0 failed, 3 warnings.
- S6 focused tests (verifier + runtime contract): 22 passed, 0 failed, 2 warnings.
- Final S7 safe suite: 228 passed, 4 skipped, 0 failed, 3 warnings.
- Safe command excluded research, training, dataset, pilot and `test_real_media_acceptance.py` paths. Four skips: two MinIO and two PostgreSQL integration tests, because service endpoints were not configured.
- Warnings: Starlette/httpx and AnyIO deprecations, and a deliberately short synthetic HMAC key warning from an auth test. One earlier AVI synthetic extraction test was transiently flaky; later full safe runs passed. No existing tests were edited in S5 or S6.

## Owner actions

- For staging verifier: in a staging-only PowerShell session set `V2_VERIFY_BASE_URL`, `V2_VERIFY_BEARER_TOKEN`, `V2_VERIFY_ALLOW_STAGING=true`, and `V2_VERIFY_ALLOWED_HOST`, then run `rtk proxy .\\.venv\\Scripts\\python.exe tools/verify_deployment.py`. Do not use a production host.
- Confirm Directus `business_profile` mapping, status enum, role permissions, and supported result fields in staging.
- Decide whether to accept the protobuf advisory temporarily or fund a compatible MediaPipe Tasks migration; then rerun pip-audit.
- Run the verifier against a disposable HTTPS staging host using the documented `V2_VERIFY_*` environment names; do not use production.
- Run the load/capacity and live Postgres/MinIO gates on staging hardware/services before any ship decision.

## Not validated

No production or staging calls, real Directus service, PostgreSQL/MinIO live integration, human-data scientific validation, pilot media, training, full-scale load, backup restore, or actual EasyPanel deployment was used. Nothing in this report claims scientific accuracy, deployment readiness, or target latency.
