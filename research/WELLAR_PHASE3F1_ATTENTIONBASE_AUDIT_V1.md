# Phase 3F.1 — AttentionBase audit (verified CSV)

## Decision

- **SAFE FOR FEATURE COMPATIBILITY RESEARCH:** Yes, with strict limits. The local CSV can be audited and its 68 coordinate pairs can support structural compatibility investigation. EAR/MAR are not yet compatible with Wellar's normalized features.
- **ELIGIBLE FOR SUBJECT-INDEPENDENT BENCHMARK:** No. The CSV has no participant or session identifier columns; timestamp segments cannot establish participant identity.
- **BLOCKED FOR SCIENTIFIC FATIGUE VALIDATION:** Yes. The publisher describes visual-attention/drowsiness research and the 2021 related paper describes manually labelled attention, not independently measured occupational fatigue.

No model was fitted and no random-row accuracy is reported.

## Local file and schema verification

Read-only inspection of `research/public_data/attentionbase/data.csv` found a UTF-8 CSV of 7,934,060 bytes, **14,127 rows × 140 columns**. The schema is 68 `x_0..x_67` / `y_0..y_67` coordinate pairs, `EAR`, `MAR`, `Label`, and `timestamp`. No column name indicates participant, subject, person, session, video, or recording ID. No blank/missing cells or non-finite numeric values were found. There are **0 fully duplicated rows**. Four rows repeat an identical 68-point coordinate vector (and the corresponding non-timestamp feature/label tuple); this is a small but real repeated-measurement/duplicate risk, not evidence of identity.

Label counts are:

| CSV code | Rows | Share |
|---|---:|---:|
| H | 5,250 | 37.15% |
| L | 3,472 | 24.58% |
| M | 3,328 | 23.56% |
| N | 2,077 | 14.70% |

Coordinates are integer-valued in image-like ranges: X **−6..644**, Y **−8..475** (combined extrema −8..644); **1,921,268 of 1,921,272** coordinate scalar values fall outside [0,1]. This is direct evidence they are not normalized [0,1] coordinates. The out-of-frame negative and high values should be treated as coordinate outliers/possible detector artifacts pending image dimensions and coordinate convention; no rows were removed. All coordinates are finite.

`EAR` ranges **7..594** (median 278, mean 277.722); `MAR` ranges **0..461** (median 282, mean 278.850). EAR has 482 distinct values; MAR has 482 distinct values and all values lie on half-unit increments. These scales do not match Wellar's normalized landmark geometry feature contract, where eye/mouth ratios are computed from normalized MediaPipe landmark coordinates. Same column names do not establish same formula, landmark index order, scale, or preprocessing. No mapping was implemented.

## Labels and publisher evidence

The official Mendeley record states 14,127 samples from 200 participants during reading and browsing, 20 FPS, facial landmarks/EAR/MAR/timestamps, and intended visual-attention/drowsiness/HCI research; it lists CC BY 4.0. Its displayed description does not define the CSV's H/L/M/N codes or say how each CSV row was labelled. The dataset record's rendered Files section is empty, so the local CSV's acquisition/provenance chain cannot be checked from that listing alone.

The related 2021 publisher research-output page says 10,197 instances were manually labelled for attention level from eye movement/video and describes visual focus of attention. An earlier chapter by the same research group describes qualitative levels High, Medium, Low, and No Attention. Those sources support an attention-label vocabulary and manual attention annotation in related work, but **do not document the code crosswalk `H=High`, `L=Low`, `M=Medium`, `N=No attention` for this 2025 CSV**. That mapping remains unverified and must not be asserted as fact. The CSV is not an independently measured fatigue target. Mendeley's CC BY 4.0 notice requires attribution, license link, indication of changes, and no implied endorsement; it does not by itself settle privacy/publicity rights for derived biometric data.

