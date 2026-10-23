# Wellar V2 P0 pretrained evidence baseline

## Status

**P0 BASELINE STATUS: PASS for operational pretrained face/landmark evidence; fatigue, focus, sleepiness, and risk prediction remain unavailable.**

The existing V2 worker processor already ran MediaPipe Face Mesh on an authorized local image and video. This implementation makes the pretrained model identity and per-modality processing times observable in the returned evidence object. It does not enable a classifier or create a readiness score.

## Model, weights, task, and rights

- Runtime: `mediapipe.solutions.face_mesh.FaceMesh`, installed MediaPipe `0.10.9`, CPU XNNPACK delegate. Real inference was observed in the processing run (TFLite reported its XNNPACK delegate).
- Bundled model assets used by the Face Mesh graph: `face_detection_short_range.tflite` and, with `refine_landmarks=True`, `face_landmark_with_attention.tflite`. These are present in the installed wheel (229,714 and 2,495,106 bytes respectively). They produce face landmarks; they are not drowsiness/fatigue classifiers.
- Licensing evidence: the upstream MediaPipe repository identifies Apache-2.0 licensing ([official license](https://github.com/google-ai-edge/mediapipe/blob/master/LICENSE), [upstream package metadata](https://github.com/google-ai-edge/mediapipe/blob/master/setup.py)). The installed wheel has no separate license metadata adjacent to its bundled model assets. The report therefore records the upstream project license and that asset-specific metadata was absent; no newly downloaded model or separately redistributed weight was added.
- The existing PLOS KSS/PERCLOS research ridge artifact (`research/kss_perclos_integration/models/wellar_kss_perclos_ridge_v1.json`, SHA-256 `2eb1418edb305d9eca0e2f8fd159dc1092c3bdd4484b67c7be3e523fcbade85d`) was not loaded. Its five-minute pupil-diameter/PVT feature definition is incompatible with short RGB EAR evidence.

## Operational evidence contract

`evidence.schema_version` is now `1.1`. Image, video, and audio evidence retain their existing feature and quality fields and now include `timing_ms`. The `models` array reports MediaPipe only when an image/video extraction through the MediaPipe extractor ran. Existing predictor extension stays disabled by default. The existing legacy result fields stay null/unavailable, and unsupported fatigue, subjective-fatigue, and vigilance targets have no value or confidence.

Actual processor output statuses for the authorized local diagnostic were image `available`, video `available`, and WAV audio `available`; prediction invocation was `disabled`, and all three prediction targets were `unavailable`. The output included image geometry/quality measures, timestamped short-clip eye/face measures, and WAV signal-integrity measures. Private feature values and media identifiers were not copied into this report. The video contract explicitly marks its short-window eye fraction as not PERCLOS and not a sleepiness prediction.

Audio uses the existing PCM WAV decoder and signal-integrity summary only (duration, sample rate/channels, RMS/peak, silence, clipping, DC offset). There is no validated speech or vocal-fatigue model, so no audio model inference or vocal-state claim is made. M4A was excluded because its permission was not attested in the local consent scope and the V2 audio extractor accepts WAV only.

## Authorized functional run and timings

The local consent attestation authorized media analysis and storage for local feature extraction, and explicitly denied model training and evaluation. Only the attested JPG, MP4, and WAV were processed. No training, evaluation, export, upload, or model fitting was performed.

Measured on the current local CPU environment, one subsequent worker-processor pass recorded:

| Modality/stage | Observed time |
|---|---:|
| Image decode, Face Mesh, image features (total) | 1,230.714 ms |
| Video decode | 537.234 ms |
| Video total including sampled Face Mesh/features | 875.829 ms |
| WAV decode and signal checks | 2.588 ms |
| Combined processor wall time for the three media inputs | 2,210.301 ms |

An earlier first processor invocation measured 7,253.070 ms total, reflecting a cold process/model-runtime initialization path. These are single local functional observations, not a population benchmark, load test, latency distribution, or production-capacity claim. Timings exclude client capture and upload. No scan acceptance or mocked inference is counted as a model prediction.

## Changes and preservation

Changed files:

- `app/media/analysis.py` — add per-modality timing to evidence, bump evidence schema to `1.1`, and add conditional MediaPipe model provenance with task and license-scope note.
- `tests/test_worker_inference_integration.py` — add a wiring regression for evidence schema/timing and ensure a test-only extractor does not falsely report production model provenance.
- `tests/test_media.py` — update the evidence schema assertion to `1.1`.
- `P0_BASELINE_EXECUTION.md` — this execution record.

Exact pre-edit backups were captured before each source/test edit:

- `backups/p0-baseline-pre-edit-20261015T225857Z/manifest.json` (SHA-256 values for analysis and worker integration test are recorded there).
- `backups/p0-baseline-test-pre-edit-20261015T225937Z/manifest.json` (SHA-256 for the media test is recorded there).

No Flutter, Directus, Legacy, production service, database, persistent volume, or model artifact was changed.

## Verification

- `tests/test_worker_inference_integration.py` and `tests/test_media.py`: **31 passed**, one upstream Starlette deprecation warning.
- Real local functional inference: MediaPipe Face Mesh executed in the V2 `MultimodalFeatureProcessor` operational path on consent-authorized image/video; WAV quality extraction executed. Predictor targets remained disabled/unavailable.
- No model training or scientific evaluation was performed or permitted.

## Limitations and next action

Face landmarks and short-window EAR are descriptive measurements only. Face Mesh visibility/geometry does not establish alertness, fatigue, focus, impairment, or risk. Sparse five-second capture cannot reproduce the five-minute PLOS pupil-diameter/PERCLOS input. The audio summary assesses signal integrity, not speech, emotion, fatigue, or health. No validated classifier exists in the enabled worker path; unsupported targets must remain unavailable.

**Next action:** have the release owner review the bundled MediaPipe model-asset license scope for the intended commercial distribution before enabling this evidence path in a commercial deployment; continue to keep unsupported classifications disabled.
