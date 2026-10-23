# Wellar AI V2 — Feature Audit

Audit date: 2026-10-17. Scope: local source, local tests and deployment files only. No deployed endpoint was contacted. No code, test, service or database was changed. The only created file is this report.

## A. Feature matrix

Status meanings: `DONE-VERIFIED-LOCAL` means local implementation and successful local verification; `DONE-UNVERIFIED-LIVE` means source and test coverage are present, but live behavior was not verified in this audit; `PARTIAL` means a material limitation or verification gap remains; `MISSING` means no implementation was found. Test execution was blocked before collection by missing `python-dotenv` (`tests/conftest.py:4`, `app/core/config.py:19`), so no feature is labeled locally test-verified in this report.

| Feature | Status | Evidence | Class |
|---|---|---|---|
| Scans API + idempotency | DONE-UNVERIFIED-LIVE | `app/api/v1.py:72-105` requires `Idempotency-Key`; `app/queue/postgres_store.py:24` and `deployment/migrations/001_initial.up.sql:1-6` scope uniqueness to tenant/subject/key. Tests `tests/test_api.py:44,71` and `tests/test_queue.py:158` exist but did not run. | REQUIRED-FOR-PILOT |
| Media upload (image/video/audio) and limits | DONE-UNVERIFIED-LIVE | `app/api/v1.py:54-61`; modality limits/settings in `app/core/config.py` and signature checks in `app/media/storage.py`; synthetic media tests in `tests/test_media.py:24,168-243`. Runtime not verified. | REQUIRED-FOR-PILOT |
| `POST /process` Directus pull facade | DONE-UNVERIFIED-LIVE | `app/api/v1.py:42-52`, `app/compat/directus_facade.py:59-113,247-...`; duplicate/idempotency and fake-server tests in `tests/test_directus_pull.py:56,180`. `WELLAR_DIRECTUS_PULL_ENABLED` defaults off (`app/core/config.py:60`); live enablement is UNVERIFIED. | REQUIRED-FOR-PILOT |
| `POST /api/v1/scans/measurements` | DONE-UNVERIFIED-LIVE | Route `app/api/v1.py:121-140`; strict ranges and extra-field rejection `app/scans/measurements.py:5-17`; contract tests `tests/test_integration_contracts.py:38-59`. Client values are marked unverified by route. | REQUIRED-FOR-PILOT |
| Rule indicator: four states + `insufficient_quality`, config validation + hash | DONE-UNVERIFIED-LIVE | `app/drowsiness_indicator.py:10-48,82-127`; config includes version/hash and abstention; `tests/test_drowsiness_indicator.py` and `tests/test_drowsiness_indicator_api.py:75` are present. Tests did not run. | REQUIRED-FOR-PILOT |
| Quality gates including pose proxy | DONE-UNVERIFIED-LIVE | `app/drowsiness_indicator.py:92-114` gates face ratio, lighting, blur, temporal coverage, pose proxies and audio quality; `app/media/extractors.py` computes inputs. Synthetic pose/quality tests exist under `tests/test_drowsiness_indicator.py` and `tests/test_media.py`; not run. | REQUIRED-FOR-PILOT |
| Personal baseline + freshness | DONE-UNVERIFIED-LIVE | `app/evidence/baseline.py`; `app/drowsiness_indicator.py:51-80,126-127`; per-user history lookup is in `app/queue` stores. Test `tests/test_personal_baseline.py` exists; not run. | NICE-TO-HAVE |
| Labels (KSS/fatigue/focus), consent, withdrawal, retention purge, migrations 001–004 | PARTIAL | KSS/fatigue/focus schema `app/scans/labels.py`; consent, withdrawal and retention enforcement routes in `app/api/v1.py` (label route starts at line 142); purge methods in `app/queue/sqlite_store.py:507-510` and `app/queue/postgres_store.py:248-251`; one-shot purge command in `app/media/retention.py:10-24`; migrations `deployment/migrations/001-004.*.sql`. Storage requires consent and retention configuration. No periodic scheduler/deployment invocation for `python -m app.media.retention` was found; expiry purge is implemented but recurring operation is UNVERIFIED. Tests `tests/test_drowsiness_indicator_api.py:14-72` exist but did not run. | REQUIRED-FOR-PILOT |
| Legacy projection to `scan_results` | DONE-UNVERIFIED-LIVE | `app/compat/legacy_projection.py:9-39`; facade calls projection/write in `app/compat/directus_facade.py`; tests `tests/test_integration_contracts.py:26-36`. Live Directus field behavior not verified. | REQUIRED-FOR-PILOT |
| Delete V2 copies AND Directus files + `deletion_proven` | DONE-UNVERIFIED-LIVE | Local/remote deletion and proof flow in `app/compat/directus_facade.py` (write and cleanup paths); persistent pending state/reconciliation methods in same module and `app/queue/postgres_store.py`; tests `tests/test_directus_pull.py:108,299`. No live Directus/S3 proof in this audit. | REQUIRED-FOR-PILOT |
| Reconciliation sweeper | DONE-UNVERIFIED-LIVE | `app/media/reconciliation.py:34-49`; startup and five-minute loop `app/workers/worker.py:35-61,149-...`; test `tests/test_media_reconciliation.py:39`. Live sweep not verified here. | REQUIRED-FOR-PILOT |
| Isolated killable decode (timeout/RSS) | DONE-UNVERIFIED-LIVE | Child uses `[sys.executable, "-m", "app.media.isolated_decode", ...]` at `app/media/isolated_decode.py:70-78`; RSS uses `psutil` first and POSIX `/proc` fallback (`:50-68`); config names/defaults `WELLAR_DECODE_TIMEOUT_SECONDS`, `WELLAR_DECODE_MEMORY_LIMIT_MB` in `app/core/config.py:109-110`; analysis call at `app/media/analysis.py:147-148`. Windows-only `tasklist` fallback is inside `_rss_bytes` only when psutil missing and `os.name == "nt"`; it is not on the Linux branch. Tests `tests/test_isolated_decode.py:29-61` exist. Linux/container execution was blocked by Docker daemon unavailable. | REQUIRED-FOR-PILOT |
| Graceful drain/shutdown | PARTIAL | SIGTERM sets stop event and closes processor/stores in `app/workers/entrypoint.py:37-40`; bounded analysis/drain setting is `WELLAR_WORKER_DRAIN_SECONDS` (`app/core/config.py:43,111,154-155`) and passed to processor (`entrypoint.py:32`). Worker loop stops claiming when stop is set (`app/workers/worker.py:149-...`). No running-worker SIGTERM test was executed; actual container signal behavior is UNVERIFIED. | REQUIRED-FOR-PILOT |
| Rate limiting + 429/503 `Retry-After` | PARTIAL | Per-process tenant limiter `app/security/rate_limit.py:8-...`; request path returns 429 and `Retry-After` in `app/api/v1.py:34-39`; app maps 429/503 headers in `app/main.py:81-84,100-104`. Limiter is in-memory, not shared across API replicas; queue saturation/429 tests exist (`tests/test_reliability_hardening.py:9-27`) but did not run. | NICE-TO-HAVE |
| Auth modes (Directus/OIDC) and tenant isolation | DONE-UNVERIFIED-LIVE | Mode validation/production restriction in `app/core/config.py:57-... ,158-186`; authenticator selection in `app/main.py:63-68`; tenant/subject in `app/security/auth.py`; scan ownership in API/store. Tests `tests/test_directus_auth.py`, `tests/test_jwt_auth.py`, and tenant-scoped queue tests exist. Live identity provider and tenant claims UNVERIFIED. | REQUIRED-FOR-PILOT |
| Health/ready/metrics | PARTIAL | `/health`, `/metrics`, `/ready` in `app/main.py:112-160`; metrics expose bounded counters/snapshots (`app/observability/metrics.py:16-...`). Current implementation is counters, not latency histograms; no queue oldest-age or DB pool saturation metric was found. | REQUIRED-FOR-PILOT |
| Structured logs without PII | PARTIAL | Structured event helper `app/core/logging.py`; worker pseudonymizes tenant ref in `app/workers/worker.py:95`; Directus sanitized error logging tests in `tests/test_directus_pull.py:315-...`. Worker logs still include raw `scan_id` and `job_id` (`worker.py:99,118-141`), so the broader requirement that identifiers be minimized/pseudonymized is not met. Log tests were not run. | REQUIRED-FOR-PILOT |
| OpenAPI + drift test | DONE-UNVERIFIED-LIVE | Checked-in `openapi.json`, generator `tools/export_openapi.py`; exact runtime schema comparison test `tests/test_integration_contracts.py:66-69`. No generation/drift check executed because import dependency is missing. | REQUIRED-FOR-PILOT |
| Docker/EasyPanel kit | PARTIAL | `Dockerfile.easypanel`, `Dockerfile.worker.easypanel`, `compose.easypanel.staging.yml`, `EASYPANEL_DEPLOYMENT.md`; image and runtime verification not possible: Docker CLI exists but daemon pipe is absent (`docker images` command output: cannot connect to Docker Desktop Linux engine). | REQUIRED-FOR-PILOT |
| `tools/verify_deployment.py` | DONE-UNVERIFIED-LIVE | Read-only behavior is documented in its module docstring; checks health/readiness/OpenAPI/measurement route. The four `V2_VERIFY_*` names were absent from the current shell; it was not run. | REQUIRED-FOR-PILOT |
| Docs (README/RUNBOOK/PRIVACY/THRESHOLDS/INTEGRATION/DIRECTUS_SERVICE_ROLE) | DONE-UNVERIFIED-LIVE | Files exist: `README.md`, `docs/RUNBOOK.md`, `docs/PRIVACY.md`, `docs/THRESHOLDS.md`, `docs/INTEGRATION.md`, `docs/DIRECTUS_SERVICE_ROLE.md`; contents inspected for deployment, scope, threshold limitations and data handling. Operational accuracy is not live-verified. | REQUIRED-FOR-PILOT |

