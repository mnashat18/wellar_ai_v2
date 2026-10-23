# P0 Tonight

Implemented in `D:\flutter\last\wellar-ai-v2`. No Flutter, Directus, or Legacy files changed. Existing modified files were copied to `backups/tonight-pre-edit-20261016/` before edits. No dataset search, audit, training, or pilot-media evaluation was run.

## Status

1. **PASS — Rule-based indicator.** Added worker/API `drowsiness_indicator` with `level` low/medium/high and exactly four states: Stable, Low Focus, Elevated Fatigue, High Risk. Uses observed PERCLOS estimate, blink rate, and mean/max eye closure duration; thresholds come from `config/drowsiness_indicator.json`.
2. **PASS — Quality abstention.** Face detection ratio, luminance, blur variance, required temporal eye measures, and optional audio quality gate failure return `status: insufficient_quality` without `state` or `level`.
3. **PASS — No confidence/accuracy claims.** Indicator response contains neither field and always carries `indicative, non-diagnostic, not a validated classifier`.
4. **PASS — Optional personal baseline.** A robust PERCLOS z-score is added only with at least five prior quality-passed scans, nonzero baseline MAD, and available current measurement; otherwise the field is omitted. History is tenant/user scoped and capped at 100.
5. **PASS — Consent-gated KSS API and media deletion.** `POST /api/v1/scans/{scan_id}/labels/kss` accepts KSS 1–9. False consent retains nothing; true consent stores KSS plus allowlisted numeric features in a dedicated feature-only table. Worker deletes media after each processed modality. Local deletion and consent paths are tested. Live S3/PostgreSQL integration remains unverified because service endpoints were not configured.
6. **PASS — Safe existing V2 suite.** 169 passed, 4 skipped. Training, dataset/research evaluation, pilot acceptance, and repository audit tests were excluded to honor “no training, no dataset searches, no audits.”
7. **PASS — Four-state mapping.** The indicator returns only Stable, Low Focus, Elevated Fatigue, or High Risk when quality passes; otherwise `insufficient_quality` with no state or level. Audio affects quality gating only.

## Tests

`pytest`: **169 passed, 0 failed, 4 skipped** (MinIO and PostgreSQL endpoints not configured). One Starlette deprecation warning.

## Files changed

- Modified: `app/api/v1.py`, `app/media/analysis.py`, `app/media/contracts.py`, `app/media/extractors.py`, `app/queue/postgres_store.py`, `app/queue/sqlite_store.py`, `app/scans/schemas.py`, `app/workers/worker.py`, `tests/test_media.py`, `tests/test_self_test_runner_e2e.py`.
- Added: `app/drowsiness_indicator.py`, `app/scans/labels.py`, `config/drowsiness_indicator.json`, `deployment/migrations/003_training_labels.up.sql`, `deployment/migrations/003_training_labels.down.sql`, `tests/test_drowsiness_indicator.py`, `tests/test_drowsiness_indicator_api.py`.
- Backups: `backups/tonight-pre-edit-20261016/` contains pre-edit copies of all modified existing source/test files.

## Limits

Thresholds are configurable heuristics and have not been clinically or scientifically validated. The short-clip PERCLOS value is a descriptive fraction over valid observed eye intervals, not a diagnostic measurement. Real S3/PostgreSQL deletion and label persistence require deployment integration tests. Training and research evaluations were not run.

ONE next action: review the four-state threshold config before enabling the indicator for production users.
