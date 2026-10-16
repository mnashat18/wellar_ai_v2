# V2 code health notes

The production API/worker path is `app/api`, `app/scans`, `app/media`, `app/queue`, and `app/workers`. Research/model registry code remains under `app/ml` and `research`; worker entrypoint currently imports the optional research image classifier, but its configuration rejects enabling it outside development/test. Do not expose those results as production fatigue/focus states.

Several runtime modules contain dense one-line statements and large functions, including `app/media/analysis.py`, `app/media/extractors.py`, and queue adapters. They were not refactored in this hardening pass because behavioral changes and incomplete failure coverage make broad restructuring risky. Split only in a follow-up with focused regression coverage.

The repository does not contain Git metadata in this workspace, so a complete owner-change diff is UNVERIFIED. Current-turn backups are under `backups/v2-hardening-20261016/`; one exception is documented in `V2_PROGRESS.md`: `app/api/v1.py` was first edited before its current-state snapshot was copied.
