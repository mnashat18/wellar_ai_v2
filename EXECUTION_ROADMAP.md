# Wellar AI V2 — Dependency-Aware Execution Roadmap

This roadmap orders completion packages by prerequisite and release risk. Each package ends at reviewable evidence. It makes no schedule, staffing, capacity, or scientific performance estimate. Do not activate operational predictions or claim 2,000-scan readiness until their separate gates pass.

## Package 1 — Stabilize database and media lifecycle correctness

**Checklist:** QUE-03, MED-04, QUE-01.  
**Status:** Package implementation and scoped local/disposable integration checks complete. QUE-03 is closed for code correctness; QUE-01 and MED-04 remain PARTIAL at project level.  
**Scope:** Reproduce the reconciliation cutoff boundary failure deterministically; define an exclusive timestamp cutoff so exact ties defer to the next cycle; test arrivals during a cycle; verify lease-fenced metadata cleanup. Trace retryable processing failures and post-result object deletion failure.  
**Evidence:** Deterministic SQLite cutoff/repeated-cycle and cleanup tests; local reconciliation/media/queue suite 71 passed; storage/PostgreSQL contract suite 23 passed; guarded Sprint 08 PostgreSQL/MinIO suite 14 passed. The first integration attempt found cross-test synthetic-reference contamination in the new boundary fixture; fixture cleanup was added and the full guarded suite passed on rerun. No migration or service change was made.  
**Remaining at project level:** QUE-01 migration upgrade/rollback/restore and broader worker lifecycle evidence; MED-04 scheduled retention and full real-storage fault matrix. Keep synthetic data only.

## Package 2 — Freeze identity, consent, and V2 API contract

**Status:** PARTIAL implementation; release acceptance remains blocked. The Flutter V2 client transmits the user's operational consent choice/version, blocks declined or unsupported consent, and sends research consent as false. The backend requires the supported version for media/reaction submissions and rejects research opt-in. Backend focused tests: 111 passed, 3 warnings, exit 0; guarded Sprint 08 PostgreSQL/MinIO: 14 passed, 2 warnings, exit 0. Blocker follow-up found an orphaned two-hour Flutter test process and that direct `rtk proxy flutter.bat` invocation stalls; launching the batch via `cmd.exe /d /c` works. After stopping only the confirmed orphan (PIDs 13024/17752), V2 client tests passed 18/18, auth-journey widget tests 5/5, and scan result/display/poll/failure tests 17/17 (all exit 0). The stale generic splash test expectation was updated to match the implemented `PublicEntryScreen` route; it passes 1/1. No dedicated V2 processing/result screen widget test exists. Targeted V2 analysis passes with no issues; full analyzer finishes in 29 seconds with 0 errors, 106 warnings, 209 infos (315 total), exit 1. Android debug APK built successfully, exit 0; `flutter doctor -v` exits 0 but reports unaccepted Android SDK licenses. Flutter currently forwards Directus access tokens and refreshes through Directus. Backend Directus mode validates `/users/me` and active membership online, failing closed, but denies multiple active memberships. Scalable Compose instead hardcodes OIDC and requires issuer/audience/JWKS settings. **Identity options:** (1) Directus verification is the least disruptive immediate fit: V2 passes through the Directus user token, validates it and active membership on every request, and derives tenant from Directus; it requires HTTPS, correct per-user membership permissions, and either exactly one active membership or a new server-verified selection contract, and it couples API request availability/load to Directus. (2) A trusted V2 token exchange could validate Directus once and issue short-lived V2 credentials, but adds an issuer, signing-key management, refresh/revocation rules and another service. (3) Shared OIDC provides the cleanest long-term token contract, but requires Flutter login/session changes and an approved issuer/audience/JWKS/tenant-claim configuration. Recommendation: use existing Directus verification as a bridge only if Directus is approved as V2 authority and the membership policy is confirmed; otherwise adopt shared OIDC only after actual provider details are supplied. No token exchange or OIDC configuration has been invented. Operator confirmation and isolated staging verification remain required; Package 3 should wait. No production identity, Directus or EasyPanel resources changed.

**Final identity closure update (2026-10-20):** Flutter supports multiple active workspaces and persists a selected membership ID; this is client UI state, not an authorization proof. Backend Directus mode verifies `/users/me?fields=id,status` and an active membership collection read using the caller token, derives both IDs server-side, and fails closed when membership count is zero or greater than one. Least-privilege policy requirements and an isolated staging template/acceptance note are recorded in `deployment/staging/`. Synthetic backend identity/API tests, including 12 concurrent independent identities, passed: 50 passed, 3 warnings, exit 0. Two bounded-in-intent Flutter invocations emitted no output and were stopped (exit 1); environment access denied process enumeration, so no new Flutter verification is claimed. The scalable Compose file remains OIDC-only; no provider, service, or live configuration was changed. Do not close Package 2 or start Package 3 until the owner chooses multi-organization policy and supplies/approves isolated staging configuration and service validation. No identity caching is justified without staging measurements and an explicit revocation-delay bound.

