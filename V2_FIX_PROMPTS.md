# Scoped Codex Fix Prompts for Wellar V2

These prompts correspond to the BLOCKER and HIGH findings in `V2_FULL_AUDIT.md`. Each prompt is intentionally bounded. Do not change Flutter, Directus, or Legacy; do not use private-pilot media, perform dataset searches, or train a model. Back up any owner-modified file before editing it. Use synthetic fixtures for tests.

## F-01 — BLOCKER: prove media deletion on failure

```text
In D:\flutter\last\wellar-ai-v2, fix the V2 media lifecycle so media and temporary files are deleted after successful processing and after all terminal/failure paths. Trace upload, scan acceptance, queued/running/poison jobs, media resolve/download, decoder exception, object deletion failure, worker crash/restart, local disk, S3/MinIO, and scratch directories. Do not touch Flutter, Directus, or Legacy. Do not open private-pilot media or run dataset searches/training. Before editing any owner-modified file, create a timestamped backup preserving its relative path. Add failure-injection tests for missing/unresolvable references, malformed poison payload, decode exception, delete exception, and restart orphan cleanup. Do not claim deletion is guaranteed unless tests prove each owned storage layer. Preserve tenant/subject authorization. Run only the focused safe tests and report changed files, test totals, and remaining unverified external-store behavior.
```

## F-02 — BLOCKER: consent withdrawal and retained-feature expiry

```text
Implement the missing V2 API-only lifecycle for KSS training labels and consent. Add authenticated, tenant/subject-scoped withdrawal that deletes retained feature rows and an expiry job for labels/features according to a configurable retention period. Preserve the consent-off behavior (no persistence), retain only an explicit feature allowlist (never media), and make withdrawal idempotent. Align SQLite and PostgreSQL migrations/store behavior, including down migrations and parity constraints. Do not change Flutter, Directus, or Legacy. Do not run training, dataset searches, or use private-pilot media. Back up owner-modified files before editing. Add tests for consent on/off, repeated labels, withdrawal, expiry, unauthorized IDs, and deletion in each supported backend. If no owner-approved retention duration is available, use an explicit required setting and fail closed rather than inventing a policy. Publish OpenAPI updates and report live DB coverage limitations.
```

## F-03 — BLOCKER: establish burst capacity with measured evidence

```text
Create a safe, reproducible V2 load test using generated synthetic media only; never access pilot media or train a model. Measure full scan wall latency and CPU-seconds at concurrency 1, 10, 50, then a controlled 2,000-submit burst against an isolated disposable environment. Do not contact production. Record hardware, warm/cold state, queue backend, worker count, p50/p95, throughput, failures, and bottleneck. Compute required CPU cores as 2000 * measured CPU-seconds / 10 seconds and state assumptions. Do not claim target capacity unless measurements support it. Do not modify Flutter/Directus/Legacy. Back up owner-modified files before changes. Add the benchmark and a concise report; tests must not invoke training or dataset discovery.
```

## F-04 — BLOCKER: verify the distributed deployment template

```text
Prepare and verify V2 staging deployment using only isolated, disposable PostgreSQL and S3-compatible/MinIO services. Do not contact production and do not use private-pilot media. Validate migrations up/down, API and worker readiness, shared queue/storage configuration, authentication/tenant isolation, object deletion, restart behavior, backup/restore, resource limits, and rollback steps. Do not modify Flutter, Directus, or Legacy. Back up any owner-modified file before editing. Add integration checks that skip cleanly when services are absent. Never report staging as verified unless the live isolated run completed; provide exact command output and remaining deployment-owner decisions.
```

## F-05 — HIGH: correct the short-clip eye metric name

