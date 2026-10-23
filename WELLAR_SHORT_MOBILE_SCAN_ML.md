# Wellar short mobile scan ML decision

## Definitive decision

**CAN USE THE TRAINED KSS MODEL ON EXISTING MOBILE CLIPS: NO.**

Do not supply the existing five-minute PLOS-derived PERCLOS input from the five-second Wellar clip, and do not run the five-minute-trained KSS predictor on the new short-window eye feature. The model’s training window, PERCLOS measurement and baseline are not equivalent to the mobile feature. A separate short-mobile/KSS model requires consented paired data and untouched-participant evaluation first.

## Verified capture and backend constraints

- Flutter `lib/screens/scan_flow_impl_normalized.dart` sets `_videoSeconds = 5`, `_audioSeconds = 5`, and `_focusDuration = 15`. Video is auto-stopped by the five-second timer. The app logs `preferred_resolution=1280x720`, `preferred_fps=30` and a bitrate, but `_targetFrameRate` is not applied to `CameraController`; actual recorded FPS is a file property and can vary by device. The video request uses `ResolutionPreset.veryHigh`.
- Flutter performs video, then audio, then the 15-second focus task. Thus a “seven-second scan” measured from the start of the whole capture flow is impossible without changing the product flow. Seven seconds could only mean a backend-after-submission target, which is not end-to-end upload-to-result latency.
- V2 defaults in `app/core/config.py`: `max_video_seconds=30`, `max_video_frames=1800`, `video_sample_hz=4.0`. `extract_video` rejects file FPS above 120, decodes all frames, and samples with `stride=round(file_fps/requested_hz)`. A 30 FPS file at a 4 Hz request therefore samples at **3.75 Hz** (19 samples in a nominal five-second file); actual timestamps are `frame_index / file_fps`.
- The existing media pipeline already keeps `perclos_estimate=None`; its 4 Hz sparse `sampled_closed_fraction` divides by whole clip duration and does not expose the observed-eye denominator. The new short-window feature below uses its own eligible interval denominator and leaves `perclos_estimate` unchanged.

## Why the PLOS PERCLOS model does not match

The PLOS ONE study associated its KSS prompt with each **five-minute task trial**: the trial began five minutes before the KSS response point. Its PERCLOS eye-closure criterion was pupil diameter `<= 20%` of that participant’s maximum for more than `400 ms`; that maximum was derived from the participant’s pupil-diameter mode during a PVT at their lowest KSS. This is a five-minute, individually baselined pupil-diameter feature, not Wellar’s five-second normalized RGB facial landmark EAR. The source reports those method details and trial timing in its [PLOS ONE Methods](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0285557).

The mobile clip is 60 times shorter, does not collect the PVT pupil baseline, and the current video path does not measure pupil diameter. A 3.75 Hz feature stream has roughly 267 ms between samples, so a `>400 ms` eye-closure decision has only about one or two sampled intervals and is not equivalent to the study’s criterion. The study also separately had raters score five-second ORD video segments; those were observer ratings, not five-second PERCLOS or five-second KSS labels. Short-window closure cannot be relabeled as its five-minute PERCLOS or KSS target.

## Implemented short-window evidence path

Implemented in this isolated copy only. It reuses OpenCV decoding and the existing MediaPipe Face Mesh landmarks. It does not change Flutter duration, V2 duration/sample defaults, legacy routes, or model activation.

`video.short_eye_closure_fraction` is a new descriptive feature contract `wellar-short-mobile-eye/1.0`; it is **not PERCLOS and is not a sleepiness prediction**:

- Eye state uses the existing research-only rule: mean left/right EAR `<= 0.16`.
- Frame timestamps come from decoded frame index divided by the media file FPS. The actual sample rate is included in the output.
- Denominator: sum of intervals between adjacent observations where both endpoints have a detected face and valid landmarks for both eyes, and the positive gap is at most `1.5 / actual_sample_hz`. Numerator: eligible interval time assigned closed by its left-end observation. No gap interpolation or imputation.
- It emits a numeric fraction only with at least **1.0 second** of eligible eye time, **4** valid face/eye observations, **50%** eligible time coverage of the clip, and actual sampling of at least **3 Hz**. Otherwise the fraction is `null` and `short_eye_feature_status` explains why. These are engineering extraction gates, not scientifically validated KSS thresholds.
- Output includes closed seconds, observed denominator seconds, time coverage, valid observation count, clip duration, actual sampling rate, method/version and exclusion rule. Face-loss intervals remain excluded uncertainty. The existing `perclos_estimate` remains null.

The new research trainer interface consumes already imported, consent-gated feature JSONL plus its rights manifest. It accepts only 4–7 second clips, quality-gated short-eye features, a qualified KSS 1–9 score measured from clip end through 15 seconds after the clip, and requires the KSS `class_label` to equal the measured raw score. It never maps or invents labels. It trains only `reference_sleepiness` on `video.short_eye_closure_fraction`; the existing trainer’s participant-disjoint 70/15/15 split and minimum 12 distinct participant gate apply. Twelve is a software minimum, not a powered or validated sample size. Every class has to meet existing training/validation split requirements, and credible independent test performance needs a separately justified participant sample size.

