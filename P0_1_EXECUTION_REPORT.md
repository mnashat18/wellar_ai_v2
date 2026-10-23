# P0.1 Execution Report — Consent-Qualified Mobile KSS Training

**Date:** 2026-10-16  
**Acceptance:** **BLOCKED**

## Compact gate

| Gate | Result |
|---|---|
| Lawfully qualified five-second video + immediate KSS records | **0 eligible** |
| Real mobile-compatible model trained | **NO** |
| Real mobile model artifact created | **NO** |
| Independent participant-disjoint model metrics | **None** |
| Collector package functional | **YES, rights gated** |
| Collector ready for authorized real collection | **NO — approvals/rights/key are absent; collection remains disabled** |

The existing pilot consent grants local media analysis and storage, but explicitly denies model-training, evaluation, and cross-site-transfer permission. The session manifest has no KSS label or authoritative capture timestamps. It is therefore ineligible for training/evaluation. No consent, label, participant identity, dataset, result, or metric was fabricated.

## A. Inventory and rights matrix

| Source | What is present | Recorded rights / compatibility | Disposition |
|---|---|---|---|
| Existing private pilot session | One session manifest; consent-listed image, video, and WAV media | `media_analysis=true`, `media_storage=true`, `model_training=false`, `evaluation=false`, `cross_site_transfer=false`. No KSS or capture timestamp is recorded. | Local diagnostics only; no importer output, export, training, or evaluation. |
| Additional M4A file in the private media directory | File not referenced by the session manifest | No file-specific consent linkage established. | Not used for feature extraction or ingestion. A compatibility probe was rejected immediately by the V2 WAV-only decoder before audio feature extraction. |
| Existing PLOS driving KSS/PERCLOS research artifact | Ridge artifact `research/kss_perclos_integration/models/wellar_kss_perclos_ridge_v1.json`; SHA-256 `2eb1418edb305d9eca0e2f8fd159dc1092c3bdd4484b67c7be3e523fcbade85d`; 1,850 bytes | Manifest identifies a 300-second pupil-diameter feature (`pupil_diameter <= 20% of participant PVT maximum for >400 ms`) and concurrent numeric KSS. It is a research-only integration. The local manifest does not establish commercial rights for a new deployment. | Not loaded or used for five-second RGB/EAR training; incompatible input definition and duration. |
| Other authorized labeled mobile data in V2 workspace | No non-private JSONL/CSV/Parquet/NPZ/XLSX labeled files found under `data/` | No other qualifying records discovered in the authorized research workspace. | No eligible dataset to train. |
| Software fixtures | Explicitly marked `unit_test_synthetic` only | Software-test use only; not a research dataset. | Used only for importer/training plumbing; no metrics or artifact retained. |

With the existing V2 extractors, the consent-listed image, video, and WAV media decoded and returned feature structures during local in-memory diagnostics. The V2 video quality gate returned `available` and its short-eye feature status was `available`. No raw media or numeric feature values were copied to this report or an export. This permission does not authorize training/evaluation. No third-party AI provider or remote storage was used.

## B. Collector pairing, contract, and correction

The collector is in the existing separate isolated copy at:

`C:\Users\Abdulrhman Nashat\AppData\Local\Temp\wellar-mobile-collector-19tp2sir`

Reviewed behavior:

- Consent/rights gate precedes session creation and camera access. Separate media-analysis, storage, training, and evaluation consent is required; cross-site transfer is independent and optional.
- A server ticket links one clip to one KSS response. The response stores the unmodified integer KSS 1–9 and server receipt timestamp. A record becomes paired/exportable only after both the verified video and response exist for that ticket. Client times remain marked unverified.
- The server accepts verified 4.90–5.10 second video and enforces the immediate label window. Export is curator-only, encrypted as authenticated AES-GCM `.wlr`, excludes unpaired/withdrawn records, and requires explicit rights configuration. Withdrawal and retention delete encrypted media and the wrapped data key. No real export was generated because no eligible record exists.
- Export targets V2 research schema 1.1 and preserves KSS separately from subjective fatigue and vigilance; unsupported labels remain unknown. V2 schema 1.2 requires direct eye-state and mouth-opening annotation that this collection protocol does not gather, so those labels are not invented.

I found that collector and V2 both advertised `wellar-short-mobile-eye/1.0` while using different calculations. The collector previously counted only intervals whose two endpoints were closed and put sample-count coverage in `short_eye_time_coverage`; V2 uses the left endpoint to weight an interval and defines that coverage as observed eligible seconds divided by clip duration. This could let the trainer consume semantically mismatched features under one version.

In the isolated collector only, I corrected feature sampling to use the V2 frame stride, refined FaceMesh landmarks and normalized EAR coordinates; aligned valid-eye bounds, interval denominator, left-endpoint closure rule and observed-time coverage; retained sample-frame coverage as a separate diagnostic; and closed decoder/FaceMesh resources on failure. I added regression tests for the transition rule, gap exclusions, time coverage, sample-rate rejection, and AES-GCM tampering. No V2/Flutter/Directus/Legacy source was edited.

Before editing, exact collector copies were saved with SHA-256 manifests:

