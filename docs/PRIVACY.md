# V2 data handling and privacy checklist

This is a technical inventory, not legal advice. The applicable policy, lawful basis, controller responsibilities, and retention duration require owner and counsel decisions.

## Data received and stored

- Scan uploads accept image, video and WAV audio. Local storage keeps media files plus opaque media metadata until deletion/retention; S3 mode stores private objects and metadata. Media is intended to be deleted after processing. Resolve errors, poison jobs, crash paths, stale scratch cleanup, and live S3 deletion have not all been verified; see `V2_PROGRESS.md` Stage 1.
- Completed scan results store extracted feature envelopes and the rule indicator. The backend does not need raw media to compute the optional personal baseline after the scan is complete.
- Consented labels store KSS and optional fatigue/focus/shift/time-of-day values plus an allowlisted numeric feature object. `WELLAR_LABEL_RETENTION_DAYS` is required to accept training-consented labels. Current consent metadata includes timestamp and text-version identifier; withdrawal routes delete retained label rows.
- Logs include structured event metadata. Request, scan, job and worker identifiers are pseudonymized by the JSON formatter; raw W3C trace IDs remain for correlation. Do not enable unreviewed access logs or exporters that include URLs, tokens, user IDs, media names or feature vectors.
- Database backups, object-store versions, infrastructure logs, and external telemetry deletion propagation are deployment-owner decisions and are not verified by code tests.

## Technical safeguards present

Bearer identity, tenant/subject ownership checks, fixed label-feature allowlist, per-modality size caps, type/signature checks, image/video dimension/frame/duration checks, media ownership, non-public object storage guidance, consent-off non-retention, withdrawal, expiry fields/job, sanitized structured logs, and bounded metric labels are implemented to varying degrees. Live distributed backend verification remains an owner action.

## Owner checklist for Law 151/2020 implementation

Obtain qualified legal review. Decide and document: user notice and purpose; lawful basis and consent wording; whether this data includes sensitive personal data and applicable safeguards; access/correction/erasure/restriction/objection handling; data minimization; retention schedules across databases, object stores, logs, caches and backups; processor/vendor terms; cross-border transfer review; security incident/breach workflow; access review and audit records; Arabic and accessible user notices; and the designated controller/DPO responsibilities. The owner must confirm the applicable current regulatory requirements and implementation dates.