## B. Fix-prompt check: Directus changes

| Requirement | Result | Evidence / exact smallest change if missing |
|---|---|---|
| 1. `write_result` emits compact JSON string for `internal_analysis`, retaining `deletion_proven` | PRESENT | `app/compat/directus_facade.py:142-160` uses sorted compact `json.dumps` and sets deletion flag; `tests/test_directus_pull.py:220-226` asserts the encoded string and flag. Explanation is generated through the projection path (`legacy_projection.py:34-38`). Local test not run. |
| 2. Every Directus non-2xx logs status, endpoint, error code, extension field names only; distinct validation code | PRESENT IN SOURCE; TEST UNRUN | Sanitizer/request handling at `app/compat/directus_facade.py:186-231`; 400/422 maps to `directus_validation_error` (`:221-229`); redaction test at `tests/test_directus_pull.py:315-...`. Test suite unavailable. |
| 3. Permanent failures terminalize V2 scan, set Directus failed best-effort, delete Directus file records; transient retries preserve files | PRESENT IN SOURCE; TEST UNRUN | Permanent branch and `_terminal_failure` around `app/compat/directus_facade.py:247-...`; transient HTTP exceptions are retryable and flow to worker retry (`app/workers/worker.py:128-141`). Fake-client tests cover permanent lookup/write failures and transient Directus down (`tests/test_directus_pull.py:124-135,249-...`). Tests not run. |
| 4. Tests for 1–3 | PRESENT; NOT EXECUTED | `tests/test_directus_pull.py:180-226,249-... ,299-... ,315-...`; full safe test load failed before collection because `python-dotenv` is absent. |

