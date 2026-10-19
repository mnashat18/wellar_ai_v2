# Wellar V2 research boundary and evaluation v1

This package is offline-only evaluation infrastructure. It does not train models, load model artifacts, call external services, or feed predictions to the operational worker. The current worker remains deterministic feature extraction with `insufficient_evidence`; no validated Wellar classifier exists.

## Local data inventory

No authorized labeled research dataset or research model artifact is present in this repository. Synthetic scan fixtures used by software tests are not scientific observations, cannot pass manifest loading without an explicit unit-test override, and must never be used to claim model performance. Consequently, predictive training and scientific validation were not performed.

## Manifest contract

Use [schema_v1_1.json](schema_v1_1.json) for new JSONL sample records and `research.evaluation_v1.validate_record` for dependency-free validation. The reader retains schema 1.0 compatibility; 1.1 adds a separately qualified `subjective_fatigue` label. Use [schema_v1.json](schema_v1.json) only for historical 1.0 records. Store only de-identified feature summaries and label metadata in the manifest, not frames, images, waveforms, face crops, voice, or other raw media. The validator rejects identifying/credential/raw-media field names and rejects synthetic fixtures by default. Feature summaries can still be biometric or sensitive and require restricted access, encryption at rest, access audit, retention limits, and a documented purpose.

Every sample must include:

- Pseudonymous participant and session IDs, with the re-identification key held and governed separately.
- Study/dataset/version, source reference, acquisition protocol, feature extractor provenance, and role-based curation/assessment provenance.
- Explicit consent state and separate booleans for research evaluation, model training, and cross-site transfer. Consent must be granted for evaluation; each additional use requires its own permission.
- UTC collection time (or `unknown`), coded site/device/industry/occupation (or explicit `unknown`/null), and availability state for video, audio, image, and reaction-task data.
- Separate reference labels. Subjective sleepiness must be a documented, explicitly qualified KSS value from 1 through 9. Subjective fatigue is a distinct construct and requires its own separately selected and qualified instrument; do not relabel KSS as fatigue. Objective vigilance must be an independent PVT measure with metric, units, protocol, timing, and source. A class label requires its own preregistered rule ID; do not derive an unregistered class from a raw value.
- A documented missing/unknown status and reason instead of imputing absent labels or modalities.

Do not place names, employee IDs, employer names, direct contact details, face images, or voice samples in manifests. Occupation and site/device fields must be controlled categories or study codes, not free-text job/person descriptions. Cross-site transfer is prohibited unless its explicit consent permission is true and governance authorizes it.

## Evaluation workflow

1. Obtain ethics/privacy approval, data-use rights, and consent covering the planned evaluation and any model training or cross-site transfer. Complete an independent power/sample-size analysis before enrollment or test access.
2. Define the KSS qualification/timing, the independent fatigue instrument/timing, and the PVT protocol/metric/thresholds separately. Fill the placeholders in `protocol_v1_1.json`, including inclusion/exclusion, missingness, abstention, calibration, subgroup thresholds, and acceptance criteria. `validate_preregistration` rejects the template and previously inspected data presented as an untouched test set.
3. Validate authorized, de-identified JSONL records with `python -m research.evaluation_v1 <manifest.jsonl>`. Validation checks provenance, consent, modality availability, labels, and pseudonymous linkage metadata; it does not read media or fit a model.
4. Create deterministic participant-disjoint train/validation/test assignments with `participant_disjoint_split`; run `check_split_leakage` for participant/session/media fingerprint overlap. Fit/tune only on training/validation data.
5. Freeze feature code, model hash, calibration, decision thresholds, abstention policy, and preregistration hash before the held-out custodian releases the test predictions/labels. Evaluate the locked test once. Never iterate on that test and continue to call it held out.
6. Report class balance, coverage, abstention, participant-cluster bootstrap confidence intervals, Brier score/ECE when probabilities exist, and per-site/device/industry metrics only when the configured participant and per-class minimum support is met. Suppress small-group scores.
7. Run site-disjoint and device-disjoint holdouts independently where data support them. These test distinct external-generalization questions; participant-disjoint random splits alone do not establish domain generalization.
8. Record aggregate-only results with `experiment_record`. It hashes protocol/split assignments and optional analysis source files; it rejects participant/sample IDs and feature payloads. Keep any ID-to-split mapping in the governed research environment.

