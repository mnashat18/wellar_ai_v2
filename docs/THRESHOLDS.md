# Rule indicator thresholds

The four labels are rule outputs only: Stable, Low Focus, Elevated Fatigue, and High Risk. Every response says “indicative, non-diagnostic, not a validated classifier.” The thresholds in `config/drowsiness_indicator.json` are engineering gates and have not been calibrated against prospective human labels. They are not accuracy claims and must not be used as a diagnosis or employment decision.

The video measure `short_clip_eye_closure_fraction` is the fraction of observed valid adjacent eye intervals classified closed in the current short clip. It is not a validated clinical measure. The deprecated `perclos_estimate` key is always null for compatibility and is not used for rules or personal baselines.

Face ratio, luminance, blur, observed-eye temporal coverage, coarse normalized face orientation proxies, and optional audio quality can cause `insufficient_quality`. Audio never changes a state. The face orientation proxies are geometric heuristics and have not been validated for camera position, device variation, eyewear, skin tone, occlusion, or demographic groups. A qualified owner must approve any threshold change and its version/hash becomes part of the output.

The personal baseline uses median/MAD over available prior measurements and is descriptive only. A zero MAD or insufficient history suppresses the z-score. Freshness/drift policy is not implemented yet.
