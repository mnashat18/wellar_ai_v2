# Wellar V2 operations runbook

## Scope and launch gate

The checked-in Docker/EasyPanel files are deployment templates. Production/staging acceptance, HA, backup/restore, rollback, alerting, and the 2,000-request burst gate have not been completed. Do not route production traffic until an owner has approved and recorded those steps.

## Deploy

1. Build an immutable V2 image from the approved revision and record its digest.
2. Provision isolated PostgreSQL and private S3-compatible storage, identity auth, TLS, least-privilege access, encryption, and network policy.
3. Supply configuration through the platform secret manager. Do not put secret values in this runbook, command history, or committed env files.
4. Run `python -m app.queue.migrate up` once as a controlled migration job with `WELLAR_POSTGRES_AUTO_MIGRATE=false`.
5. Start one API and one worker; verify `/health`, `/ready`, `/metrics`, queue progression, media deletion, consent/withdrawal, and sanitized logs in staging.
6. Scale only from measured queue-age, scan-latency, CPU/RAM, database-pool and object-store results. Resource-limit variable names are in the scalable compose template.

## Shutdown and recovery

The worker stops claiming new work during shutdown and applies the configured bounded drain. Native decode runs in a supervised child process with wall-time and RSS checks; timeout/crash releases work for retry and media cleanup. Configure the platform termination grace period above `WELLAR_WORKER_DRAIN_SECONDS` plus the process shutdown margin, then verify lease recovery on staging.

Retries are at-least-once; scan submission requires the same `Idempotency-Key` on retry. Deletion failures are retried and counted. Startup/periodic reconciliation removes media for terminal scans and stale scratch files. Queued or actively leased scans retain media until terminal processing; an abandoned nonterminal job is cleaned only after lease recovery makes it terminal. This is the queued-scan retention caveat: queue delay or repeated retries can extend media retention beyond processing time.

## Migrations, backup, restore and rollback

Migrations have explicit up/down SQL. Before migrations or rollback, take a database backup and confirm object-store lifecycle/backup policy. Test restoration into a separate disposable environment and verify results, label rows, media metadata, and authorization before any cutover. Rollback means stop new V2 traffic, retain/secure the queue and media for investigation, deploy the last approved V2 image/config, and restore only a schema-compatible backup. Never point V2 at legacy persistent volumes.

Backup interval, retention, RPO/RTO, object versioning, and backup deletion after consent withdrawal require owner decisions and have not been verified.

## Monitoring and alerts

`/metrics` exposes bounded counters and aggregate queue diagnostics in Prometheus text format. Verify the deployed metric set before configuring dashboards; DB-pool saturation and histogram coverage are not claimed as complete here. Configure external collection and alerts for oldest queued age, queue saturation, retry/terminal failure growth, worker absence, storage/deletion errors, 5xx, DB pool saturation, readiness failures, disk and memory pressure. Threshold values and paging owners are operator decisions, not hardcoded product claims.

## Safe local integration

`docker-compose.test.yml` contains disposable PostgreSQL/MinIO only and requires `V2_TEST_*` values. The verifier performs read-only health/readiness/OpenAPI/measurements checks, accepts loopback HTTP or an exact HTTPS staging host allowlist, and does not create scans, labels, or media. Set `V2_VERIFY_BASE_URL`, `V2_VERIFY_BEARER_TOKEN`, `V2_VERIFY_ALLOW_STAGING=true`, and `V2_VERIFY_ALLOWED_HOST` only in the shell for a staging run. Never pass production URLs or credentials to tests or benchmark tools.
