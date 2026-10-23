# Wellar AI V2 - Stage 2 Scientific Fatigue Model Report

**Review date:** 2026-10-21  
**Verdict: BLOCKED for the intended pre-work occupational-fatigue model.**  
**Completed subtask:** reproducible, research-only KSS sleepiness baseline and isolated inference verification.  
**Production prediction:** disabled.

## 1. Exact prediction target

The product target is a continuous pre-work occupational-fatigue burden estimate for office/customer-service employees. There is no approved target instrument or qualified participant dataset for this estimand in the repository.

The existing real-data model predicts **concurrent subjective sleepiness**, represented by the numeric 1-9 KSS response after a five-minute simulated-driving trial. KSS is not an occupational-fatigue scale, PVT is a separate vigilance-performance reference, and facial/acoustic features are observations rather than fatigue labels. The model result must not be described as pre-work fatigue, vigilance impairment, focus, risk, medical status, or fitness for duty.

The proposed occupational reference in `research/WELLAR_PHASE2_MEASUREMENT_DECISION_V1.md` is the Arabic OFER-15 Intershift Recovery subscale, but its construct, Arabic version, scoring, permissions and pre-shift fit for this workforce remain unapproved. Existing scientific acceptance criteria in `research/WELLAR_SCIENTIFIC_ACCEPTANCE_V1.md` intentionally have no numeric error, calibration, coverage or meaningful-margin thresholds. No such thresholds were frozen before the existing locked-test results were inspected.

## 2. Dataset inventory, rights and suitability

