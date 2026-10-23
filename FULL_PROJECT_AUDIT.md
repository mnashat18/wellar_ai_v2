# Wellar AI V2 — Full Project Audit

**Audit snapshot:** 2026-10-20  
**Scope:** V2 backend repository and the separate Flutter checkout at `D:\flutter_app\wellar-flutter-app`. Static inspection plus the targeted SQLite regression test. No source code, Flutter files, services, databases, deployments, or model artifacts were changed by this audit. Existing dirty worktrees were preserved.

## Executive decision

**Release readiness: BLOCKED.** This repository contains a substantial V2 backend and an evolving Flutter V2 client. It is not a complete production product. The single synthetic Sprint 08 scan completed with `insufficient_evidence` and inference disabled. The bounded HTTP runs at concurrency 1/5/10 completed all 16 scans after upload retries, but they are tiny local cohorts, recorded 8 upload 429s at concurrency 5 and 10, and do not validate 2,000 concurrent end-to-end scans under 10 seconds. No production deployment, real-device acceptance, or scientific fatigue validation has been demonstrated.

## Evidence and limits

- The V2 repository is `D:\flutter\last\wellar-ai-v2`; the Flutter app is the separate `D:\flutter_app\wellar-flutter-app` checkout. The latter has tracked modifications and ignored local artifacts. Its existing work was read-only inspected and preserved.
- Sprint 08 evidence is in `SPRINT_08_DATABASE_WORKER_RECOVERY_SMOKE_20261020_1252.json` and `SPRINT_08_VALIDATION03_2W.json`. Prior confirmed integration execution was 13 passed; current audit did not rerun live integration or inspect/start services.
- Baselines are `CAPACITY_BASELINE_C1_20261020.json`, `CAPACITY_BASELINE_C5_20261020.json`, and `CAPACITY_BASELINE_C10_20261020.json`. Each is a one-repetition synthetic run against a single local Compose host. Resource telemetry and worker stage events are unavailable in these harness artifacts.
- Focused audit verification: `.venv\Scripts\python.exe -m pytest tests/test_media_reconciliation.py::test_terminal_arrival_after_cycle_cutoff_is_deferred_to_next_cycle -q --disable-warnings` — **1 passed, 2 warnings, exit 0** in this run. The supplied confirmed local baseline was 70 passed / 1 failed on that same test. The failure has therefore not been consistently reproduced in the current run; its clock-boundary race remains unresolved.
- Flutter has `test/wellar_v2_client_test.dart` and an opt-in `test/wellar_v2_live_http_test.dart`; Flutter invocation was attempted but did not produce test output before it was interrupted (process exit 1). No Flutter test pass is claimed. No Android/iOS build or physical-device verification was performed.
- Files and status claims below distinguish source/tests from external runtime evidence. The audit did not run full suites, load tests, migration commands, service actions, or deployment checks.

## Architecture inventory

The V2 FastAPI service lives under `app/`, with routes in `app/api/v1.py`, schemas and processing in `app/scans/` and `app/media/`, queue adapters in `app/queue/`, and worker entrypoint in `app/workers/entrypoint.py`. PostgreSQL migrations are in `deployment/migrations/`; local and isolated Sprint 08 Compose profiles are under `deployment/`. The repository also includes a Directus compatibility facade, but this is not the legacy server. Production EasyPanel V2 is documented as a separate, new service; no EasyPanel resources were inspected or modified.

Flutter source is in `D:\flutter_app\wellar-flutter-app`. Its current dirty tree contains a V2 API client, V2 processing/result screens, route changes, and client tests. This is implementation evidence only; it does not establish a released or device-tested app.

## Backend status

