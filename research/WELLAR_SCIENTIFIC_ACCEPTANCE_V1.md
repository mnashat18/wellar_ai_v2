# Wellar Pilot Scientific Acceptance Criteria v1

**Status:** preregistration checklist and decision framework. No numerical performance target is asserted here. Thresholds and meaningful margins must be approved by domain experts and frozen before outcome analysis.

## 1. Separate target-specific decisions

Assess each target separately. Success for KSS does not validate fatigue or PVT, and success on one modality or device does not validate another.

| Target | Reference and primary outcome | Prespecified primary metrics (select/freeze before analysis) |
|---|---|---|
| Subjective sleepiness | KSS 1–9 ordinal score at protocol-defined time | Ordinal absolute error / MAE, ordinal calibration and weighted kappa; report full score distribution. If a validated ordinal regression is used, preserve ordinal structure. |
| Occupational fatigue | Approved Arabic independent instrument and specific subscale/score/time window | MAE with 95% participant-cluster CI for a continuous score; concordance correlation or prespecified association as secondary. Do not score it using KSS. |
| Vigilance | Validated PVT raw trial data and prespecified metric(s) | Error for continuous latency/lapse summaries; if a threshold-defined class is justified, sensitivity, specificity, AUROC, calibration and class prevalence. Threshold and lapse definition must be frozen. |

The pilot must select one primary endpoint per target and state whether the goal is concurrent estimation, ordinal discrimination, or another explicit estimand. Association alone is not predictive performance, and correlation alone is not agreement.

## 2. Minimum evidence gates before any training

Training suitability requires **all** of the following:

1. **Rights and ethics:** current ethics/privacy approval; instrument and dataset rights documented; consent permits the exact media processing, evaluation, and training purposes; withdrawal and deletion processes work; no unresolved commercial-use assumption.
2. **Measurement validity:** exact KSS/PVT protocol and fatigue instrument selected; fatigue instrument language/cultural/content validity is adequate for the target population and intended pre-work time window; scoring software independently verified; reference timestamps and synchronization meet prespecified bounds.
3. **Data integrity:** schema/provenance/consent validation passes; no direct identifiers in analysis data; every row has participant/session linkage, extractor and protocol versions; no unexplained missingness or duplicate media; all media and labels have explicit states.
4. **Information adequacy:** sample size is justified before enrollment by participant-level precision/power methodology; enough unique participants and outcome variation per target and split; class/score support is sufficient without relying on repeated clips as independent n; missingness, abstention and modality coverage are described. A software minimum is not scientific sufficiency.
5. **Leakage control:** participant-disjoint train/validation/locked-test assignment is sealed; all repeated sessions/media of a participant remain in one split; exact and near-duplicate media checks completed in restricted environment; no test data used for feature/model/threshold selection.
6. **External applicability:** a truly held-out device/site cohort is reserved if the intended claim includes generalization beyond the development device/site. Otherwise that claim is explicitly out of scope. Subgroup analyses meet prespecified support and privacy minimums.
7. **Protocol lock:** protocol, target mapping, feature list, preprocessing, model family, tuning plan, baseline, primary metrics, uncertainty method, meaningful margins, subgroup plan, missingness rules and acceptance rules are versioned and hashed before model fitting.

Failure of a gate means **not ready for model training**. It may still support descriptive feasibility reporting.

## 3. Analysis, baselines, and uncertainty

