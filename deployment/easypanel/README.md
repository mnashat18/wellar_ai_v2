# EasyPanel deployment (staging first)

Use separate V2 API and worker services, a dedicated PostgreSQL database, and a private S3-compatible bucket. This package is an operator runbook; it does not contact or configure any environment.

1. Build `Dockerfile.easypanel` from the V2 repository root and pin the same image digest for API and worker.
2. Create isolated staging database/bucket and configure the names in `.env.template` through EasyPanel settings. Put credentials only in its secret store. Use OIDC or Directus auth; never enable development auth in a deployed environment.
3. Set `WELLAR_POSTGRES_AUTO_MIGRATE=false`. Run once as a migration job: `python -m app.queue.migrate up`. Confirm the target is the disposable/staging V2 database before running it.
4. Start API command `uvicorn app.main:app --host 0.0.0.0 --port 8000`; publish only through TLS. Configure liveness `/health` and readiness `/ready`.
5. Start worker command `python -m app.workers.entrypoint` as a private service. API and worker must share PostgreSQL and S3 configuration. Do not use SQLite or local media storage for multiple replicas.
6. Verify staging manually or run `tools/verify_deployment.py` with `V2_VERIFY_BASE_URL`, `V2_VERIFY_BEARER_TOKEN`, `V2_VERIFY_ALLOW_STAGING=true`, and `V2_VERIFY_ALLOWED_HOST` set in the shell. The verifier checks health, readiness, OpenAPI and the authenticated measurements contract; it does not upload media or create labels. It prints PASS/FAIL per check and never prints the bearer token or response body.
7. For rollback, stop new submissions, deploy the previously approved image, and restore only a schema-compatible V2 backup. Do not point V2 at legacy storage. Validate backup restore and migration rollback on disposable staging first.

The verifier enforces HTTPS plus an exact host allowlist for non-loopback targets. Do not invoke it against production. Run `python -m app.queue.migrate down` only against a disposable database when validating rollback; this is destructive to that database's migrated schema.

Resource limits, replicas, backup schedule, alert thresholds, retention policy, and production rollout remain operator decisions. No production readiness or capacity result is implied by this kit.
