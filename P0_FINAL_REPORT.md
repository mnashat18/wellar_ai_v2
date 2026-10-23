# Wellar V2 P0 execution report

Run date: 2026-10-16. Work was limited to new backend files. The workspace has no Git metadata, so no repository diff or owner-edit audit was possible. No Flutter, Directus, or Legacy files were touched. No dataset search, dataset audit, training, or pilot-media scientific evaluation was performed.

## Phase status

| Phase | Status | Gate evidence / blocker |
|---|---|---|
| 1. Evidence contract | PASS (backend contract tests) | Added `evidence_schema.json`, Pydantic v1 types, and tests that reject predicted classes/confidence absent the explicit validated-model path. No TypeScript stack exists in this Python backend. |
| 2. Pretrained inference | BLOCKED - needs owner data | Existing MediaPipe extraction executes for image/video and audio quality extraction exists. Existing video output explicitly leaves PERCLOS uncomputed and does not meet the requested complete measures. No permitted owner-provided acceptance media was supplied; pilot media was not used. |
| 3. Personal baseline | BLOCKED - needs owner data | Added rolling median/MAD helper and synthetic tests. It is not connected to per-user durable feature storage; integration depends on the consented feature store in Phase 4. |
| 4. Label capture API | BLOCKED - needs owner data | No label endpoint, feature-only consent store, withdrawal flow, or expiry job was implemented. Phase 1's existing scan submission fatigue item is not equivalent to the requested KSS/fatigue label capture contract. No Flutter changes made. |
| 5. Dormant training/evaluation | BLOCKED - needs owner data | No qualified, consented labelled dataset was provided. No training or scientific evaluation was run. Existing research/training files were not used. |
| 6. Throughput/reliability | BLOCKED (end-to-end gate) | Synthetic SQLite queue/no-op worker measurements completed below. They exclude media, inference, HTTP, and production storage, so do not satisfy the scan-worker throughput gate. Existing retry/lease/idempotency mechanisms were not failure-injection tested in this phase. |
| 7. Regression/security | BLOCKED (not run) | Only the six new contract/baseline tests were run. Full suites and audits were not run because test scope can touch research/pilot paths and the repository has no Git metadata to inspect owner changes. No claim of regression/security clearance. |

## Queue-only measurements

Synthetic SQLite queue + no-op worker, one measured run per concurrency level:

| Submit concurrency | Scans | Queue-to-commit p50 | Queue-to-commit p95 | Completion throughput |
|---:|---:|---:|---:|---:|
| 1 | 50 | 165.866 ms | 194.509 ms | 247.02 scans/s |
| 10 | 100 | 266.341 ms | 361.208 ms | 271.82 scans/s |
| 50 | 250 | 810.777 ms | 1060.946 ms | 232.85 scans/s |

These are not media scan latency or production capacity measurements. The observed bottleneck at 50 concurrent submits is SQLite contention (accept p95 rose to 392.041 ms); the worker itself performed no-op analysis.

Arithmetic from the 50-concurrency queue-only rate: `ceil((2000 scans / 7 seconds) / 232.85 scans/s) = 2` no-op workers. This is a queue-only arithmetic estimate and must not be used to size production inference workers. The requested 2000 real scans/7s capacity remains unmeasured.

## Tests

- New focused tests: 6 passed, 0 failed.
- Full V2 / Collector 17 / training path 28 suites: not run.

## Files added

- `app/evidence/__init__.py`
- `app/evidence/schemas.py`
- `app/evidence/baseline.py`
- `evidence_schema.json`
- `tests/test_evidence_schema.py`
- `tests/test_personal_baseline.py`
- `benchmarks/p0_queue_load.py`

## Not validated

- JSON Schema validation by an independent JSON Schema implementation; tests validate the Pydantic contract and parse the checked-in JSON Schema file.
- MediaPipe output on owner-provided permitted acceptance media; full requested PERCLOS and lighting/blur/pose gates.
- Per-user baseline persistence and API exposure.
- Label retention, consent withdrawal, expiry, and deletion across every V2 storage layer.
- Training/evaluation quality, subject-wise splits, calibration, promotion thresholds, and model registry promotion.
- End-to-end p50/p95 scan latency, production backend throughput, and worker sizing for real inference.
- Full regression suite, log/PII security review, and backward compatibility review.

## Owner-only remaining items

- Supply approved, non-pilot acceptance media and confirm the authorized test set for local functional checks.
- Confirm the label collection protocol, consent language/ownership, permitted retention period, and label-to-scan ownership rules before enabling Phase 4 collection.
- Supply qualified consented human labels and subject identifiers before Phase 5 evaluation; no model result can be scientifically validated without these.
- Confirm the intended production queue/storage configuration and provide the deployment hardware target for end-to-end capacity testing.

ONE next action: approve the label-capture consent and retention policy and provide permitted acceptance media; then implement and gate Phase 2 and Phase 4 before any training work.