No code change is recommended from this checklist based on the checked-in source. Verification remains outstanding.

## C. Worker and container audit

- **Entrypoint/start order:** `app/workers/entrypoint.py:18-40` loads and validates settings, configures logs, imports NumPy before starting a worker lease, creates queue and media adapters, loads predictors/classifier, builds processor, optionally wraps it in Directus pull, installs SIGINT/SIGTERM handlers, then enters `run_forever`.
- **Startup reconciliation:** `Worker.__init__` calls media and Directus deletion reconciliation (`app/workers/worker.py:35-61`). Media reconcile repeats every 300 seconds in the loop (`:149-...`).
- **Lease/heartbeat/retry:** Worker claims a job and starts lease heartbeat every `lease_seconds/3` (`app/workers/worker.py:73-111`). Queue retry cap is `max_attempts` (`app/queue/postgres_store.py:24,207-...`); terminal failures are handled after max attempts. Poison JSON is converted to `{}` and then terminal processing failure (`worker.py:78-82,113`).
- **Important retry/deletion interaction:** exception handling calls `_delete_payload_media` before deciding whether the job will retry (`app/workers/worker.py:128-136`). This can erase media needed by a later retry; `tests/test_media.py:101` expects retention through retries, but the complete safe suite did not run. Treat as a REQUIRED-FOR-PILOT code-path risk until that test is run and the actual behavior is reconciled. Smallest fix, if confirmed: delete media only after successful processing or terminal failure, and preserve assets while retryable.
- **Drain:** SIGTERM stops new loop iterations; current `run_once` is synchronous and decode/analysis has configured deadlines. `WELLAR_WORKER_DRAIN_SECONDS` defaults to 40 and is passed as analysis timeout (`app/core/config.py:43,111`; `entrypoint.py:32`). A container-level bounded drain was not tested.
- **Decode:** Linux child invocation uses `sys.executable -m app.media.isolated_decode` and the platform-independent RSS path uses `psutil`, otherwise `/proc` (`app/media/isolated_decode.py:50-68,70-108`). No Windows-only command runs on the Linux path. Limits: `WELLAR_DECODE_TIMEOUT_SECONDS` (default 12), `WELLAR_DECODE_MEMORY_LIMIT_MB` (default 768), `WELLAR_WORKER_DRAIN_SECONDS` (default 40), `app/core/config.py:41-43,109-111`.
- **Dependency outage behavior:** Postgres queue/pool initialization and readiness are required (`app/main.py:...`; `app/core/adapters.py`); failure stops startup/readiness. S3 adapter operations raise storage errors and are retried/failed through worker and deletion retry paths (`app/media/reconciliation.py:12-31`). Directus transient errors are retryable; permanent classes terminalize (`directus_facade.py:221-261`). These branches were read, not exercised against services.
- **Container fitness:** Docker build/reuse/import/decode checks NOT RUN. `docker --version` succeeded (`Docker version 29.8.2`), but daemon access failed: `failed to connect to the docker API at npipe:////./pipe/dockerDesktopLinuxEngine`. Therefore `cv2`, `mediapipe`, `app.media.isolated_decode` imports inside the production image, synthetic decode, and missing native library diagnosis are all UNVERIFIED. Image tags were not enumerable without the daemon.