```text
Rename the V2 short-video `perclos_estimate` output to an honest name such as `short_clip_eye_closure_fraction` throughout extraction, analysis, result schemas, feature allowlists, baseline lookup, tests, and docs. The value is an observed fraction of valid eye intervals in a short clip and is not validated PERCLOS. If backward compatibility requires the old key, keep it deprecated and null or explicitly mark its semantics; do not silently relabel the value as clinical PERCLOS. Do not touch Flutter, Directus, or Legacy. Back up owner-modified files before editing. Add tests that prevent the short clip metric from being described or consumed as validated PERCLOS. No pilot media, training, or dataset searches.
```

## F-06 — HIGH: publish API contract and compatibility evidence

```text
Generate and check in a versioned OpenAPI contract for all current V2 routes, request/response schemas, auth requirements, idempotency behavior, and stable error responses. Add a test that detects drift between runtime FastAPI OpenAPI and the checked-in file. Do not invent a legacy response contract: if an owner-approved legacy fixture/map is absent, mark that compatibility gate blocked and identify the precise owner input required. Do not modify Flutter, Directus, or Legacy. Back up owner-modified files before changes. Do not read secrets, use private-pilot media, search datasets, or train.
```

## F-07 — HIGH: complete quality abstention and camera pose handling

```text
Audit the V2 image/video quality path and add explicit fail-closed quality handling for off-camera/head pose and partial/occluded face where supported by current MediaPipe landmarks. If a reliable pose measure cannot be derived with existing landmarks, do not guess: report the limitation and gate the affected state as insufficient_quality. Ensure face ratio, lighting, blur, short clip coverage, missing metrics, and audio quality-only behavior are consistent. Audio must never influence state severity. Do not add a new unverified model. Add synthetic unit tests for each quality failure and the no-level abstention response. Do not touch Flutter/Directus/Legacy; back up owner-modified files; no pilot media, training, or dataset search.
```

## F-08 — HIGH: validate indicator threshold config

```text
Harden V2 drowsiness-indicator configuration loading. Define and enforce a supported schema/config version, required four state keys, finite numeric thresholds, sensible ranges, and monotonic severity ordering. Reject malformed, missing, NaN/infinite, negative or contradictory values at startup/config load with a safe error. Keep the response label `indicative, non-diagnostic`; do not emit confidence/accuracy percentages or extra classes. Add boundary and invalid-config tests. Do not touch Flutter, Directus, or Legacy. Back up owner-modified files before editing. No pilot media, training, or dataset search.
```

## F-09 — HIGH: align SQLite/PostgreSQL label behavior

```text
Make V2 SQLite and PostgreSQL label persistence enforce the same scan existence, completed status, tenant/subject ownership, unique-per-scan behavior, foreign-key/cascade semantics, consent retention, and expiry. Update versioned up/down migrations only as needed. Add backend contract tests for both adapters; live Postgres tests may be skipped only when the isolated test DSN is absent. Do not change Flutter/Directus/Legacy. Back up owner-modified files before editing. No training, dataset search, or private-pilot media.
```

## F-10 — HIGH: processor deadlines and graceful shutdown

```text
Add bounded processing deadlines and a documented graceful shutdown/drain policy for V2 workers. On timeout/cancellation, release the lease safely, preserve idempotency/fencing correctness, mark/retry terminally according to configured attempts, and invoke media cleanup. Ensure decoder/native work cannot block shutdown forever; use process isolation if thread cancellation cannot stop it. Add injected hung-processor, timeout, shutdown, and lease-recovery tests. Do not touch Flutter/Directus/Legacy. Back up owner-modified files before editing. Do not use pilot media, train, or search datasets.
```

## F-11 — HIGH: API throttling and overload behavior

```text
Add tenant-aware request throttling and explicit overload behavior for media upload and scan submission in V2. Preserve existing byte quotas and idempotency. Use 429 with Retry-After for rate-limit rejection and a documented queue-capacity response for saturation. Make limits configurable and test isolation between tenants, burst rejection, and recovery. Do not add production service calls. Do not modify Flutter, Directus, or Legacy. Back up owner-modified files first. Do not read secret values, use pilot media, train, or search datasets.
```