| Area | Current status | Evidence / gap |
|---|---|---|
| Authentication | Partial | V2 supports OIDC/JWKS, Directus membership validation, and development token auth. Production issuer, claim mapping, key rotation, session exchange/refresh, and operator configuration are not accepted end to end. See `app/security/auth.py`, `app/main.py`, `deployment/docker-compose.scalable.yml`. |
| Authorization / tenant isolation | Implemented in core paths; release verification incomplete | Principal-derived tenant/subject scope is used for scan/result/media lookups and deletion; cross-tenant tests exist. Production identity/membership and broad negative integration coverage remain release gates. |
| Consent | Partial | Backend requires media-processing consent for scan media and separates `research_use`; label collection has separate training consent, retention and withdrawal routes. Flutter’s current V2 client hardcodes media consent true and research false; no separately consented research/model-improvement UI lifecycle is evidenced. See `app/scans/schemas.py`, `app/api/v1.py`, Flutter `lib/services/wellar_v2_client.dart`. |
| API contracts | Partial | FastAPI routes and generated runtime OpenAPI exist. V2 Flutter client currently implements upload, submit, polling, result and delete, with test fixtures. Versioned client/server drift gate and complete deployed consumer contract are not proven. |
| Media upload / validation | Implemented with bounded local controls; capacity incomplete | Per-modality limits, signatures, MIME checks, quotas, tenant ownership and bounded async S3 executor are present. Baselines recorded upload `429 upload_capacity_exceeded`; ambiguity after lost upload response is delegated to retention/operator reconciliation. |
| Processing | Feature extraction, no validated fatigue inference | Video/image/audio processing and evidence/quality outputs exist. Model prediction remains disabled; result is `insufficient_evidence`. MediaPipe feature extraction is not scientific occupational-fatigue prediction. |
| Queue / workers | Implemented, integration evidence partial | PostgreSQL claims, leases, heartbeat/fencing, retries and independent worker IDs exist. Two workers were healthy after recovery and actual observed `max_running` reached 2 in the prior bounded HTTP run. The later synthetic scan exposed a missing migration table, now known to have been applied per baseline; stable end-to-end reliability still needs repeated verification. |
| Idempotency / persistence | Implemented in tested code paths; production reliability unproven | Scan idempotency key, durable queue/result writes, owner-scoped result retrieval and retry-safe processing are implemented. The HTTP artifacts do not include comprehensive crash/replay or duplicate-commit evidence at scale. |
| Media lifecycle | Partial | Normal worker deletion, terminal reconciliation, quota cleanup, retention command, training-label expiry and withdrawal exist. Retention scheduling/alerting and all orphan/failure paths are not operationally accepted. |
| Migrations | Partial | Sprint 08 disposable PostgreSQL migrations 1–5 were reported applied successfully and integration suite reported 13 passed. Production migration ownership, backup/restore, rollback and upgrade rehearsals are not established. |
| Health / readiness | Implemented locally; deployment readiness unproven | API `/health`/`/ready`, PID-specific worker readiness markers, startup warm-up and health checks exist. No production monitoring or availability SLO is evidenced. |
| Observability / operations | Partial | Structured logs and `/metrics` counters/queue diagnostics exist. Durable metrics export, alerting, dashboards, on-call, incident recovery, backup/restore and resource telemetry are missing or operator-dependent. |

## Flutter readiness

Current Flutter source has V2 endpoint configuration and a distinct V2 client (does not call legacy processing from the V2 route), media-signature/size checks, bearer session use, same-key submission replay, status polling, result parsing, upload cleanup and a synthetic/opt-in live HTTP test. The scan flow includes a privacy/consent step and hands video/audio/thumbnail to the V2 processing screen. These are source-level findings; existing Flutter modifications were not made by this audit.

Open release gaps: no successful Flutter test run was obtained in this audit; no real Android/iOS build or device run; no verified release signing/provisioning and store submission evidence; no lifecycle/network interruption acceptance on devices; no production identity contract proving Directus-issued token plus active tenant membership works against the separate V2 service; no API contract CI gate. Current V2 submission parameters for consent are accepted by the client method but payload uses hardcoded `media_processing=true` and `research_use=false`; consent UI state is checked before navigation. This coupling needs explicit product/privacy acceptance and tests. Android camera/mic/Internet permissions and iOS camera/mic descriptions are present, but platform behavior and permission-denial recovery are not device verified.

**Flutter status: PARTIAL; not release-ready.** No claim is made that authentication/session expiry, capture fidelity, media conversion on all devices, logout, retry UX, or result semantics are accepted in production.

## AI and scientific readiness

The operational worker is deliberately configured to return unavailable targets and `insufficient_evidence` absent a separately validated release. Current output can include deterministic image/video/audio quality and feature extraction plus an explicitly indicative, non-diagnostic drowsiness indicator. The repository’s `research/README.md` and `P0_AI_EVALUATION.md` state no authorized Wellar participant-labeled dataset or validated fatigue model is present. Research training artifacts are candidate-only and guarded from operational activation.

No occupational fatigue model accuracy, sensitivity, calibration, subgroup robustness, abstention coverage or external generalization is established. KSS/sleepiness, subjective fatigue, PVT vigilance, observed drowsiness and operational risk are distinct constructs. Required study items include rights and ethics approval, qualified instruments/timing, independently powered sampling, participant/site/device-disjoint locked evaluation, leakage checks, confidence intervals, calibration and preregistered decision/abstention criteria. No scientific metric is inferred from software tests or third-party model cards.

**Scientific status: BLOCKED for predictive fatigue claims or production prediction.** Feature extraction is not model validation.

## Performance and infrastructure readiness

The bounded baseline evidence (single repetitions, local disposable stack, synthetic media) is:

| Concurrency | Completed | Initial upload 429 | End-to-end submit-to-terminal P50/P95/P99 | Throughput | Max observed running |
|---:|---:|---:|---:|---:|---:|
| 1 | 1/1 | 0 | 3,634 / 3,634 / 3,634 ms (n=1) | 0.242 scans/s | 1 |
| 5 | 5/5 after retries | 2 (audio 1, image 1) | 511 / 2,099 / 2,099 ms (n=5) | 1.915 scans/s | 2 |
| 10 | 10/10 after retries | 6 (audio 1, image 4, video 1) | 795 / 1,069 / 1,069 ms (n=10) | 4.778 scans/s | 1 in sampled trace |