- `p0_1_backups/20261015T224718Z/collector/app.py` — `7ff077d8af1887eaeb4b7e8e0cc814fab8d6878744005a39852947179e035fa4`
- `p0_1_backups/20261015T224718Z/tests/test_collector.py` — `3850b0dced4b01722431f19d1ea8356ef688151380098b2de903a2d7e22c809f`
- `p0_1_backups/20261015T224718Z/WELLAR_MOBILE_TRAINING_COLLECTION.md` — `f757ce57cc638f6377094b35dccc9887745fac2aedd43ed9c764bdfbcada3b4f`

An earlier exact copy of the unchanged Dockerfile is at `p0_1_backups/20261015T224454Z/Dockerfile`, SHA-256 `e3058f34e0fbe2842de1d201336f76f7db5a7f56d7a585a58ec3a30acce31078`. The Dockerfile already prepares `/data` and `/export` for UID 10001; it was not changed.

## C. Training and artifact results

No lawful participant-labeled five-second RGB/EAR + KSS records were available, so real participant-disjoint training, validation, locked testing, and calibration were not run. **Real model trained: NO. Real mobile artifact created: NO.** Independent accuracy, macro-F1, recall, confusion matrix, calibration, abstention coverage, and confidence intervals are **not available**.

For plumbing only, I ran the current V2 schema validator, short-mobile quality/KSS pairing gates, and `train_target` in memory over 48 explicitly marked synthetic records across 24 synthetic identities. The mobile training function completed. All fixture labels/rights were synthetic, the candidate artifact remained in memory, no files were emitted, and no predictive metrics were retained or reported. This establishes software execution only, not model performance.

The existing trainer requires at least 12 distinct eligible participants for three-way participant-disjoint splits, qualified KSS 1–9 without remapping, the versioned short-eye feature contract, and explicit dataset rights. Twelve is a software minimum, not a sample-size or statistical-power recommendation. The pilot contributes **zero** eligible participants.

## D. Verification and staging readiness

- Collector tests: `python -m pytest -q tests` — **17 passed**, one Starlette deprecation warning. Covers consent/rights gates, authorization, KSS range and timing, duration bounds, media rejection, V2-matched feature math, pairing/export exclusion, retention, withdrawal, and ciphertext tamper rejection.
- V2 research regressions, using the V2 project virtual environment and shared app fixtures: `.venv\Scripts\python.exe -m pytest -q -p no:cacheprovider tests/test_short_mobile_kss.py tests/test_owned_pilot_workflow.py tests/test_ml_training_pipeline.py tests/test_dataset_qualification.py` — **28 passed**, one Starlette deprecation warning. No dependency was installed or upgraded.
- Collector staging Compose configuration validated with `docker compose ... config --quiet`. No staging Compose project, named/persistent volume, production system, or remote service was started.
- Built isolated image `wellar-mobile-collector:p0.1-aligned`, image ID `sha256:9396c0ee6a537c445d6423ead59f37eba592d674df9b6f05591fc27d152ba1cb`, size **964,015,001 bytes**, user `10001:10001`.
- Read-only-container smoke with ephemeral `tmpfs` data/export mounts: `/healthz` returned `status=ok`, `vision_runtime=true`, `collection_enabled=false`; `/` and `/static/app.js` returned HTTP 200. Container was stopped and removed. The local preview at `http://127.0.0.1:18888` is likewise bound to loopback and collection-disabled.
- No authorized staging hostname/TLS issuance, mobile-device acceptance, real participant recording, real encrypted export/decryption, or real-data evaluation was performed.

## Exact files changed

- Existing isolated collector source: `collector/app.py` (V2-compatible feature extraction and interval/coverage semantics).
- Existing isolated collector tests: `tests/test_collector.py` (feature, quality-gate, and authenticated-integrity regressions).
- Existing isolated collector evidence: `WELLAR_MOBILE_TRAINING_COLLECTION.md` (corrected contract, build, and test evidence).
- New V2 workspace deliverable: `P0_1_EXECUTION_REPORT.md` (this report).
- Exact pre-edit backups and SHA-256 manifests are listed above. No V2 application/trainer, Flutter, Directus, Legacy, deployment, or production files were edited.

## E. Minimum owner inputs and exact blockers

Keep `COLLECTION_ENABLED=false`. Before authorized real collection, the study owner must provide and approve:

1. Actual ethics/organizational approval reference and lawful processing/ownership basis for this specific short-video + immediate-KSS protocol.
2. Final participant information and consent text/version, with separate, explicit permissions for recording, media analysis, encrypted retention, model training, and evaluation. Cross-site transfer must stay false unless separately authorized.
3. Dataset ID/version, documented rights/license, study ID, protocol/acquisition ID and protocol SHA-256, linkage-governance reference and participant-code custodian, curator role, site ID, KSS instrument/qualification reference, and study contact.
4. A dedicated 32-byte collector encryption master key delivered through a secret manager, plus the isolated staging hostname (`SITE_HOST`). Do not put secrets in source control or this report.
5. New eligible paired records under those rights. The existing pilot’s training/evaluation denials cannot be overridden by this implementation.

The precise P0.1 blocker is **absence of consent- and rights-qualified five-second video/KSS participant records**: the only inspected pilot session has no KSS/time pairing and explicitly denies training/evaluation. The 300-second pupil-diameter artifact cannot substitute for them.

**One next action:** the study owner approves and supplies the short-mobile KSS study-rights/consent packet and secret-manager key, then authorizes collection of a new participant cohort under that protocol. Until then, no real training or prediction activation is permitted.
