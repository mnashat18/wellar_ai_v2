# Wellar AI V2

Wellar AI V2 is a standalone Python/FastAPI backend. It is separate from the Legacy AI server and does not modify the Flutter or Directus projects. The current worker emits a rule-based, indicative, non-diagnostic eye/face indicator with quality abstention. It is not a validated fatigue classifier. Fatigue/focus predictions and research classifiers remain disabled; no confidence or accuracy output is claimed.

## Development

Requires Python 3.10+. Create a virtual environment, install `requirements-lock.txt`, copy `.env.example` to `.env`, replace placeholders with local development values, then start the API and worker:

```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-lock.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload --env-file .env
python -m app.workers.entrypoint
```

Development defaults use single-host SQLite/local media and must not be used for a deployed service. For safe local auth, use synthetic-only development tokens. Never put real credentials or user media in the repository.

## Docker and staging

- `Dockerfile` and `docker-compose.yml` support the existing local development workflow.
- `Dockerfile.easypanel` is the separate production-style image. It defaults to PostgreSQL, S3-compatible storage, OIDC and disabled model inference; required configuration is validated at startup. The API is the default command; the worker uses `python -m app.workers.entrypoint` from the same image.
- `compose.easypanel.staging.yml` defines API and worker services on a private external network and expects separately provisioned EasyPanel PostgreSQL and MinIO/S3 services.
- Follow [EASYPANEL_DEPLOYMENT.md](EASYPANEL_DEPLOYMENT.md) for TLS, secret, migration, health, rollback and staging verification steps.
- The minimal staging API surface is port `8000`; configure the EasyPanel HTTPS domain to route to API only. Do not expose worker, PostgreSQL, or object storage publicly.

The Docker context excludes `.env` files, media, research material, model weights, backups, and local state. `.env.example` contains placeholders only. The P0 MobileViT and other research weights are not included in the release image or safe publication snapshot.

## Tests

The safe backend suite is:

```powershell
python -m pytest -q --ignore=tests/test_research_image_drowsiness.py --ignore=tests/test_research_evaluation.py --ignore=tests/test_research_drowsiness_worker.py --ignore=tests/test_owned_pilot_workflow.py --ignore=tests/test_ml_training_pipeline.py --ignore=tests/test_dataset_qualification.py --ignore=tests/test_real_media_acceptance.py --ignore=tests/research
```

Live PostgreSQL/MinIO tests require disposable local endpoints and skip when those endpoints are absent. Do not use production services, private-pilot media, or research datasets for local testing.

## Integration and operations

- [EasyPanel staging deployment](EASYPANEL_DEPLOYMENT.md)
- [API integration contract](docs/INTEGRATION.md)
- [Operations runbook](docs/RUNBOOK.md)
- [Environment variable names](docs/ENVIRONMENT.md)
- [Privacy inventory](docs/PRIVACY.md)
- [Thresholds and limitations](docs/THRESHOLDS.md)
- [Directus service role scope](docs/DIRECTUS_SERVICE_ROLE.md)

Do not route production traffic or make employment/fitness-for-duty decisions from this indicative output. A production fatigue/focus classifier requires separate validation and approval.