| Dataset | Local/rights status | Labels and modalities | Stage 2 disposition |
|---|---|---|---|
| Uchiyama et al. 2023 PLOS supplementary trial table, DOI `10.1371/journal.pone.0285557.s004` | Present locally as `research/public_data/uchiyama_2023_22780783/pone.0285557.s004.xlsx`; SHA-256 `6349435674993d62306513dc4db8b970dd3581bec27806fc4f3a281bbc548f55`. Figshare declares CC BY 4.0 and the table contains 408 trials from 17 participants. [Figshare record](https://figshare.com/articles/dataset/Trial_data_for_Figs_2_and_3_and_Table_2_/22780783) | Simulator-driving KSS 1-9, PERCLOS, ORD, SDLP, SEM and EEG summary values; 8 sessions and 24 trials per participant. No paired Wellar RGB image, video, audio or workplace scan data. | Usable for a narrow retrospective PERCLOS-to-KSS sleepiness benchmark with attribution. Not suitable for pre-work occupational fatigue or multimodal Wellar model validation. The study is explicitly simulated driver drowsiness, with 17 participants. [PLOS paper](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0285557) |
| UL-DD | Zenodo record is restricted. Access requires an account, institutional/corporate R&D email, purpose statement and acceptance of the research-use agreement. The agreement permits bona fide research including commercial R&D subject to restrictions, but the files were not requested or accessed. [Official record and agreement](https://zenodo.org/records/17978727) | 19 driver participants; KSS every four minutes; video, posture, biometric and driving signals. | Conditional driver-drowsiness/sleepiness research lead only; not an employee pre-work-fatigue ground truth. No Wellar feature compatibility or extraction was measured. |
| DROZY | Research/evaluation dataset with a published license agreement that prohibits commercial-product development absent an amendment; no data used. [Official dataset page](https://www.drozy.uliege.be/) | 14 participants; KSS, PVT, PSG and NIR video, with missing tests/frames. | Excluded from Wellar commercial model development under current terms; small driver-drowsiness cohort and NIR extractor compatibility also unresolved. |
| UTA-RLDD | Official page offers video access but does not publish commercial training permission; no data downloaded. [Official owner page](https://sites.google.com/view/utarldd/home) | 60 participants, RGB video, three participant-reported predominant-state categories derived from KSS descriptions, not raw KSS or independent PVT. | Excluded pending written rights and target/label clarification. Self-reported driver drowsiness categories cannot be relabeled as fatigue. |
| Brighter Time 2025 | Prior local audit found the CC BY 4.0 metadata but no locally available archive to inspect; no rows were used here. The data are repeated app/cognitive-task observations, not paired face/voice media. | Modified KSS and task performance including PVT, in an everyday-life cohort. | Possible separate sleepiness/task-outcome analysis after row, participant, consent and feature audit; it does not supply the intended workplace fatigue target or multimodal media. Prior findings are recorded in `research/WELLAR_PHASE3A_DATASET_AUDIT_AND_REVIEW_PACKAGE_V1.md`. |

**Participant and leakage audit for the used PLOS benchmark:** 17 distinct participants, 408 unique subject/session/trial keys, no missing cells in the 10 source columns. The deterministic split is participant-disjoint: train 10 participants/240 trials, validation 3/72, test 4/96. Every participant's repeated sessions remain in one split. Split assignment is derived from the documented SHA-256 participant rule and reproduced by the source script. The four-person test cluster count makes interval estimates highly uncertain.

## 3. Candidates reviewed

- **Simple real-data baseline:** one-feature ridge regression from source-study PERCLOS percent to concurrent numeric KSS. This is the only fitted candidate with actual participant-disjoint held-out evaluation available in this workspace.
- **Multimodal/fused model:** not trained. There is no legally and scientifically qualified set of paired Wellar image/video/audio/reaction features and independent occupational-fatigue labels. The PLOS table has aggregate PERCLOS but no raw media and no audio.
- **MobileViT drowsiness and DOZE-GUARD artifacts:** local research-only binary driver-drowsiness image classifiers. Their model cards report high accuracy, but underlying training-data rights and independent participant-disjoint results for Wellar's target are not established. Their binary target is not occupational fatigue; neither was accepted as a candidate for this product target. Do not interpret publisher metrics as Wellar accuracy. The local inventory and limitations are in `P0_AI_MODEL_SELECTION.md` and `P0_AI_ARTIFACT_MANIFEST.json`.
- **PLOS PERCLOS/KSS research ridge:** compatible only with the study's pupil-diameter PERCLOS definition and five-minute window. Stage 1's short-clip EAR-derived closure observation is a different method and duration, so no numerical conversion makes it a valid input mapping.

## 4. Training method and existing artifact

The model was previously trained in an isolated research copy; this Stage 2 run reproduced its evaluation from the checked-in licensed source workbook rather than refitting a Wellar fatigue model. It uses a single standardized feature (`PERCLOS %`) and ridge linear regression. Scaling is fit only on the train participants; ridge alpha is selected by validation MAE over `[0.001, 0.01, 0.1, 1, 10, 100]`. Selected alpha: `100`. The locked test is not used for fitting or selection.

Research artifact: `research/kss_perclos_integration/models/wellar_kss_perclos_ridge_v1.json`  
Artifact SHA-256: `2eb1418edb305d9eca0e2f8fd159dc1092c3bdd4484b67c7be3e523fcbade85d`  
Artifact schema: `wellar.kss-regression.v1`  
Target: concurrent KSS numeric points, 1-9  
Release class: research only; no operational artifact.

This is not a multimodal model, calibrated confidence is unavailable, and its input does not match Stage 1's short-clip closure feature.

## 5. Real validation metrics and locked test

Reproduction command: `rtk proxy .\\.venv\\Scripts\\python.exe -m research.kss_perclos_benchmark_v1.reproduce`  
Exit code: **0**. It verified the workbook checksums and regenerated `research/kss_perclos_benchmark_v1/evaluation_report.json`.

| Metric on four held-out participants / 96 trials | Ridge | Training-mean baseline |
|---|---:|---:|
| Trial-weighted MAE (KSS points) | 1.7907 | 2.1981 |
| Trial-weighted RMSE (KSS points) | 2.3503 | 2.6613 |
| Equal-weight participant MAE | 1.7907 | - |
| Equal-weight participant RMSE | 2.2171 | - |
| Pearson correlation, descriptive only | 0.5013 | approximately 0 |

The observed point estimates favor ridge over the training-mean baseline in this small split. No predeclared meaningful improvement margin exists, and no confidence interval for the paired model-versus-baseline difference was estimated. This is not sufficient to accept a model.

The benchmark's feature/target-range abstention accepted 88/96 test trials (91.7% coverage) and abstained on 8. Accepted-trial MAE was 1.7892 and RMSE 2.2586. This is range guarding, not calibrated selective prediction. Participant-cluster bootstrap (20,000 replicates, seed `20261019`, only four test participants) gave equal-weight participant MAE 95% CI **[1.1865, 2.3949]** and RMSE 95% CI **[1.4503, 2.9839]**. Small cluster count limits interpretation. Calibration, AUROC and class sensitivity/specificity are not reported because the fitted task is numeric KSS regression and its output is not calibrated; KSS is ordinal and preserving it as numeric here is an exploratory benchmark decision, not proof of interval-scale validity.

These metrics are actual held-out results for **driver-study KSS sleepiness**, not fatigue accuracy and not results on Stage 1 image/video/audio extraction. No locked occupational-fatigue test set exists.

## 6. Isolated inference and V2 integration boundary

`rtk proxy .\\.venv\\Scripts\\python.exe research\\kss_perclos_integration\\integrate.py reference` exited **0**. On one verified PLOS locked-test reference row, the research adapter returned numeric KSS estimate `5.855358150733352` for source KSS `4`, with `confidence: null`, `status: research_only`, and `production_enabled: false`. This confirms artifact loading and a reference-feature inference path; one row is not a metric or validation claim.

The adapter rejects Stage 1 video-extractor output as `incompatible_perclos_measurement_method`, and rejects short mobile clips as below the training observation window. Stage 1's output remains unchanged. The generic versioned runtime in `app/ml/runtime.py` remains disabled by default and requires an operational release manifest, verified artifact hash, trusted loader and explicitly enabled activation. API/worker startup does not load this research artifact. No predictor was enabled.

### Stale reference-sample pin corrected

The first isolated integration run exited **1**: 8 tests passed and 2 errored because the stored sample's pinned SHA-256 was stale. Before changing the pin, its sample values were checked against the actual workbook row (Subj17, session 1, trial 1: PERCLOS 2.6111028591223775%, KSS 4), and Subj17 was verified in the participant-disjoint locked test group. The sample file's actual SHA-256 is `b090a9cf1b1d484188490254dc5c6876c55d0faf0d3c50bc4392be4c98a69808`. Updated the pin and its explicit test expectation in `research/kss_perclos_integration/adapter.py` and `research/kss_perclos_integration/test_integration.py`; the corrected integration run passed all 10 tests. No label or model expectation was altered.

## 7. Verification results

| Command | Exit code | Result |
|---|---:|---|
| `rtk proxy .\\.venv\\Scripts\\python.exe -m research.kss_perclos_benchmark_v1.reproduce` | 0 | Source checksum, benchmark reproduction, selected alpha 100, test MAE 1.790666, coverage 0.916667, 4 test participant clusters. |
| `rtk proxy .\\.venv\\Scripts\\python.exe research\\kss_perclos_integration\\test_integration.py` before pin correction | 1 | 8 passed, 2 errored on stale reference-sample hash. |
| Same isolated integration test after correcting verified sample pin | 0 | 10 passed, including artifact hash/schema, reference inference, independent predictor equality, method mismatch, short-window and missing-face abstention. |
| `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_dataset_qualification.py tests/test_research_evaluation.py tests/test_research_regression_training.py tests/test_research_model_input.py tests/test_ml_training_pipeline.py tests/test_short_mobile_kss.py tests/test_kss_perclos_paired_statistics.py tests/test_research_drowsiness_worker.py tests/test_research_image_drowsiness.py tests/test_worker_inference_integration.py tests/test_ai_engine_foundation.py` | 0 | 72 passed, 2 dependency deprecation warnings. Training pipeline synthetic tests are software tests, not scientific validation. |
| Stage 1 media, API, worker, reconciliation, inference-boundary regressions: `rtk proxy .\\.venv\\Scripts\\python.exe -m pytest -q tests/test_audio_vad.py tests/test_video_continuity.py tests/test_synthetic_face_quality.py tests/test_media.py tests/test_mp4_media.py tests/test_real_media_acceptance.py tests/test_isolated_decode.py tests/test_media_feature_adapter.py tests/test_worker_inference_integration.py tests/test_research_drowsiness_worker.py tests/test_worker_startup.py tests/test_api.py tests/test_media_reconciliation.py tests/test_hardening_media_cleanup.py tests/test_ai_engine_foundation.py tests/test_directus_pull.py` | 0 | 162 passed, 2 dependency deprecation warnings in 52.33s. |

## 8. Remaining blockers and exact study needed

Stage 2 cannot close for the intended workplace-fatigue target until the following evidence exists:

1. An occupational-fatigue researcher and Arabic psychometrician approve the exact target, instrument version, language, scoring and pre-shift time window for office/customer-service employees. OFER-15 IR remains a proposal; do not treat KSS or PVT as interchangeable fatigue labels.
2. Written rights-holder permission for the exact questionnaire wording/scoring and research/commercial use, plus applicable ethics, employer/site, consent, privacy, retention and withdrawal approvals.
3. A prospective, target-population study with repeated pre-shift sessions and pseudonymous participant/session linkage; independently collected primary fatigue reference; KSS retained separately as subjective sleepiness; validated PVT protocol and raw trial timestamps retained separately as vigilance. Do not derive labels from the media.
4. Separately consented and synchronized Stage 1 image/video/audio features for those same sessions, explicit modality missingness and device/site metadata, and adequate feature/label timing. Current public datasets do not provide this lawful paired workplace set.
5. A sample-size/precision plan, participant-level splits and custodian-held locked test, predeclared baseline/model/tuning/abstention/calibration/subgroup plan and meaningful acceptance thresholds before test labels are opened. Include external site/device evaluation if that generalization is claimed.
6. Evaluate the selected target with participant-cluster uncertainty, agreement/error, calibration and coverage/abstention. Report subgroup/domain-shift outcomes only where sample support permits. Do not make clinical or fitness-for-duty claims.

No multimodal fatigue artifact was produced. No production predictor was activated. No Flutter, Angular, Directus, legacy service or production system was changed. No model training on synthetic labels occurred. Existing unrelated dirty worktree contents were preserved.

## 9. Exact Stage 2 files changed or regenerated

- `research/kss_perclos_integration/adapter.py` - corrected the verified reference sample digest pin.
- `research/kss_perclos_integration/test_integration.py` - updated the explicit expected digest after source-row and split verification.
- `research/kss_perclos_benchmark_v1/evaluation_report.json` - deterministically regenerated from the licensed source workbook; metrics were reproduced.
- `STAGE_02_FATIGUE_MODEL_REPORT.md` - this delivery report.

## Stage 2 verdict

**BLOCKED for the intended pre-work occupational-fatigue model.** The real KSS sleepiness benchmark, research-only artifact, split checks and isolated inference path are reproducible, but they do not validate the intended target or the Stage 1 multimodal features. Production prediction remains disabled pending the specific reviewed workplace study and locked independent evaluation described above.