**Checklist:** AUTH-01/02, TEN-01, CON-01, API-01/02, FLU-01.  
**Dependencies:** Package 1 only for reliable cleanup; approved identity/privacy decisions.  
**Scope:** Agree issuer/token source and membership claims for Flutter and V2; version API schemas and error semantics; record and send consent choices rather than hardcoding them; test idempotent ambiguous retries and tenant-owner enforcement across API and client. Add OpenAPI drift and client/server contract gates.  
**Done when:** the same synthetic account can log in, refresh, select a tenant, upload synthetic supported media, submit, poll and retrieve a result; denied/expired/cross-tenant cases fail safely; consent and retry records match user choice; contract suite passes.

## Package 3 — Close core backend and worker failure matrix

**Checklist:** QUE-01/04, MED-01/03/04, OPS-01, QA-01.  
**Dependencies:** Packages 1–2.  
**Scope:** Complete PostgreSQL migration/restore lifecycle, multi-worker lease/retry/crash tests, decoder timeout/memory limits, S3 registration/compensation/cancellation, graceful drain, startup failure visibility and retention/cleanup operations. Reverify the actual Sprint 08 PostgreSQL/MinIO path only through the established disposable guards.  
**Done when:** disposable integration and deterministic fault-injection suites establish one valid claim owner, at-least-once retry safety, no duplicate result commit, bounded decode/upload work, no untracked admitted transfer, and recovery/cleanup across worker/API restarts.

## Package 4 — Establish production security, privacy, and operations controls

**Checklist:** SEC-01, PRIV-01, OBS-01, INF-01.  
**Dependencies:** Packages 1–3; approved production architecture.  
**Scope:** Build the new isolated EasyPanel V2 plan without touching existing services; pin images; configure managed secrets, TLS, private storage/IAM, encryption, database pool/resource limits, migration/rollback, backup/restore, alerting, audit and retention runbooks. Review API exposure and metric/log privacy.  
**Done when:** a staging rehearsal in isolated resources proves deployment, migration, health/readiness, worker capacity signals, backup restore, rollback and deletion/retention operations. Production changes require their own explicit release authorization.

## Package 5 — Make Flutter V2 journeys releasable

**Checklist:** FLU-01/02/03, UX-01, CON-01/02.  
**Dependencies:** Package 2 contract and Package 4 staging identity/service.  
**Scope:** Complete login/session expiry/logout, consent UI, camera/image/video/audio conversion, upload cleanup, submission idempotency, polling, result/abstention, network cancellation and recovery. Add platform-specific tests/builds and preserve separate Directus workflows only where explicitly intended. Implement separate opt-in research consent only after governance and data handling are approved.  
**Done when:** V2 client contract suite passes, signed Android and iOS builds are reproducible, supported real devices pass permission/capture/network/lifecycle tests with synthetic accounts/media, and privacy/result content is accepted.

## Package 6 — Complete the scientific validation program

**Checklist:** SCI-01/02/03 and CON-02.  
**Dependencies:** scientific protocol, ethics/privacy/legal approvals, participant/data rights and approved funding/operations; can proceed as a governed parallel workstream after Package 2.  
**Scope:** Define separate KSS, subjective-fatigue and PVT/vigilance reference targets; preregister acquisition, exclusions, missingness, power and acceptance criteria; qualify data provenance/licenses and consent; build participant-, site- and device-disjoint locked evaluation. Validate calibration, abstention, uncertainty, subgroup robustness and generalization. Keep inference disabled unless independent reviewers approve the exact artifact and claim.  
**Done when:** independent scientific review signs a target-specific report against preregistered acceptance criteria and the release guard accepts only that reviewed artifact. Software test success alone is insufficient.

## Package 7 — Measure and engineer the capacity envelope

**Checklist:** MED-02, OPS-02, OBS-01, QUE-04.  
**Dependencies:** Packages 1–4; stable synthetic end-to-end contract; approved test environment and resource budget.  
**Scope:** Define end-to-end start/stop timestamps, representative synthetic modality/size distribution, completion/error SLO, retry budget, network placement and cost accounting. Validate queue/worker, Postgres pool, S3 throughput, upload admission, rate-limit sharing, API replicas, CPU/RAM/GPU and cold/warm behavior in a distributed load environment. Start at bounded cohorts and increase only with safe headroom; do not run 2,000 users locally.  
**Done when:** repeatable external load evidence reports full latency distributions, initial rejects separately from final completion, queue/stage times, throughput, retries, connection/resource/network use and costs. Only claim 2,000 concurrent scans within 10 seconds if the precise agreed acceptance test passes.

## Package 8 — Final release review

**Checklist:** all release matrix gates.  
**Dependencies:** Packages 1–7 and owner acceptance.  
**Scope:** Run clean-checkout backend and Flutter regression, real disposable integrations, security/privacy review, device matrix, scientific review if prediction claims are present, production-like capacity test and operations rehearsal. Resolve every P0 and document accepted P1/P2 residuals.  
**Done when:** every P0 gate is PASS with linked evidence; no unexplained failure/skip; release owners sign the exact software, model, data, service and claim versions. Otherwise the product remains blocked or explicitly limited to a non-predictive release.