- Compare any candidate with a prespecified simple baseline (e.g., training-partition mean/ordinal majority or a preregistered demographic-free reference-only baseline). Fit all transforms, feature selection, imputation, calibration, and hyperparameter selection inside development partitions only.
- Report participant counts and session counts separately for each partition, target, site/device, and missingness/quality status. Include score/class distribution and repeated-measure structure.
- Compute 95% uncertainty intervals by participant-cluster bootstrap (resample participants, retaining all their sessions); use a prespecified number/seed and method. For external site/device holdouts, report uncertainty and limitations of the number of independent sites/devices. Never calculate confidence intervals as though each frame/session were an independent participant.
- For continuous outcomes report MAE (and RMSE if useful), calibration/agreement, and score-range plots. For ordinal outcomes report ordinal error, weighted kappa and calibration. For classification report confusion matrix, sensitivity/specificity, AUROC and precision-recall as appropriate, plus calibration (Brier score/reliability curve) and prevalence. Accuracy alone is insufficient.
- For every metric report coverage and abstention rate overall and by input-quality/missing-modality state. Quality failures and abstentions are outcomes to report, not silently discarded. Provide performance both among eligible predictions and end-to-end coverage.
- Predefine missing reference, missing modality, invalid PVT, temporal mismatch, withdrawal, protocol deviation, and non-random dropout handling separately. Do not impute missing target labels for primary analysis. Provide a flow table and sensitivity analysis only if specified before unblinding.
- Do not use a single arbitrary confidence cutoff. Any abstention policy is trained/calibrated on development data, frozen before locked testing, and evaluated for both selective error and retained coverage.

## 4. Acceptance rule (thresholds intentionally unfilled)

For each target, a candidate advances only if all prespecified criteria are met on the untouched participant-disjoint locked test, and separately on required external-device/site data:

- Candidate improvement over the chosen baseline exceeds a domain-expert-approved, meaningful margin; the confidence interval and multiplicity approach support that conclusion.
- Error and calibration satisfy thresholds set before analysis, with no clinically/safety unacceptable failure mode identified by the independent review panel.
- Coverage/abstention satisfies the prespecified minimum in the intended capture conditions. The model does not silently return a value for out-of-domain or low-quality media.
- No prespecified subgroup shows an unacceptable error disparity; uncertainty and sample support are adequate. Small or underpowered groups are reported as inconclusive, not “fair.”
- Locked-test procedure is intact; code, extractor, model, protocol and data hashes match frozen records; analysis is reproducible by an independent analyst.

Set the numerical meaningful margin, calibration tolerance, minimum coverage, subgroup support, and unacceptable-error thresholds in the preregistration after instrument selection and stakeholder/risk review. Do not backfill them after seeing results. Do not promise 90% accuracy or any other performance not supported by this process. Passing this pilot indicates target-specific research evidence only; it does **not** authorize clinical, employment, safety-critical or operational use.

## 5. Operational acceptance is separate

Predictive/scientific acceptance and service performance are independent release dimensions. A scientifically acceptable model can still be operationally unusable; a fast system can still be scientifically invalid.

Before any service-scale claim, define and run a separate load-test plan against a staging-equivalent full path: real client upload, authentication, media storage, queue, worker decode/features/inference, results, cleanup, and failure handling. Include cold/warm starts, target devices/networks, concurrency ramp and sustained load, upload size mix, queue backpressure, retries, dependency outage, memory/CPU, p50/p95/p99 end-to-end latency, throughput, error rates, and recovery. Publish test environment, workload, repetitions and confidence intervals. No claim of 2,000 simultaneous scans under 10 seconds is acceptable without this independent evidence.

Operational gate also requires privacy/security review, observability without media/token/identity leakage, retention/deletion verification, rollback/kill switch, model artifact provenance and integrity, deployment parity, and a non-diagnostic user communication plan. None of these operational checks can substitute for independent target validation.

## 6. Results wording and prohibited inferences

Report uncertainty and limitations prominently. Use “sleepiness estimate” only for a model validated against KSS; “occupational fatigue estimate” only against the selected independent fatigue instrument; and “vigilance performance estimate” only against PVT. Do not call facial closure fractions PERCLOS unless a separately validated protocol supports that measure. Do not equate drowsiness labels, KSS, fatigue, and PVT. Do not claim causal inference, clinical diagnosis, fitness-for-duty, generalization to untested populations/devices, commercial rights, or a target accuracy from a small feasibility pilot.
