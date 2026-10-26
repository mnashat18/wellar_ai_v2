# Easypanel deployment (V2 rule-based indicators)

This deployment uses the separately labeled `rule_based_indicators` engine. ML
inference remains disabled (`WELLAR_MODEL_INFERENCE_ENABLED=false`); research
artifacts are not production models. Do not change the artifact guard to enable
ML. This engine emits heuristic wellness indicators, not medical advice or
validated diagnostic predictions.

## Services

Create these services in Easypanel (verify service/network/health-check fields
in Easypanel):

1. **PostgreSQL**: a persistent PostgreSQL 15+ database, reachable from both
   app services. Use TLS with certificate verification (`sslmode=verify-full`).
2. **S3-compatible storage**: MinIO with a persistent volume, or external S3.
   Create the private bucket and grant the service account list/head, put, get,
   and delete permissions. The API checks bucket accessibility at startup and
   fails clearly if the check fails.
3. **API**: build from this repository using `Dockerfile.easypanel`; expose
   container port `8000`, route the HTTPS domain to it, and use `/health` for
   liveness and `/ready` for readiness.
4. **Worker**: use the same built image and environment as API, with command
   `python -m app.workers.entrypoint`. Do not expose a public port.
5. **Retention task**: schedule `python -m app.media.retention` at an interval
   appropriate to your policy; this deletes expired raw media and opted-in
   retained labels. Confirm its schedule and logs in Easypanel.

Set the Docker build context to the repository root and Dockerfile to
`Dockerfile.easypanel`. Its base image is pinned to
`python:3.11.13-slim-bookworm`; it installs the constrained
`requirements-runtime.txt` using `requirements-lock.txt`, includes the
versioned rule config, runs as non-root, and provides a `/ready` healthcheck.
Both API and worker must use the same image tag. Configure a persistent volume
for PostgreSQL and MinIO if hosted in Easypanel. Application scratch files are
temporary; queue/results live in PostgreSQL and media objects live in S3.

## Environment variables

Set these in both API and worker service settings (no values are included here):

| Variable | Description |
| --- | --- |
| `WELLAR_ENV` | Set `production`. |
| `WELLAR_LOG_LEVEL` | `INFO` or `WARNING`; production rejects `DEBUG`. |
| `WELLAR_QUEUE_BACKEND` | Set `postgres`. |
| `WELLAR_POSTGRES_DSN` | PostgreSQL connection URI with `sslmode=verify-full`. |
| `WELLAR_POSTGRES_AUTO_MIGRATE` | Keep `false`; run the migration command below during release. |
| `WELLAR_AUTH_MODE` | Set `oidc`. |
| `WELLAR_OIDC_ISSUER` | Exact OIDC issuer URL. |
| `WELLAR_OIDC_AUDIENCE` | API audience configured at the identity provider. |
| `WELLAR_OIDC_JWKS_URI` | HTTPS JWKS endpoint. |
| `WELLAR_OIDC_ALGORITHMS` | Approved asymmetric JWT algorithms, normally `RS256`; never use `none` or symmetric secrets. |
| `WELLAR_OIDC_TENANT_CLAIM` | Tenant claim name (default `tenant_id`). |
| `WELLAR_MEDIA_BACKEND` | Set `s3`. |
| `WELLAR_S3_BUCKET` | Existing private bucket name. |
| `WELLAR_S3_REGION` | Bucket region. |
| `WELLAR_S3_ENDPOINT_URL` | HTTPS endpoint for MinIO; omit for AWS S3. |
| `AWS_ACCESS_KEY_ID` | Secret S3-compatible access key, if static credentials are used. |
| `AWS_SECRET_ACCESS_KEY` | Secret S3-compatible secret, if static credentials are used. |
| `AWS_SESSION_TOKEN` | Optional temporary credential token. |
| `WELLAR_MODEL_INFERENCE_ENABLED` | Set `false`; production ML remains disabled. |
| `WELLAR_RESEARCH_IMAGE_DROWSINESS_ENABLED` | Set `false`. |
| `WELLAR_CORS_ORIGINS` | Comma-separated exact HTTPS Flutter web origins; leave empty for native-only clients. |
| `WELLAR_RATE_LIMIT_PER_MINUTE` | Positive per-tenant limit; default is 120. Set according to expected traffic. |
| `WELLAR_MEDIA_RETENTION_DAYS` | Raw-media retention in days; default is 30. Set to the approved policy. |
| `WELLAR_MAX_VIDEO_BYTES` | Maximum uploaded video size; default 20 MiB. |
| `WELLAR_MAX_VIDEO_SECONDS` | Maximum decoded video duration; default 30 seconds. |
| `WELLAR_MAX_AUDIO_BYTES` | Maximum uploaded audio size; default 8 MiB. |
| `WELLAR_MAX_AUDIO_SECONDS` | Maximum decoded audio duration; default 30 seconds. |
| `WELLAR_MAX_IMAGE_BYTES` | Maximum uploaded image size; default 5 MiB. |
| `WELLAR_MAX_IMAGE_PIXELS` | Maximum decoded image pixels; default 4,000,000. |
| `WELLAR_VIDEO_SAMPLE_HZ` | Video analysis sampling frequency; default 4 Hz. |
| `WELLAR_WORKER_COUNT` | Worker concurrency; default 1. |