`evaluate_target` accepts externally produced predictions for exactly one target and reports the corresponding metrics. This package intentionally contains no baseline trainer: none can be fit honestly until authorized labels and features exist. The guard in `app/ml/artifact_guard.py` rejects research-only artifacts and requires independent validation/release evidence for any future operational load path. There is no model artifact loader in the present V2 worker.

## Minimum paired study data

For each participant, collect repeated synchronized sessions that pair (where separately consented and available): video-derived features, audio-derived features, and reaction/PVT observations with exact acquisition timestamps and device/protocol provenance. Obtain a qualified KSS reference near each session using a documented instrument and timing window. Maintain independent PVT raw outcome measures (for example preregistered lapse count or reaction-time summary) and do not use KSS to define vigilance labels. Include missing-modality sessions rather than silently dropping them, subject to a prespecified missingness policy.

The study must identify participant/session linkage, site, device model/version, coded industry/occupation, collection time, modality availability/quality, extractor version, label source/assessor role, label measurement time, and all consent scopes. Store identity linkage separately. Include enough participants and reference-class observations at every planned site/device/industry to meet a preregistered power calculation and subgroup minimums; frame counts do not substitute for independent participants. Ensure some whole sites/devices are reserved before analysis for external tests, with participants disjoint from development data.
## Candidate training pipeline (software engineering only)

`training.py` is a bounded NumPy softmax trainer for three separately selected
classification targets: `reference_sleepiness` (qualified KSS class),
`subjective_fatigue` (its own qualified non-KSS instrument), and
`vigilance_impairment` (a preregistered PVT class). Each invocation trains one
target and never substitutes one target's labels for another's. The trainer
requires the schema 1.1 record contract plus a separate rights manifest that
attests to an identified license, approval, protocol digest, consent, and
explicit model-training permission for every row.

The trainer performs deterministic participant-disjoint train/validation/test
splits, checks participant/session/media-fingerprint leakage, learns missing
value medians and standardization from the training partition only, fits a
bounded linear-softmax model, temperature-scales on validation data, and reports
target-specific test classification/calibration/coverage metrics with a
confidence abstention threshold. Input is capped at 5,000 samples / 16 MiB,
features at 64, epochs at 250, and candidate JSON artifacts at 1 MiB. Output
files are created exclusively and are never overwritten.

The sample configuration in `training_config_v1.json` is a template only. Run
from the V2 repository after replacing it with an approved target-specific
configuration and supplying only a rights-cleared de-identified dataset:

```powershell
python -m research.training --data <approved-dataset.jsonl> --rights <rights-manifest.json> --config <target-config.json> --artifact <new-candidate.json> --report <new-report.json>
```

Every output manifest is deliberately `research_candidate`,
`research_only=true`, `validation_status=not_validated`; it is rejected by
`app/ml/artifact_guard.py`. Synthetic fixtures are permitted only via an
explicit in-process test argument and are never accepted by the CLI. The
reported metrics from unit tests are software-mechanics checks, not scientific
results. No authorized Wellar dataset or predictor artifact is currently
present; therefore this tool must not be used to claim Wellar performance.

`app/ml/json_backend.py` is a fixed JSON data parser and NumPy inference backend.
It does not import, unpickle, or execute model artifacts. Artifact size, schema,
dimensions, numeric ranges, duplicate JSON keys, interface version, target and
feature identifiers are validated before inference. The existing runtime
activation guard still requires an independently validated release manifest
and a matching SHA-256 before any trusted loader is called. Operational model
activation and worker integration remain disabled until those scientific and
API-release gates are resolved.