## D. Safe tests

Safe suite command attempted (research/training/dataset/pilot/real-media and short-mobile KSS tests excluded):

```text
rtk proxy pytest -q --ignore=tests/test_dataset_qualification.py --ignore=tests/test_ml_training_pipeline.py --ignore=tests/test_owned_pilot_workflow.py --ignore=tests/test_real_media_acceptance.py --ignore=tests/test_research_drowsiness_worker.py --ignore=tests/test_research_evaluation.py --ignore=tests/test_research_image_drowsiness.py --ignore=tests/test_experimental_drowsiness.py --ignore=tests/test_short_mobile_kss.py
```

Result: **0 passed, 0 failed, 0 skipped; collection aborted** at `tests/conftest.py:4` because `ModuleNotFoundError: No module named 'dotenv'` (`app/core/config.py:19`). This is an environment prerequisite failure, not a passing suite.

Synthetic AVI test `tests/test_ai_engine_foundation.py::test_video_extracts_synthetic_avi_with_sampling` was attempted five times. All five failed during the same conftest import before test collection, for missing `python-dotenv`; therefore flakiness is UNVERIFIED (there were no test executions).

No tests were edited. No test result can be represented as passed in this audit.

## E. Read-only live check

`V2_VERIFY_BASE_URL`, `V2_VERIFY_ALLOW_STAGING`, `V2_VERIFY_ALLOWED_HOST`, and `V2_VERIFY_BEARER_TOKEN` were absent from this shell (checked by printing names only). Per instruction, `tools/verify_deployment.py` was not run. Live verification is **OWNER ACTION**: provision the allowed staging verification variables in a protected local shell, then run `python tools/verify_deployment.py` from the V2 root. Do not paste or print the bearer token. This tool is read-only and does not upload media or create labels.

## Pilot GO/NO-GO

**NO-GO based on this audit.** This is an evidence gate, not a claim that all features are absent. Exact blockers:

