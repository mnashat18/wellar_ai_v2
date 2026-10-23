# Stage 01 — Image, Video & Audio Analysis

**Review date:** 2026-10-20  
**Scope:** Python/FastAPI media ingestion, worker extraction, quality/evidence output and synthetic-media verification.  
**Verdict: PASS**

## Readiness

| Modality | Readiness | What runs |
|---|---|---|
| Image | Ready for Stage 1 extraction | JPEG/PNG decode, dimensions, luminance, clipping, Laplacian blur proxy and actual MediaPipe face/landmark extraction. No-face, multi-face, low-light, blur and missing-eye-landmark abstention are represented. |
| Video | Ready with bounded spatial continuity | MP4/AVI decode via OpenCV, metadata/resource checks, timestamped frame sampling, temporal landmarks, sparse eye/mouth observations and frame quality. Identity continuity is not guaranteed and no fatigue measure is claimed. |
| Audio | Ready for supported PCM WAV | Uncompressed PCM WAV decode (8–96 kHz, mono/stereo, 8/16/24/32-bit), signal-quality measures and bounded 20 ms speech-like activity heuristic. This is not speech recognition or validated human-speech VAD. |
| Multimodal | Ready for descriptive evidence contract | Versioned per-modality envelopes, provenance, quality/reason fields, explicit missing evidence, safe insufficient-evidence status and inactive fusion. Fatigue prediction remains disabled. |

## Actual processing and output

Image decoding is restricted at intake to JPEG and PNG and bounded by pixel and isolated-process memory/time limits. The worker computes grayscale mean/std, Laplacian variance as a blur proxy, clipped-pixel fraction, dimensions, face visibility and normalized eye/mouth landmark geometry when exactly one face is detected. A second detected face causes abstention and eye/mouth measurements are null; no face also leaves those measurements null. Low luminance (<20 grayscale mean) and low blur proxy (<10 Laplacian variance) are quality failures. These are extraction heuristics, not validated thresholds for fatigue or image acceptability.

Video intake accepts MP4 and AVI containers, subject to actual OpenCV codec availability. The decoder validates FPS, frame count, duration, dimensions and per-frame dimensions, then decodes each frame and samples at the configured rate (capped by frame and time limits). MediaPipe Face Mesh runs in video mode. Exactly one face with valid eye landmarks is required for an observation. Consecutive normalized landmark boxes must remain within 0.55 face diagonals, within a 2x width/height scale ratio, and within the sampling gap limit. Face loss, missing eye landmarks, multi-face frames, or a failed spatial check makes the track unestablished; aggregate temporal eye/mouth measurements become null and the modality is insufficient quality. This is a spatial continuity check, not identity verification: it cannot distinguish a replacement face occupying nearly the same box. The temporal pass otherwise computes coverage, missingness, sampled eye closure, observed blink events/rate, mouth-open events, coarse yaw/pitch proxies, sampled luminance and blur.

Eye aspect ratio, thresholded eye closure, blink events/rate, closure duration, mouth aspect ratio/open events, and yaw/pitch are descriptive research observations only. Short-clip closure fraction is gated by observed eye time, valid observations, sampling rate and coverage; it is explicitly not PERCLOS. The `perclos_estimate` remains null. Mouth opening is not classified as a yawn. These observations do not indicate fatigue, sleepiness, focus or risk.

Audio decoding supports uncompressed integer PCM WAV only: 1–2 channels, 8–96 kHz, and 8/16/24/32-bit samples. Defaults cap audio at 30 seconds and 8 MiB. The implementation computes duration, sample rate, channel count, RMS/peak dBFS, maximum channel DC offset, AC RMS dBFS, low-energy-window fraction and clipping fraction. A bounded 20 ms analysis also computes RMS activity, spectral flatness, centroid, speech-band ratio, zero-crossing rate and temporal variation. It reports `silence`, `uncertain_noisy_or_non_speech`, `uncertain_clipped_signal`, or `speech_like_activity_heuristic`. Only the final heuristic status passes audio quality. Its conservative criteria require at least 10% active windows, at least five active windows, 65% qualifying active windows, and minimum RMS and spectral-centroid variation. These are engineering heuristics, not validated speech thresholds. Music, noise or other non-speech signals may resemble speech; this reports a speech-like candidate, not proof of speech. No real human speech corpus was used to validate accuracy.