The resulting candidate manifest uses the short feature contract version, which the current operational feature mapping does not support. The training interface is research-only; no inference path was wired or enabled, and the five-minute model remains disconnected. No model was trained in this work because no eligible paired dataset was available in this isolated scope.

## Benchmark evidence and seven-second bottlenecks

Executed `benchmarks/benchmark_short_mobile_video.py` five times on generated black 5-second, 1280x720, 30 FPS MJPG video. No participant or licensed media was used. The benchmark ran the actual OpenCV decoder and MediaPipe Face Mesh CPU path with one warm-loaded Face Mesh instance. It does not include upload or model prediction.

| Measure | Result |
|---|---:|
| Host | Intel Core i7-7820HQ, 8 logical CPUs, 15.83 GiB RAM |
| Face Mesh initialization (one-time cold) | 920.553 ms |
| Median full decode, 150 frames | 563.256 ms |
| Median MediaPipe face detection, 19 sampled frames | 84.289 ms |
| Median feature extraction inclusive of face detection | 140.239 ms |
| Median total per warm extraction, including decode | 701.482 ms |
| Warm-run median total / decode / feature extraction | 710.819 / 566.944 / 138.393 ms |
| Valid face samples / short-window output | 0 / null (`insufficient_observed_eye_time`) |

OpenCV decode dominated this generated-media CPU run; MediaPipe face detection is a smaller but per-sampled-frame CPU cost. The runtime announced TensorFlow Lite **XNNPACK CPU** delegate; no GPU delegate or GPU measurements were used. A cold worker also pays about 0.92 seconds to initialize Face Mesh, so it should be initialized once per worker before traffic. The five-second 720p file’s complete decoding took about 0.56 seconds on this host; decoding all frames rather than sparse sampling is the measurable decode cost. These timings are local synthetic-media results, not a production sizing or device benchmark. H.264/mobile compression, face-present imagery, server CPU, upload, queue, inference and result polling were not measured.

The warm CPU extraction number is below seven seconds in this microbenchmark, but it is not seven-second end-to-end evidence. The whole mobile capture sequence itself contains 5 seconds video + 5 seconds audio + 15 seconds focus, before upload and result polling. For a seven-second backend-after-submit target, this benchmark leaves upload/network, queueing, actual fitted-model inference and result retrieval unmeasured. No 2,000-user concurrency or capacity claim is made.

## Tests and actual run outcome

From this isolated copy, using the existing source virtualenv interpreter without modifying it:

```text
pytest -q tests/test_media.py tests/test_short_mobile_kss.py \
  tests/test_worker_inference_integration.py tests/test_owned_pilot_workflow.py \
  tests/test_real_media_acceptance.py
54 passed, 1 existing Starlette/AnyIO deprecation warning
```

Tests cover timestamped interval fraction arithmetic, excluded face-loss intervals, short evidence abstention, PLOS PERCLOS remaining null, paired-label timing, verbatim KSS scoring, and regressions through worker/media/research pilot flows. The synthetic benchmark confirms real decode/MediaPipe code executed but finds no face; it does not constitute a real eye feature or model prediction.

No training, KSS inference, artifact loading, upload test, service load test, or production action was performed.

## Exact files implemented

1. `app/media/extractors.py` — short-window measured eye feature, timestamps/quality/abstention metadata, and separate decode/feature timing fields.
2. `research/short_mobile_kss_v1.py` — strict paired short-video/KSS qualification and reproducible, consent/rights-gated research candidate training wrapper.
3. `benchmarks/benchmark_short_mobile_video.py` — repeatable 5-second CPU extraction benchmark on generated media.
4. `tests/test_media.py` — interval denominator, face-loss exclusion and abstention tests.
5. `tests/test_short_mobile_kss.py` — training-input feature gates and immediate qualified KSS pairing tests.
6. `WELLAR_SHORT_MOBILE_SCAN_ML.md` — this report.

Unchanged: Flutter, `app/ml/feature_mapping.py` (kept at operational version 1.1 so existing legacy/model feature behavior is preserved), V2 production settings, Directus, legacy service and persistent resources.

## Blocker and minimal next action

**Blocker:** no rights-attested, consented dataset currently pairs this exact five-second mobile video and extracted short-eye feature with a qualified KSS score within 15 seconds after clip end.

**Next action:** collect that paired dataset under the existing V2 consent/rights manifest, then run `python -m research.short_mobile_kss_v1 --data <paired-features.jsonl> --rights <rights.json> --artifact <new-candidate.json> --report <new-report.json>` and evaluate only on untouched participants before any research scoring.

## Isolation and merge

Working copy: `C:\Users\Abdulrhman Nashat\AppData\Local\Temp\wellar-short-mobile-k8945_23`. Flutter and the active V2 source were read-only; no Flutter, Directus, legacy, production or persistent-resource edits occurred. To review, compare these six exact files against the active tree and transfer only the five code/test/benchmark files after owner review; keep the report as the evidence record. Do not connect a five-minute artifact to this feature. A short-window candidate must carry the exact short-feature contract, be independently evaluated, and remain out of operational inference until separately approved.
