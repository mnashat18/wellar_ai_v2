# Wellar AI V2 — P0 execution result

**Status: PARTIAL.** A verified third-party pretrained MobileViT drowsiness image classifier is present, safely loaded, and executed inside the V2 worker processing path as explicit non-production research evidence. The work does **not** produce a validated Wellar fatigue/focus/sleepiness or four-state classifier. Production activation and commercial Wellar use remain blocked.

## Completed implementation

- Added `app/ml/research_image_drowsiness.py`: pinned SHA and byte-count gate; PyTorch weights-only checkpoint load; tensor mapping validation; strict `mobilevitv2_200` architecture load; bounded CPU threads; reusable model session; input decode/dimension checks; uncalibrated binary output isolated from production target predictions.
- Added non-production-only opt-in settings in `app/core/config.py`: `WELLAR_RESEARCH_IMAGE_DROWSINESS_ENABLED=false` and `WELLAR_RESEARCH_IMAGE_DROWSINESS_ARTIFACT_DIR`. Production config rejects enablement.
- Added model initialization/reuse to `app/workers/entrypoint.py`; added quality-gated image-only research invocation and versioned research evidence plus per-modality timings in `app/media/analysis.py`. Existing operational prediction targets remain unavailable.
- Added focused adapter and worker evidence tests. Added `scripts/p0_research_inference_smoke.py` for consent/manifest-gated local worker exercise and `scripts/p0_mobilevit_candidate_smoke.py` for candidate direct inference benchmarking.
- Added selected model, provenance, runtime versions, and artifact hash metadata under `models/pretrained/mobilevitv2-drowsiness-1aa87742/`.

## Actual run evidence

The isolated CPU environment safely loaded the pinned weights and strict architecture, then ran a full `Worker.run_once()` harness over manifest-listed and locally permitted image, five-second video, and WAV assets. No data was exported; training and scientific evaluation were not performed. The worker result was persisted only to in-memory test persistence.

- Selected model inference: one actual image invocation in the worker; `research_only` status; latency **348.728 ms**.
- Full worker analysis wall time after model initialization: **3,806.671 ms**.
- Cold model/architecture load in that run: **10,530.151 ms**.
- Image extraction total **2,233.320 ms**; video decode **725.593 ms**, 141 frames decoded / 18 sampled, total **1,082.192 ms**; audio quality/features **2.646 ms**.
- Process peak RSS **580,067,328 bytes**; CPU time including model load and scan **15.312 s**.
- Five warm direct inference calls median **303.527 ms**; separate worker harness five-call warm median **387.993 ms**. These small single-process samples are not percentiles or capacity evidence.
- The full existing test suite: **214 passed, 4 skipped**, 1 upstream Starlette deprecation warning. Four skipped tests require configured external MinIO/PostgreSQL; these integrations were not run.

The smoke command is reproducible from project root (using the isolated runtime that was used for this run):

```powershell
& 'C:\Users\Abdulrhman Nashat\AppData\Local\Temp\wellar-doze-runtime-p0-20261016\Scripts\python.exe' scripts\p0_research_inference_smoke.py `
  --consent-file data\private-pilot\session-001\consent-attestation.json `
  --session-file data\private-pilot\session-001\session.json `
  --media-dir data\private-pilot\session-001\media `
  --artifact-dir models\pretrained\mobilevitv2-drowsiness-1aa87742
```

The existing repository test environment command was `.\.venv\Scripts\python.exe -m pytest -q`.

## Release gates not met

The chosen model's repository declares MIT, but its two linked training datasets' source rights/consent suitability for Wellar commercial use are not established. The model card describes a driver-image binary classification proof of concept and reports 98.18% accuracy; it does not provide the participant split, counts, intervals, or independently reproducible evaluation needed to treat that value as a validated generalization result. No Wellar predictive accuracy is measured. See [model selection](P0_AI_MODEL_SELECTION.md) and [evaluation](P0_AI_EVALUATION.md).

The research image output is neither a diagnosis nor calibrated confidence and does not determine `stable`, `low_focus`, `elevated_fatigue`, `high_risk`, fatigue, focus, sleepiness, or vigilance. It is single-frame and has no temporal evidence. Five-second video continues to provide measured/quality-gated visual features only. WAV processing remains signal-quality/feature extraction only. No unsupported modality is fused.

Cold model loading exceeded ten seconds on this local CPU, so a cold-start seven-second SLA is not met. The measured single-process warm timings do not demonstrate concurrency, production server capacity, or GPU requirements. No 2,000-scan claim is made.

## Data and safety

- Consent-authorized private-pilot media were used only for local functional inference/diagnostic timing under `media_analysis` permission. The unmanifested M4A was excluded. No private media or identifiers appear in these reports.
- No pilot training, evaluation, or export occurred; the consent attestation explicitly does not authorize model training/evaluation.
- No Flutter, Directus, Legacy, production service, production database, or persistent Docker volume was touched.
- Exact pre-edit backups were created for existing files before edits. 12 backup file hashes were verified with no mismatches. This workspace has no Git metadata, so a Git status/diff is unavailable.

## Changed files

Source/tests/scripts:

- `app/core/config.py`
- `app/workers/entrypoint.py`
- `app/media/analysis.py`
- `app/ml/research_image_drowsiness.py`
- `tests/test_research_image_drowsiness.py`
- `tests/test_research_drowsiness_worker.py`
- `tests/test_media.py`
- `tests/test_worker_inference_integration.py`
- `scripts/p0_research_inference_smoke.py`
- `scripts/p0_mobilevit_candidate_smoke.py`

Artifacts/reports:

- `models/pretrained/mobilevitv2-drowsiness-1aa87742/provenance.json`
- `models/pretrained/mobilevitv2-drowsiness-1aa87742/requirements-inference.txt`
- `P0_AI_COMPLETE_EXECUTION.md`
- `P0_AI_MODEL_SELECTION.md`
- `P0_AI_ARTIFACT_MANIFEST.json`
- `P0_AI_EVALUATION.md`
- `P0_AI_PERFORMANCE.md`

## Next gate

Obtain documented commercial-use rights and source/annotation provenance for training datasets suitable for Wellar's intended population and labels, then collect or access a participant-disjoint, consent-qualified short-mobile-video dataset with paired validated sleepiness/fatigue/attention references. Until an independently evaluated compatible model exists, keep target predictions disabled.