Sources: [official Mendeley record](https://data.mendeley.com/datasets/zdf2cvf6p2/1), [2021 publisher research record](https://researchoutput.csu.edu.au/en/publications/a-human-robot-interaction-system-calculating-visual-focus-of-huma/), [2021 article PDF](https://researchoutput.csu.edu.au/files/440394643/440282181_Published_article.pdf), [earlier chapter](https://link.springer.com/book/10.1007/978-981-15-3607-6?page=2), [CC BY 4.0 deed](https://creativecommons.org/licenses/by/4.0/).

## Time, dependence, duplicates, and leakage

Timestamp range is **0.0..35.0 s**, with **351 distinct values**. There are **133 backward timestamp resets**, yielding **134 contiguous timestamp segments** if segment starts are defined mechanically as row 0 plus each negative timestamp step. Segment lengths range **1..351**, median **107**; three segments contain at most two rows. There are no equal adjacent timestamps. Most positive adjacent intervals are 0.1 s (5,982 instances); 22 are 0.2 s and other gaps occur, including several >7 s. These observations do not establish that segments are people, sessions, or source videos. The publisher's 200-person claim cannot be reconciled to row-level identities with the accessible CSV.

There are 124 adjacent label changes in file order (two of these occur exactly at timestamp resets); row order and timestamp are therefore sequence-like, and nearby rows are likely correlated. Repeated coordinates, temporal adjacency, and absent identity keys make random-row splitting leakage-prone. Segment-based splits are also not subject-disjoint. No participant-independent benchmark is defensible from this file.

The four classes have overlapping EAR/MAR ranges, and the two measurements alone do not encode a documented target semantics. Class-conditional EAR means (H 280.801, L 279.066, M 270.444, N 279.353) and MAR means (H 280.634, L 279.079, M 272.599, N 283.973) are descriptive only, not construct validation. EAR/MAR values and labels may have been produced from the same face/attention processing pipeline; without collection and annotation provenance they carry target leakage risk. No predictive analysis was run.

## Wellar feature compatibility

Inspected `app/media/extractors.py` and `research/media_feature_adapter.py` read-only. Wellar derives EAR and MAR from selected MediaPipe landmark indices in normalized coordinates; the video path additionally emits sampled temporal closure/coverage features. AttentionBase provides one row of 68 paired coordinates and two derived scalar columns, but its 68-point topology/index mapping, dimensions, formula, scale, source image preprocessing, and codebook are not established by the dataset page or CSV headers. The 68-point representation is structurally different from Wellar's MediaPipe Face Mesh representation. Therefore:

- Coordinate rows are usable for schema/geometry compatibility research only; direct landmark correspondence is unverified.
- CSV EAR/MAR must not be renamed or passed through as Wellar `eye_aspect_ratio` / `mouth_aspect_ratio`.
- The CSV has no audio, source image/video, face coverage, quality, or missingness fields; zero missing cells do not mean face landmarks were valid or high quality.
- No extractor was run on source media because the CSV contains features rather than the underlying media.

## Benchmark decision and safe exploratory option

There are no subject/session identifiers and no verified label codebook. Thus neither subject-disjoint performance nor a faithful target definition is available. **Do not train now.** If the publisher supplies the codebook and a lawful, documented split/identity key, a future strictly exploratory experiment could assess row/contiguous-block prediction of visual-attention codes only. With the current file, a within-file exercise would require publisher-confirmed target meanings, temporally blocked evaluation, removal of near-duplicate/repeated observations across folds, no claims of person generalization, and clear exploratory-only labeling. It would still not validate fatigue.

## Files and tests

- Inspected: the CSV above, `app/media/extractors.py`, `research/media_feature_adapter.py`, and this existing Phase 3F.1 report.
- Changed: this report only.
- Dataset: preserved in place; not downloaded, rewritten, or modified.
- Production code/tests: unchanged; automated tests not run (0 passed, 0 failed, 0 skipped). The work was a read-only CSV audit plus this report update.