HTTP retry counts were 0/2/6 for concurrency 1/5/10. All scans ultimately completed; initial overload was not zero. The separate two-worker 1/5/10 validation artifact reports 16/16 completed, six image upload 429s, and sampled `max_running=2`; submit-to-terminal P50/P95/P99 across all 16 observations were 584 / 1,974 / 1,974 ms. The later recovery smoke had one accepted/completed scan, no 429/retry, terminal latency 3,844 ms and result visible at 3,854 ms; result was `insufficient_evidence`, inference disabled. Sample sizes and harness limitations prevent capacity inference.

No CPU/RAM by container, PostgreSQL connection count, true stage-level worker latency, or production network throughput is recorded in these artifacts. No 2,000-scan run exists. At the measured small cohorts upload admission is already rejecting initial requests; target design still requires multi-host/shared rate-limit/admission choices based on capacity testing. Worker scaling alone does not satisfy the target.

The EasyPanel Compose file requires PostgreSQL, private S3, OIDC, TLS and explicit resource limits, but its README describes an operator kit, not an active verified service. Production V2 must be a new isolated service. SQLite remains single-host development only. Production backup, failover, resource sizing, autoscaling, GPU/CPU placement, SLO monitoring, disaster recovery and cost remain unverified.

**Performance / infrastructure status: BLOCKED.** The 2,000 concurrent / 10-second target is not validated and should not be represented as close based on these small-cohort numbers.

## Security and privacy

Positive implementation evidence includes signed-token verification paths, active Directus membership checks, principal-bound tenant/subject media and result access, MIME/signature/size validation, private media stores, non-public object handling, label consent/expiry/withdrawal paths, structured log redaction, and a separate opt-in research boundary. Remaining acceptance work includes external identity and tenant isolation integration tests, encryption and key/secret lifecycle evidence, access audit and retention operations, worker/API least privilege, backup encryption/restore, security review of decode/resource abuse, and privacy review of logs and identifiers.

Ordinary scan media must remain on its operational retention/deletion lifecycle. Research/model-improvement data handling requires separate explicit consent, purpose, rights, minimized feature-only storage, retention/withdrawal and governed access; it must not be inferred from scan-processing consent. The optional research/model-improvement consent workflow remains a checklist item.

## Confirmed failures and technical debt

The supplied current baseline is **70 passed, 1 failed** in the local suite. The failing test is `test_terminal_arrival_after_cycle_cutoff_is_deferred_to_next_cycle` in `tests/test_media_reconciliation.py`. In this audit’s focused rerun it passed (1 passed, 2 warnings, exit 0). The implementation records a cycle cutoff with wall-clock seconds and selects terminal scans with `updated_at <= cutoff`; test completion updates `updated_at` using the same wall clock during the same cycle. A scan completing on an equal/coarse clock tick can fall on the cutoff side nondeterministically. This is not enough evidence to call it merely a test defect or a deterministic product defect: classify it as an **unresolved timing-boundary race with a known prior failure and a current pass**. Do not silently count the whole local suite as passing.

Other confirmed release gaps are the upload 429s under the measured small cohorts, lack of scientific model validation, absence of 2,000-scan testing, incomplete production observability/DR evidence, and absence of physical-device acceptance. Historical records also show a worker crash/scan timeout before migration 5 was applied; after the disposable migration and worker recovery one synthetic scan completed. These historical failures should not be conflated with the current post-recovery smoke outcome.

## Production blockers (P0)

1. **P0 scientific claims:** No independently validated occupational-fatigue predictor. Keep prediction disabled and labels non-diagnostic until scientific gates pass.
2. **P0 capacity/reliability:** No validated 2,000-concurrent / 10-second full pipeline; small cohorts show upload admission 429s and no resource telemetry.
3. **P0 production service readiness:** No verified isolated EasyPanel V2 service, production identity integration, backup/restore, migrations, alerting, incident/rollback or resource sizing evidence.
4. **P0 mobile release:** Flutter V2 path is dirty source-level implementation only; no passing audited Flutter suite, Android/iOS build/device acceptance, or app release evidence.
5. **P0 privacy/security acceptance:** Consent-to-data lifecycle and production controls have not been independently accepted end to end; research-use opt-in remains separate and incomplete.
6. **P0 queue/media reliability:** The reconciliation cutoff race has a confirmed prior failure and current pass but no deterministic resolution; media orphan/retention operational guarantees remain incomplete.

## Overall disposition

**BLOCKED for production release and for the stated capacity/scientific acceptance.** Backend foundations and one synthetic end-to-end path are implemented; the app has a V2 path in progress; two disposable workers have been observed healthy and competing for jobs in earlier tests. Those facts are not production readiness. See the checklist and gate matrix for testable completion criteria.