Use Easypanel secret fields for DSN, OIDC endpoints as appropriate, and object
store credentials. Never put credentials in image build args or source files.
Confirm the service network, environment inheritance, persistent-volume
mounts, scheduled task, and health-check behavior in Easypanel.

## Release and startup

1. Build and deploy the API/worker image tag in Easypanel. Keep both on the same
   release tag and set all variables above.
2. Before enabling API traffic, run once against the release image with the
   same environment and network:

   ```sh
   python -m app.queue.migrate up
   ```

   Migration is intentionally an explicit release operation; startup reports
   an actionable readiness error if PostgreSQL schema is absent.
3. Start worker and API. Verify API `/health` returns 200 and `/ready` returns
   200 with queue, media storage, rule config, and extractor dependencies all
   ready. Confirm worker logs show it connected and polling.
4. Configure the public domain and HTTPS termination in Easypanel. Verify
   forwarded protocol/host handling, certificate renewal, and health-check
   paths in Easypanel.
5. Point the Flutter app's API base URL to the HTTPS API base URL (the host
   without `/api/v1`, unless the app configuration explicitly expects that
   prefix). Rebuild/release the app with that environment value.
6. Run the smoke test below using a small, authorized sample video. Review the
   output and API/worker logs. A `retake_required` result is a completed job but
   not a valid prediction.

## Smoke test

The script uses Python standard-library HTTP support and uploads the local file
you select. Set these in your shell, without putting secrets in the command
history where that matters:

```sh
BASE_URL=https://api.example.invalid \
WELLAR_SMOKE_TOKEN='OIDC_ACCESS_TOKEN' \
SAMPLE_VIDEO_PATH=/path/to/authorized-sample.mp4 \
python tools/smoke_test.py
```

Optional `SMOKE_TIMEOUT_SECONDS` defaults to 180 and `SMOKE_POLL_SECONDS` to 2.
The script prints job/result status, prediction, confidence, reason, engine and
version. It exits non-zero on upload/API/job failure or timeout. Do not use
another person's media without authorization and consent.

## Rollback

Keep the previous API/worker image tag available. If readiness or smoke checks
fail, stop routing traffic to the new API, restore the previous API and worker
tag together, then verify health/readiness and a known-good scan. Do not roll
back database migrations unless the migration's reviewed rollback procedure
allows it; inspect migration compatibility first. Preserve PostgreSQL and
object-store volumes during service rollback. Verify routing and rollback
controls in Easypanel.

## Local verification limits

Docker image build and compose startup need a working Docker daemon. The current
development host's Docker CLI may be installed while the daemon is unavailable;
verify the image build, service networking, persistent volumes, TLS, object
permissions, scheduled retention, and Easypanel UI settings in the target
environment before release.
