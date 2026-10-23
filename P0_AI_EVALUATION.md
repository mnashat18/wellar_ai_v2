# Wellar V2 P0 scientific evaluation

## Gate result

Predictive accuracy for Wellar users and targets is **UNMEASURED**. No lawful, compatible, participant-labeled five-second RGB/video dataset with the required fatigue, sleepiness, attention, or four-state labels was available for this run. The private-pilot scope authorizes local media analysis only, not training or scientific evaluation. No pilot media were used as ground truth, training, or test data.

## Target validity matrix

| Target | Real trained model | Genuine inference run | Compatible ground truth | Independent evaluation | Calibrated threshold | Production activation |
|---|---:|---:|---:|---:|---:|---:|
| Observable image driver-drowsiness binary comparator | Yes, third-party pretrained image classifier | Yes, actual worker-path invocation | No Wellar/user-context labels | No Wellar or participant-disjoint evaluation reproduced | No | No |
| Fatigue | No compatible model | No | No | No | No | No |
| Sleepiness / KSS | Existing five-minute pupil-diameter research model only; incompatible with five-second RGB | No for mobile input | No compatible five-second labels | No | No | No |
| Focus / vigilance impairment | No compatible model | No | No | No | No | No |
| `stable`, `low_focus`, `elevated_fatigue`, `high_risk` | No | No | No | No | No | No |

The third-party model card reports 98.18% accuracy, APCER 3.57%, BPCER 0%, and ACER 1.78% on what it calls an unseen test set. These are publisher-reported figures, without test-set support counts, participant-disjoint protocol, confidence intervals, or local independent reproduction. They are not Wellar performance and are not repeated as achieved metrics here. [Publisher card](https://huggingface.co/mosesb/drowsiness-detection-mobileViT-v2).

The image model emits raw binary labels and uncalibrated softmax values solely inside a `research_only` evidence extension. They are not returned as fatigue/focus/risk predictions and are not calibrated probabilities. Unsupported production target outputs stay unavailable. Missing/poor-quality evidence abstains; the system does not silently report `stable`.

## Limitations

- Driver images and their domain may differ from employee mobile selfies, framing, lighting, camera angle, eye visibility, demographic representation, and capture conditions.
- The model is a single-image classifier and has no temporal context. It cannot calculate blink dynamics or infer five-second video behavior; the V2 video path only measures the existing observable face/eye features.
- Drowsiness, subjective fatigue, sleepiness, focus, and operational risk are related but distinct constructs.
- Dataset permissions and annotation/participant protocol for the selected model's linked training sources were not established. Commercial use is blocked.
- There is no independent evaluation, calibration, abstention-coverage estimate, or confidence interval for Wellar.
- No audio model qualified for fatigue/sleepiness. WAV output remains signal quality/features only, with no fatigue claim.

No classifier training or evaluation was performed. No fabricated classes, labels, scores, or metrics were created.
