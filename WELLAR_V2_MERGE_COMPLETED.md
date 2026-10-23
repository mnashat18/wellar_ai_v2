# Wellar V2 merge completion

## Result

The accepted five-second mobile eye-feature extraction and focused research tests are now present in the active V2 tree. The fitted PLOS KSS regression artifact, integrity manifest, reference sample, and reference inference remain in a separate research-only package. No operational model was activated and no four-state prediction was added.

The active feature mapping, API handlers, worker code, model registry, and existing insufficient-evidence behavior were not edited. The new mobile feature is descriptive eye-closure evidence; it is explicitly not PERCLOS or a sleepiness prediction.

## Exact active V2 files changed

Existing files modified (backed up before modification):

- `app/media/extractors.py`   additive `wellar-short-mobile-eye/1.0` fields in the temporal feature output. Existing temporal event calculations, legacy output fields, timing keys, and result structure are preserved. SHA-256: `197a71123766a27f5248b734c22b7b7f991f5cdaee9c95b768e1e732e0be0fd1`.
- `tests/test_media.py`   appended the two focused timestamped interval and quality/abstention tests. SHA-256: `85e7b7e3ca47538c9e070d98750878805e73b1414831eef1e5d24035a2de745b`.

Pre-edit backups:

- `.backups/merge-20261016-010219/app/media/extractors.py`   SHA-256 `584fac965378b77ec151cd9817e67857ea0cbcdd18bf5a9aca4e373f5350ff87`.
- `.backups/merge-20261016-010219/tests/test_media.py`   SHA-256 `b7388c8727b340ce1d5a849181f3b66d7670cec372646c517c52bf00b8e186c3`.

New mobile research files:

- `research/short_mobile_kss_v1.py`
- `benchmarks/benchmark_short_mobile_video.py`
- `tests/test_short_mobile_kss.py`
- `WELLAR_SHORT_MOBILE_SCAN_ML.md`

New isolated KSS research package:

- `research/kss_perclos_integration/adapter.py`
- `research/kss_perclos_integration/integrate.py`
- `research/kss_perclos_integration/manifest.json`
- `research/kss_perclos_integration/media/__init__.py`
- `research/kss_perclos_integration/media/extractors.py`
- `research/kss_perclos_integration/models/wellar_kss_perclos_ridge_v1.json`
- `research/kss_perclos_integration/reference/predict_kss.py`
- `research/kss_perclos_integration/samples/plos_locked_test_trial.json`
- `research/kss_perclos_integration/test_integration.py`
- `research/kss_perclos_integration/video_extractor_smoke.py`
- `research/kss_perclos_integration/SOURCE_A_TRAINING_REPORT.md`
- `research/kss_perclos_integration/SOURCE_B_PERCLOS_REPORT.md`
- `research/kss_perclos_integration/WELLAR_KSS_MODEL_INTEGRATED.md`

This report is the only additional root file.

## Preserved model boundary and evidence

The trained regression artifact is still a numeric KSS model, not a Wellar class model. It was copied unchanged:

- Artifact SHA-256: `2eb1418edb305d9eca0e2f8fd159dc1092c3bdd4484b67c7be3e523fcbade85d`.
- Manifest SHA-256: `ed0ecf1e1ae6ba7262c4303901f38e73cba88c7000388a711319afefa821c3cc`.
- Locked reference sample SHA-256: `f23f0d290a1452cbe575e0117b41f7eedf168cef4222c79f2b7341930997fb3f`.
- Pinned dataset SHA-256 (in the adapter/manifest): `6349435674993d62306513dc4db8b970dd3581bec27806fc4f3a281bbc548f55`.

The verified reference run takes the source PERCLOS fraction 0.026111028591223775, converts it to 2.6111028591223775 percent exactly once, and returns research-only KSS estimate 5.855358150733352 against reference KSS 4. Confidence remains null and production is disabled.

The PLOS training feature is five-minute, subject-baselined pupil-diameter PERCLOS. The video extractor is a different EAR-based method. The mobile extractor uses a five-second window. The adapter continues to reject both the measurement-method mismatch and short observation windows. Its abstention is explicit; it does not infer KSS/classes from the new short-mobile feature. The prior reference inference is a reproduced locked data-row calculation, not human-video inference.

## Preservation and ownership

The following operational files were not modified: `app/ml/feature_mapping.py`, `app/api/v1.py`, and `app/workers/worker.py`. The feature mapping remains unchanged, so the new short feature is not selectable as an operational model input. No Flutter, Directus, legacy service, deployment, database, or volume changes were made.

Git status could not be obtained: the active V2 directory and its `D:\\flutter\\last` and `D:\\flutter` parents have no `.git` metadata. No Git mutation command was used. The only pre-existing active source files modified were the two listed above, and both have exact pre-edit backups. All other intended additions were confirmed absent before creation. Thus the merge preserved all files outside those two backed-up, additive source changes; repository-wide uncommitted-owner status cannot be independently asserted without Git metadata.

## Verification

Commands run with the active V2 Python interpreter:

- `python -m pytest -q tests/test_media.py tests/test_short_mobile_kss.py`   **25 passed**, one existing Starlette/AnyIO deprecation warning.
- `python -m pytest -q tests/test_api.py tests/test_worker_inference_integration.py tests/test_owned_pilot_workflow.py tests/test_real_media_acceptance.py tests/test_ml_training_pipeline.py tests/test_research_evaluation.py`   **57 passed**, one existing Starlette/AnyIO deprecation warning.
- From `research/kss_perclos_integration`: `python -m unittest discover -s . -p 'test_*.py' -v`   **10 passed**.
- From `research/kss_perclos_integration`: `python integrate.py reference`   returned the numeric research-only output above with the pinned artifact and sample hashes.

No full backend suite, production deployment, production data access, Flutter build, or Directus acceptance was performed. The successful reference run does not validate mobile video inference or predictive performance in workplace scans.
