# Wellar AI V2 — Phase 3A Dataset Audit and External Review Package

**Status:** Track A `PARTIAL — EXPERIMENT BLOCKED`; Track B `PREPARED — APPROVALS PENDING`  
**Scope:** Public-data research readiness only. This record does not establish fatigue validity, authorize enrollment, or authorize operational use.

## Track A — Brighter Time audit

### Source, rights, and provenance

The identified current dataset is the University of Manchester Figshare deposit, DOI [10.48420/28911977](https://figshare.manchester.ac.uk/articles/dataset/Relationships_between_light_exposure_and_aspects_of_cognitive_function_in_everyday_life/28911977), posted 12 November 2025. Its deposit metadata identifies a **CC BY 4.0** license and a 75.65 MB archive. The accompanying analysis repository identifies its Brightertime inputs as downloaded from this deposit and describes local collection time as Europe/London, while the R data labels time as UTC for consistency; any reanalysis must verify the conversion and provenance rather than infer it from labels ([author analysis repository](https://github.com/altugdidikoglu/light-cognition-inreallife)). CC BY attribution is required and does not by itself establish the scope of participant consent, ethics approval, or suitability for a different research purpose.

The publication describes 58 participants in the analyzed everyday-life sample, repeated app assessments over approximately one week, and a separate optional laboratory subset of 41. Participants were not a representative shift-worker cohort. The study reports subjective sleepiness using a modified 10-point KSS and cognitive task outcomes including PVT, 3-back, and visual search; reported task sample counts differ. These are repeated, clustered observations, not independent rows. The publication analyzes associations with repeated-measures methods; it does not provide a locked participant-disjoint machine-learning test protocol. See the [2025 study](https://pmc.ncbi.nlm.nih.gov/articles/PMC12789024/) and deposit above.

The deposit contains R data objects for cognitive variables and task-specific KSS, PVT, 3-back and visual-search data, plus tables/figures. No face, video, or voice media are listed as the relevant Brightertime inputs. The older 2022 Brightertime pilot is a distinct dataset/deposit, DOI [10.48420/20212265](https://figshare.manchester.ac.uk/articles/dataset/Data_of_cognitive_task_performance_and_illuminance_collected_by_a_smartphone_app/20212265), also CC BY 4.0; its records and sample size must not be silently combined with the 2025 dataset.

### What the observations can support

Subject to data-file inspection, a carefully scoped secondary analysis could study modified momentary sleepiness (1–10), vigilance/PVT outcomes (including lapse summaries), 3-back working-memory performance, visual-search performance, and their associations with light, sleep, and time variables. The exact feature dictionary, missingness, participant-ID uniqueness, repeat structure, exclusions, and consent purpose still require inspection of the deposited rows and codebook before any model fit.

The dataset does **not** contain Wellar's proposed occupational pre-work fatigue ground truth and does not validate image/video/audio facial or vocal signals. KSS sleepiness and cognitive task performance are separate outcomes, not interchangeable fatigue labels. The modified 10-point KSS also does not match the existing Wellar 1–9 KSS manifest validation; no conversion is approved. Predicting a PVT or cognitive score would be a task-performance/sleepiness research benchmark, not occupational fatigue detection.

### Access and experiment decision

Local inventory checks found no Brighter Time dataset under the expected `data/brightertime`, `datasets`, or `research_data` locations. The public Figshare metadata endpoint was readable, but the archive request returned HTTP 202 with an empty body and browser access returned HTTP 403 in this environment. The archive's `.RData` files could not be parsed: Rscript and Python RData readers were unavailable. No dependencies were installed and no archive or data were saved to the repository. Consequently the actual rows, IDs, label values, feature definitions, and missingness could not be independently checked.

No training or evaluation was run; there is no participant split, metric, confidence interval, or model comparison to report. This is a deliberate gate, not a zero-score result. Existing `research/training.py` supports categorical JSON feature/label training with a minimum of 12 distinct participants and a deterministic participant-disjoint train/validation/test split, but does not itself provide regression for continuous outcomes, a simple comparator, or participant-cluster confidence intervals. Existing `research/evaluation_v1.py` includes split-leakage checks and participant bootstrap intervals for classification metrics. The published 10-point KSS cannot be mapped to the existing 1–9 schema without an approved, documented target decision.

**Decision:** do not train until the exact licensed files can be read and audited, target semantics and permissible feature set are frozen, participant-level IDs and repeats are verified, and an untouched participant-disjoint test protocol plus baseline and uncertainty method are locked in advance. If a future analysis predicts KSS/PVT/cognitive outcomes, report it only as that outcome. Do not tune on the locked test set or claim fatigue detection.

## Track B — External scientific review package

Phase 2.1 selected A-OFER IR as a **proposed** primary candidate for pre-work fatigue and FAS-Ar as a fallback/general-fatigue comparator. The decision remains PROPOSED; neither has been approved for Wellar collection or training.

### Decisions requested from reviewers

1. **Occupational-fatigue researcher:** Is the proposed construct specifically pre-work residual/intershift fatigue, and is A-OFER IR's recall window, construct, and scoring suitable for office/customer-service workers? Define the primary estimand and explain how non-work fatigue sources and recovery period are handled. Confirm that KSS and PVT remain separate measures.
2. **Arabic psychometrician:** Identify the exact Arabic instrument/version and dialect; confirm validated translation, intended-population measurement properties, administration mode, scoring direction, and any needed cognitive interviews. State what evidence is missing before use. Do not infer equivalence from translation alone.
3. **Independent statistician:** Approve the repeated-session estimand, sample-size/precision rationale, missingness and coverage rules, participant-level split/cluster strategy, leakage controls, locked test procedure, uncertainty intervals, and analysis plan before data collection or model evaluation.
4. **Rights and ethics reviewers:** Obtain written permission for the exact questionnaire, Arabic version, scoring, app display, research use, data storage, and any commercial research use. Confirm applicable ethics/IRB approval, site/employer authorization, non-coercive recruitment, privacy/data-protection basis, consent scope, retention/deletion, access, withdrawal, and separate optional consent for any raw image/video/audio. CC BY 4.0 dataset attribution does not replace these approvals.

### Minimum future pilot measurement and independent evaluation

Before enrollment, freeze a reviewed protocol with one approved pre-work fatigue instrument as the primary label. At each session, record the instrument in its validated form and scheduled time window; collect KSS and validated PVT separately as secondary sleepiness/vigilance measures and use disagreements to characterize construct differences, never as dishonesty. Record session timing, work/sleep context, modality attempts and quality/missingness, consent scope, instrument/version, provenance, and de-identified participant/session keys per the existing manifest specification. Do not place direct identifiers or raw media in manifests.

For any later model evaluation, freeze the protocol, extractor/model versions and hashes, preprocessing, and analysis before opening the test labels. Keep all sessions from each participant in one split; use a custodian-held participant-disjoint locked test set and, where the intended claim requires it, a site/device-separated evaluation. Report coverage/abstention and quality-stratified missingness alongside the primary fatigue-score metric and participant-clustered confidence interval; separately report KSS and PVT outcomes. Do not use the public Brighter Time experiment as ground truth for Wellar fatigue, and do not tune on the locked test set.

### Enrollment gate

Real participant enrollment and fatigue-model training remain **BLOCKED** until (a) occupational-fatigue construct/target approval, (b) Arabic instrument and scoring approval, (c) statistical protocol and sample-size/precision approval, (d) written instrument rights, and (e) applicable ethics, privacy, consent, and site approvals are documented. Public secondary-data analysis, if later technically possible, must remain a separately named sleepiness/vigilance/cognition study.