The VAD work is bounded by media limits: for the default 30-second maximum it analyzes at most 1,500 windows, with FFTs no larger than 2,048 points. Decoder processing remains inside the timeout/RSS-limited child process.

The top-level result remains schema `2.0`, feature envelopes `2.0`, prediction extension `1.0`, evidence `1.2`. Each modality reports presence, extraction status, quality/reason, computed features and provenance. Invalid/corrupt media, failed resolution/integrity, absent media and insufficient signal are represented separately. Missing modalities are `not_provided`; unavailable stored objects carry a failure reason. Fusion remains inactive, the result remains `insufficient_evidence`, target values stay null and the Stage 1 indicator is `not_performed`. API/worker startup does not load model artifacts. Predictor injection is behind an explicit constructor boundary with `prediction_enabled=False` by default. The tests verify injected operational and research predictors are not called under Stage 1 defaults. The existing guarded registry remains available for a future explicit Stage 2 integration.

## Defects corrected

- Face Mesh previously requested only one face and the code took the first result. It now requests up to two and abstains when multiple faces are detected; eye/mouth measurements are not attributed to an arbitrary face.
- Image/video blur was measured but not used to mark extraction quality. A conservative explicit blur-proxy gate now reports `blurred_image` and the corresponding quality flag.
- The media worker previously converted extracted eye measurements into `Stable`, `Low Focus`, `Elevated Fatigue` or `High Risk` states and loaded per-user indicator history. That rule-based classification is now disabled in this Stage 1 pipeline. Descriptive measurements remain available, while the result says fatigue prediction was not performed.
- Startup previously loaded operational and research image predictor artifacts according to settings. API and worker startup now create the media extractor without loading these prediction artifacts.
- Added bounded, spectrally informed speech-like activity assessment for PCM WAV. Silence, clipping, stationary noise, pure tone and uncertain signals are not labeled as speech.
- Added a sampled-frame spatial continuity gate. Temporal measurements are nulled after face loss, multi-face detections, missing eye landmarks or a discontinuous face box.
- Actual MediaPipe runs verified one generated face, two generated faces, low light and blur. Missing-eye-landmark abstention is tested with a deterministic malformed landmark result because the real detector did not naturally produce a face-without-landmarks fixture.
- Updated retry regression expectations to keep media on retryable prediction/decode failures, consistent with the durable-result-before-delete lifecycle. Media is still released after a successful durable commit or terminal failure.

## Verification evidence

All test media was generated locally from code-generated images, short OpenCV videos, and PCM WAV. No real participant media or recorded speech was used. Synthetic speech-like signals exercise the VAD path only; they do not validate real-speech accuracy. No production services, databases or storage were changed.

Final focused media and inference-guard command:

```text
rtk proxy .\.venv\Scripts\python.exe -m pytest -q tests/test_audio_vad.py tests/test_video_continuity.py tests/test_synthetic_face_quality.py tests/test_media.py tests/test_mp4_media.py tests/test_real_media_acceptance.py tests/test_isolated_decode.py tests/test_media_feature_adapter.py tests/test_worker_inference_integration.py tests/test_research_drowsiness_worker.py tests/test_worker_startup.py tests/test_api.py tests/test_media_reconciliation.py tests/test_hardening_media_cleanup.py tests/test_ai_engine_foundation.py tests/test_directus_pull.py
Exit code: 0
162 passed, 2 warnings in 102.63s
```

This set covers actual extraction for generated one-face, two-face, no-face, low-light and blurred images; continuous and spatially switched face videos; synthetic speech-like waveforms, silence, pure tone, noise and clipping; supported PCM widths and unsupported codecs; valid MP4/AVI plus corrupt, truncated and oversized media; combined image+video+audio output, schema, quality and provenance; retry-safe media; API/worker startup; and disabled predictor invocation. The two-face image test observed two faces through MediaPipe. The spatial-switch test used actual MediaPipe detections and verified aggregate temporal measurements became null.