1. The safe suite and all targeted tests are unverified because the local interpreter lacks the declared `python-dotenv` dependency; no tests executed.
2. Worker exception cleanup deletes payload media before retryability is decided (`app/workers/worker.py:128-136`), which risks breaking retry attempts; the test asserting retry retention (`tests/test_media.py:101`) could not run.
3. Docker engine is unavailable, so the intended image's OpenCV/MediaPipe imports and synthetic isolated decode cannot be verified.
4. Live verification variables were not set; API, worker, Directus, S3 deletion, migration level, auth, and deployment behavior were not independently checked.
5. Label expiry purge is implemented as a one-shot command (`app/media/retention.py:10-24`), but a periodic scheduler/deployment invocation was not found; recurring expiry is not established.
6. Metrics are counters, not latency histograms, and do not expose queue oldest age or DB pool saturation; production observability is incomplete.
7. Worker logs emit raw scan/job IDs (`app/workers/worker.py:99,118-141`), so the strict log-minimization requirement is not met.

## Nice-to-haves vs later

- **Nice-to-have:** replace the in-process rate limiter with a shared backend before running multiple API replicas (`app/security/rate_limit.py:8-25`).
- **Later:** no additional modeling or training scope is recommended by this audit. Rule states remain indicative, non-diagnostic, and not a validated classifier; do not promote research weights or claim fatigue/focus accuracy.

## What is NOT validated

- Any live API, worker, Directus, PostgreSQL or S3 behavior during this audit.
- Docker image build, `cv2`/`mediapipe` runtime imports, native system libraries, or decode inside the image.
- Full safe test behavior, AVI reliability, concurrency, timeout behavior under a real container, or shutdown under active work.
- Directus permanent/transient cleanup behavior against a real Directus service; object deletion on real S3/MinIO.
- Live auth configuration, tenant isolation, label expiry scheduling, deployment migration state, or environment configuration.
- Scientific validity or calibration of thresholds/states; no scientific evaluation was performed.

## Ready-to-paste Codex prompts — REQUIRED-FOR-PILOT gaps only

### 1. Verify the media retry lifecycle

```text
In D:\flutter\last\wellar-ai-v2, inspect app/workers/worker.py retry/error cleanup. Fix the confirmed risk that media_refs are deleted before retryable is computed, while preserving cleanup on success and terminal failure. Back up every existing file before editing. Do not edit tests just to pass; update/add focused tests only to encode intended behavior and list changed tests. Run the focused test plus the safe suite. Do not use pilot media, training, datasets, deployed services, or modify anything outside V2. Report evidence and files changed.
```

### 2. Establish reproducible local verification

```text
In D:\flutter\last\wellar-ai-v2, provision only an isolated local test environment from the checked-in pinned dependency manifest (do not alter project dependencies unless a real defect is proven). Run the safe suite excluding research/training/dataset/pilot/real_media tests, then run tests/test_ai_engine_foundation.py::test_video_extracts_synthetic_avi_with_sampling five sequential times. Report passed/failed/skipped and exact environment errors. Do not access deployed services, secrets, private media, or training data. Do not change tests merely to pass.
```

### 3. Prove the production-style image runtime

```text
In D:\flutter\last\wellar-ai-v2, build/reuse Dockerfile.worker.easypanel and Dockerfile.easypanel using the local Docker daemon only. Inside each relevant image, run import checks for cv2 and mediapipe plus a tiny fully synthetic fixture through app.media.isolated_decode; report missing native libraries and exit codes. Do not contact deployed endpoints or include private media, research weights, or credentials. Make no code changes unless an image defect is demonstrated; back up files before any edit.
```

### 4. Complete only the outstanding pilot operational proofs

```text
In D:\flutter\last\wellar-ai-v2, inspect how the one-shot retention command (python -m app.media.retention) is scheduled in the deployment kit, and inspect worker log identifier handling. Implement only the missing required behavior: provide a safe periodic invocation for retention purge and minimize/pseudonymize scan/job identifiers in logs without breaking traceability. Back up each file before its first edit; do not touch tests to make them pass, list any tests changed and why. Run focused tests and the safe suite. Do not access production, deployed services, pilot media, datasets, or training. Report evidence and remaining owner actions.
```

### 5. Obtain permitted read-only staging evidence

```text
From the V2 workspace, after the owner has placed V2_VERIFY_BASE_URL, V2_VERIFY_ALLOW_STAGING, V2_VERIFY_ALLOWED_HOST, and V2_VERIFY_BEARER_TOKEN in a protected shell, run only python tools/verify_deployment.py. Never echo, log, or include secret values. Do not upload media or create labels. Report PASS/FAIL per check and redact authorization material.
```