## F-12 — HIGH: production-safe container profiles

```text
Make V2 container defaults production-safe and explicit. Ensure the production API image does not default to development auth/storage; add separate API and worker commands/profiles, health/readiness checks appropriate to each role, non-root execution, resource limits in deployment templates, and a startup guard for unsafe settings. Keep local development usable. Do not touch Flutter/Directus/Legacy. Back up owner-modified files before editing. Do not deploy or contact production. Add config/container validation tests where feasible.
```

## F-13 — MED: stable OpenAPI error response definitions

```text
Define explicit response schemas for V2 authentication, authorization, validation, idempotency conflict, upload quota, queue saturation, and storage errors, then ensure each API route documents them in OpenAPI and returns stable codes without internal exception text. Add contract tests. Do not change Flutter, Directus, or Legacy; back up owner-modified files; do not inspect secrets or use pilot media.
```

## F-14 — MED: verify privacy-safe logging end to end

```text
Review V2 logging, middleware, worker, queue, storage, exception, and telemetry paths for personal data. Use synthetic canary identifiers only. Redact/drop authorization headers, tokens, raw filenames, media content, emails, subject IDs, and tenant IDs; minimize or pseudonymize scan/job identifiers and bound retention. Add caplog tests for common exception and failure paths. Do not claim global privacy without inspecting all configured exporters. Do not modify Flutter/Directus/Legacy; back up owner-modified files; never read secret values.
```

## F-15 — MED: isolate media decoding and abuse testing

```text
Harden V2 media decoding against malformed/complex files with bounded CPU, memory, dimensions, duration, frame count, and decoder wall time. Keep current MIME and magic-byte validation, and add adversarial synthetic malformed image/video/audio fixtures. Use process isolation if OpenCV/MediaPipe cannot be safely interrupted. Add per-tenant throttling integration or coordinate with F-11. Do not use pilot media, train, search datasets, touch Flutter/Directus/Legacy, or call production services. Back up owner-modified files first.
```

## F-16 — MED: export metrics and define alerts

```text
Expose/export V2 operational metrics for queue depth/oldest age, scan p50/p95, retries, expired leases, terminal failures, worker heartbeat, media deletion failures, storage errors, and DB pool saturation. Keep labels bounded and free of user identifiers. Document alert thresholds as configurable operator decisions, not guessed facts. Add tests for metric update paths. Do not touch Flutter/Directus/Legacy; back up owner-modified files; no production deployment or pilot media.
```

## F-17 — MED: owner-approved legacy contract bridge

```text
Do not infer a legacy contract. Ask the owner for or locate an already supplied approved response fixture/map, then write compatibility tests between that fixture and V2 response behavior. If it is absent, leave code unchanged and document the missing owner input. Do not modify Flutter, Directus, or Legacy. Do not access anything outside the V2 workspace, read secrets, use private-pilot media, train, or search datasets.
```

## F-18 — MED: document rule limitations and wording

```text
Write V2 user/API wording and a technical threshold note that consistently calls the four-state output indicative and non-diagnostic, not a validated classifier. Explain that audio is quality-only, short-clip eye closure fraction is descriptive, and the thresholds lack clinical calibration. Remove unsupported accuracy or confidence claims if found. Do not change Flutter, Directus, or Legacy. Back up owner-modified files before editing. No pilot media, dataset search, or training.
```

## F-20 — MED: offline dependency vulnerability inventory

```text
Perform an offline-only dependency vulnerability inventory using tools and advisory databases already present in the environment. Do not access the network or reveal secrets. If no offline advisory database/tool is available, report UNVERIFIED instead of attempting downloads. Map findings to exact locked dependency versions, propose scoped upgrades, and do not install or upgrade packages without separate authorization. Do not touch Flutter/Directus/Legacy; back up owner-modified files for any authorized edits.
```
