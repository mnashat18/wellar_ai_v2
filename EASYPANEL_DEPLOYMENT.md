# Wellar AI V2 — EasyPanel staging deployment

This guide deploys a separate V2 backend. It does not connect to Legacy storage, modify Directus, or authorize production traffic. The rule-based indicator remains indicative and non-diagnostic; pretrained/research classifiers stay disabled.

## EasyPanel project settings

- Repository: `https://github.com/mnashat18/wellar_ai_v2.git`
- Branch: `main`
- Build context: repository root (`.`)
- Dockerfile: `Dockerfile.easypanel`
- Image: build once and use the same immutable image digest for API and worker
- Container/API port: `8000`
- API command: Dockerfile default, `python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --no-access-log`
- Worker command: `python -m app.workers.entrypoint`
- API readiness: `GET /ready`; liveness: `GET /health`
- Public domain: route only the API service through an EasyPanel HTTPS domain/proxy; do not publish the worker or database/object-store ports publicly.

`compose.easypanel.staging.yml` defines the V2 API and worker services using the same image, a shared external private network, required staging-mode settings, and an API readiness check. It expects `WELLAR_STAGING_ENV_FILE` (default `.env.example`) and a network named `WELLAR_STAGING_NETWORK` (default `wellar-v2-staging`). `.env.example` is placeholders only and cannot authenticate or connect until configured. Do not commit the populated env file.

## Isolated dependencies

Create separate EasyPanel staging services before starting V2:

1. A new PostgreSQL database and service identity dedicated to V2. Enable TLS, require certificate verification, and do not reuse the legacy database. Keep the database on the V2 private network with persistent encrypted storage and platform backups.
2. A new private MinIO/S3-compatible bucket and least-privilege service identity dedicated to V2. Enable HTTPS/TLS, block public access, enable persistent encrypted storage and backups, and set an approved lifecycle policy. Create the bucket before API/worker startup.
3. Attach both dependencies and V2 API/worker to the same private network. Use internal service DNS names. The PostgreSQL certificate must match its DNS hostname; provide its CA using `sslrootcert` in `WELLAR_POSTGRES_DSN`. For a private MinIO CA, set `AWS_CA_BUNDLE` to a mounted CA file readable by the non-root `wellar` user.

PostgreSQL and object-store persistence belong to the separately managed EasyPanel dependency services. The V2 API/worker containers keep no persistent local media and share no local SQLite state. The application requires PostgreSQL with `sslmode=verify-full` and an HTTPS S3 endpoint in staging; do not weaken those checks to make a local stack start.

## Environment and secret store

Use `.env.example` as the variable-name template only. Set actual values through EasyPanel protected environment/secrets; do not paste secrets into Git, tickets, reports, command history, or logs.

Required application values:

- `WELLAR_ENV=staging`, `WELLAR_QUEUE_BACKEND=postgres`, `WELLAR_MEDIA_BACKEND=s3`, `WELLAR_AUTH_MODE=oidc`.
- `WELLAR_POSTGRES_DSN`: staging DSN with `sslmode=verify-full` and the correct `sslrootcert` path.
- `WELLAR_OIDC_ISSUER`, `WELLAR_OIDC_AUDIENCE`, `WELLAR_OIDC_JWKS_URI` (HTTPS), and `WELLAR_OIDC_TENANT_CLAIM`.
- `WELLAR_S3_BUCKET`, `WELLAR_S3_REGION`, `WELLAR_S3_ENDPOINT_URL` (HTTPS), `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`; `AWS_CA_BUNDLE` when the private CA is not in the image trust bundle.
- `WELLAR_POSTGRES_AUTO_MIGRATE=false`; choose bounded pool/queue/retry/decode limits for the staging service and database capacity.
- `WELLAR_LABEL_RETENTION_DAYS` is required before consented feature labels can be stored. Set it only after the owner has approved a retention period.
- Keep `WELLAR_MODEL_INFERENCE_ENABLED=false`, `WELLAR_RESEARCH_IMAGE_DROWSINESS_ENABLED=false`, and `WELLAR_DIRECTUS_PULL_ENABLED=false` unless the corresponding independent approvals and credentials exist. The research MobileViT weights are not included in the image or publication snapshot.

Directus pull is optional for the existing legacy-to-V2 facade: only when explicitly enabled, configure `WELLAR_DIRECTUS_URL` and `WELLAR_DIRECTUS_SERVICE_TOKEN` in the secret store. That token needs only the permissions in `docs/DIRECTUS_SERVICE_ROLE.md`. This service configuration does not alter the Directus schema or the Flutter/Legacy workflow.

## Safe migration and startup order

1. Verify the repository is `main`, the image is from the reviewed commit, and the EasyPanel database/bucket are new, isolated staging resources.
2. Create the private network and PostgreSQL/MinIO services, TLS trust, credentials, persistent volumes, and bucket. Verify their health from the private network.
3. Set all required variables in the secret store. Leave `WELLAR_POSTGRES_AUTO_MIGRATE=false` so API and worker cannot race migrations.
4. Run a one-off migration job from the exact API image: `python -m app.queue.migrate up`. The migration command is advisory-lock protected and skips already-applied migrations. Confirm logs contain no connection string or credentials.
5. Start one API instance and one worker. API/worker share the same PostgreSQL DSN, S3 bucket/region/endpoint, OIDC settings and security limits. Check `/health`, `/ready`, worker heartbeat, queue progress, and sanitized logs.
6. Run `python tools/verify_deployment.py` only against a disposable staging host. Configure `V2_VERIFY_BASE_URL`, `V2_VERIFY_ALLOW_STAGING=true`, `V2_VERIFY_ALLOWED_HOST`, and `V2_VERIFY_BEARER_TOKEN` in the operator's local environment or secret store. The verifier accepts loopback HTTP for local checks and requires HTTPS plus an exact allowed-host match for a staging host. This verifier is read-only: it checks health, readiness, OpenAPI and the measurements contract; it does not create scans, labels or media.
7. Attach an HTTPS domain to API port `8000` through the EasyPanel proxy. Confirm TLS, allowed origins, request limits, authentication and rate limits before allowing a staging client.

Do not run the `down` migration against staging as a routine rollback. It is destructive. For a staging schema rollback test only, take a disposable database backup first, stop API/workers, and run `python -m app.queue.migrate down` using that disposable database's settings. Production rollout is not authorized here.

## Rollback

Stop submissions and the worker, preserve the queue/database/object-store state for investigation, then redeploy the last approved V2 image and compatible environment configuration. Restore only a verified V2 database backup into a V2 database. Never point V2 at Legacy databases, Directus storage, or production buckets. Re-run readiness and media-deletion checks before re-enabling staging traffic.
