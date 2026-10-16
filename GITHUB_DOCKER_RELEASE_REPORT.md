# Wellar V2 — GitHub + EasyPanel release report

Updated: 2026-10-16

## GitHub

- Repository requested: `https://github.com/mnashat18/wellar_ai_v2.git`.
- Existing local Git metadata was present on `master`; no remote was configured. `git ls-remote --heads` returned exit code 0 and no refs, confirming the requested remote currently has no branches.
- Publication will use a clean orphan `main` snapshot so prior local history containing research/model material is not sent to GitHub. Existing `master` and ignored source files remain in the local workspace.
- Commit and push are pending final staged-index, size, and secret scans.

## Docker and runtime

- `Dockerfile.easypanel` is the production-style image; `Dockerfile` and `docker-compose.yml` remain the existing local-development path.
- Python image: `python:3.11.13-slim-bookworm`. Runtime direct dependencies are pinned in `requirements-runtime.txt`; the resolved graph is constrained by `requirements-lock.txt`. The image installs no pytest/pip-audit-only packages, runs non-root (`wellar`, uid 999), and copies app code, threshold config, and SQL migrations only.
- Docker build completed for `wellar-ai-v2:release-check`. Runtime `pip check` found no broken requirements. Build context measured 9.09 KB after context exclusions. The image is 1,925,001,917 bytes; MediaPipe/OpenCV/JAX native dependencies make it large and this is a deployment resource consideration.
- In-container smoke verified Python 3.11, OpenCV 4.10.0, MediaPipe 0.10.21 and `mediapipe.solutions`; OpenCV reports FFmpeg enabled. A generated non-human AVI decoded and passed through FaceMesh without error. No model weights were included.
- API and worker both started under isolated local test settings as non-root. API `/health` and `/ready` returned 200 and Docker health became healthy. Default production configuration correctly failed at startup because `WELLAR_POSTGRES_DSN` was not configured.

## EasyPanel staging design

- `compose.easypanel.staging.yml` configures API and worker as separate processes from the same image on a private external network. It expects separately provisioned, isolated EasyPanel PostgreSQL and MinIO/S3 services; those services own encrypted persistent storage and TLS.
- The app requires PostgreSQL `sslmode=verify-full`, HTTPS object storage, and OIDC/Directus verified auth. `WELLAR_MODEL_INFERENCE_ENABLED`, `WELLAR_RESEARCH_IMAGE_DROWSINESS_ENABLED`, and Directus pull default off in the compose service.
- Compose validation passed with `.env.example`; no database, MinIO or EasyPanel service was started or contacted. Staging readiness remains partial pending owner-provided service DNS/TLS/secret configuration and staging verification.

## Tests and safety

- Safe backend suite: 228 passed, 4 skipped, 0 failed, 3 warnings. Skips are two live MinIO and two live PostgreSQL tests because no endpoints were configured. No training, dataset search, or private-pilot media was used.
- No production, Legacy or Directus endpoint was contacted or modified. No Flutter or Directus source was changed.
- `.gitignore` and `.dockerignore` exclude credentials/env files, local data/media, backups, research, model artifacts, caches, and databases. `.env.example` contains placeholders only.

## Release results

- Local main commit: pending.
- Push to `origin/main`: pending.
- Remote `main` HEAD verification: pending.
- Exact remaining blockers: finish staged-index/secret scan and push; owner must provision/verify isolated staging PostgreSQL, MinIO and OIDC secrets. The V2 indicator remains non-diagnostic and no fatigue/focus classifier is validated or enabled.