The first expanded run exited `1` with 2 failures and 156 passes. One test expected a stationary 220 Hz tone to count as available audio; the VAD correctly marked it uncertain. That valid-signal test now uses a documented synthetic speech-like fixture, and a separate test asserts pure tones stay uncertain. The other failure was a stale quality-scope provenance assertion, updated to the actual output. The rerun passed with the results recorded above.

One direct run of the real extractors on a 512×512 generated face image, 2-second 4 FPS synthetic AVI (8 frames), and 1.5-second 16 kHz WAV reported:

| Extractor | Observed time |
|---|---:|
| Image | 2060.697 ms |
| Video | 461.455 ms total; 33.725 ms reported decode time; 8 frames decoded and sampled |
| Audio with VAD | 21.258 ms |

These are one local run, include image model initialization, exclude worker subprocess startup, and are not a benchmark or latency guarantee. Isolated decoder execution is bounded by configured timeout and RSS caps; video additionally bounds duration, decoded frame count and pixels. Audio duration/rate/channel constraints bound the PCM buffer size.

Two dependency deprecation warnings were emitted by Starlette/httpx and AnyIO. No test failed in the final focused run.

## Limits and remaining work

- **Flutter capture compatibility:** read-only inspection of `D:\flutter\waller_app` (`lib/screens/scan_flow_impl_normalized.dart`, `lib/services/scan_service.dart`, `lib/services/scan_finalize_service.dart`) found camera video capture, a JPEG thumbnail, and separate audio recorded as AAC-LC at 44.1 kHz mono in `.m4a`. V2 accepts the JPEG thumbnail and MP4/AVI video, but not `.m4a`/AAC audio. Convert captured audio to uncompressed PCM WAV (1–2 channels, 8–96 kHz, 8/16/24/32-bit, within the 30-second and byte limits) before V2 audio intake. This conversion is the required handoff; Flutter was not changed. The video upload helper attempts MP4 normalization and writes a `.mp4` staged file, but on compression failure it can reuse original bytes; verify the actual container before labeling the upload MP4.
- Supported V2 intake formats are JPEG/PNG, MP4/AVI and uncompressed integer PCM WAV. GIF, WebP, TIFF, MOV/QuickTime, MKV, MP3, AAC/M4A, FLAC, OGG, IEEE-float WAV and compressed WAV are unsupported. Actual MP4/AVI codec support depends on the deployed OpenCV build.
- The speech-like activity algorithm is a heuristic tested on synthetic waveforms only. It is not validated against real speech, music or diverse noise corpora and can abstain on genuine speech or accept some speech-like non-speech signals. It does not transcribe, identify speakers, assess intelligibility or infer fatigue.
- The video continuity test is spatial and cannot detect face replacement in nearly identical positions/scales. That limitation is explicit in result provenance; biometric identity tracking is not claimed.
- Blur/luminance gates, EAR thresholds and geometric event definitions are heuristics. None is a validated fatigue measure or classifier.
- Signal-quality assessment does not establish speech presence, intelligibility, microphone noise characteristics or clinical suitability.
- Only focused media/processing regressions were run; the full repository suite and live object-store/database integrations were not run. Existing dirty worktree changes outside this scope were left intact.

## Exact files changed for Stage 1

- `app/media/extractors.py`
- `app/media/analysis.py`
- `app/main.py`
- `app/workers/entrypoint.py`
- `tests/test_media.py`
- `tests/test_synthetic_face_quality.py`
- `tests/test_isolated_decode.py`
- `tests/test_worker_inference_integration.py`
- `tests/test_research_drowsiness_worker.py`
- `tests/test_audio_vad.py`
- `tests/test_video_continuity.py`
- `tests/media_fixtures.py`
- `tests/test_ai_engine_foundation.py`
- `tests/test_real_media_acceptance.py`
- `STAGE_01_MULTIMODAL_ANALYSIS_REPORT.md`

The repository already had extensive pre-existing dirty worktree changes, including in some of these files; this list records files edited during this Stage 1 task and does not claim that all their current diffs originated here.
