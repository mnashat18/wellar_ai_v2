# Wellar Pilot Dataset Manifest Specification v1

**Purpose:** a governance-aware, de-identified index that links pseudonymous participants and sessions to V2 feature-extraction provenance, separately collected reference measurements, modality/quality states, and consent scope. It is not a media store, identity registry, or authorization to train.

## 1. Storage and linkage architecture

Use one UTF-8 JSONL file with one record per **participant-session-target** (or a normalized relational representation with the same semantics). Do not duplicate raw media into the manifest. Store separately:

- **Identity vault:** direct identifiers and contact/withdrawal details; accessible only to the consent/identity custodian.
- **Linkage vault:** random study participant ID ↔ identity-vault key; separate encryption key and role from the analysis dataset.
- **Research manifest:** random participant/session/sample IDs, coded metadata, feature summaries, reference measurements, consent state/scope references, and immutable provenance hashes; no identity or direct media.
- **Restricted media store:** encrypted original media under random object IDs with separate ACL and deletion lifecycle. The manifest may reference a random media object ID only where retention/consent permits. Object IDs must not encode scan/user/site IDs or filenames.
- **Consent vault:** signed consent and versioned scope record, separate from analytic dataset. Manifest stores opaque consent record ID, version, scope booleans and status only.
- **Split vault:** participant-to-split assignments held by an independent custodian, excluded from analyst-visible manifests until the split is frozen and permitted for development.

Participant IDs must be random, non-derived pseudonyms (not hashes of email, employee number, phone, Directus ID, face, or voice). Session/sample IDs must also be random. Never include names, initials, employee/account IDs, contact data, free-text occupations, exact worksite names, access tokens, biometric templates, raw media, or precise location. Controlled site/device/industry codes may be included only if re-identification risk is assessed; suppress/recode rare combinations.

## 2. Record structure

The following is a **schema illustration**, not a real record. Placeholder values are deliberately non-identifying and no participant/media record is created by this document.

```json
{
  "schema_version": "wellar-pilot-manifest/1.0",
  "study_id": "<approved-study-code>",
  "protocol_version": "<version>",
  "protocol_sha256": "<64-hex-digest>",
  "sample_id": "<random-id>",
  "participant_id": "<random-pseudonym>",
  "session_id": "<random-session-id>",
  "session_sequence": 1,
  "context": {
    "pre_or_post_shift": "pre_shift",
    "site_code": "<controlled-code-or-unknown>",
    "device_code": "<coded-device-version-or-unknown>",
    "os_code": "<coded-os-version-or-unknown>",
    "industry_code": "<approved-category-or-unknown>",
    "occupation_code": "<approved-category-or-unknown>",
    "collection_time_utc": "<ISO-8601-or-null>",
    "clock_sync_offset_ms": null,
    "clock_sync_uncertainty_ms": null
  },
  "consent": {
    "consent_record_ref": "<opaque-vault-reference>",
    "consent_version": "<version>",
    "status": "granted",
    "permissions": {
      "video_capture_and_analysis": false,
      "image_capture_and_analysis": false,
      "audio_capture_and_analysis": false,
      "raw_media_storage": false,
      "research_evaluation": false,
      "model_training": false,
      "cross_site_transfer": false,
      "commercial_use": false
    },
    "withdrawn_at_utc": null
  },
  "v2_provenance": {
    "api_contract_version": "<frozen-contract-version>",
    "client_build_hash": "<digest-or-unknown>",
    "extractor_version": "<version-per-modality>",
    "feature_mapping_version": "<version>",
    "worker_build_hash": "<digest>",
    "configuration_digest": "<non-secret-digest>",
    "processed_at_utc": "<ISO-8601-or-null>"
  },
  "modalities": {
    "video": {
      "availability": "not_collected",
      "capture_start_utc": null,
      "capture_end_utc": null,
      "duration_ms": null,
      "format": null,
      "dimensions_px": null,
      "fps": null,
      "sample_hz": null,
      "quality_status": "not_provided",
      "missing_reason": "not_consented",
      "feature_values": {},
      "raw_media_ref": null
    },
    "image": {"availability": "not_collected", "quality_status": "not_provided", "missing_reason": "not_consented", "feature_values": {}, "raw_media_ref": null},
    "audio": {"availability": "not_collected", "quality_status": "not_provided", "missing_reason": "not_consented", "feature_values": {}, "raw_media_ref": null}
  },
  "references": {
    "kss": {"instrument": "KSS", "version": "<version>", "value": null, "measured_at_utc": null, "status": "not_collected", "missing_reason": "not_collected"},
    "occupational_fatigue": {"instrument_id": "<approved-instrument-and-subscale>", "version": "<version>", "raw_score": null, "scored_value": null, "units_or_range": "<specified>", "measured_at_utc": null, "status": "not_collected", "missing_reason": "not_collected"},
    "pvt": {"instrument_id": "<validated-app-and-device>", "version": "<version>", "protocol_id": "<preregistered-protocol>", "started_at_utc": null, "ended_at_utc": null, "metrics": {}, "status": "not_collected", "missing_reason": "not_collected"}
  },
  "protocol_deviations": [],
  "data_quality_flags": []
}
```

The illustration uses `false` consent booleans to emphasize that consent is not implied. Real ingestion must reject absent/unknown permission rather than defaulting it to true.

## 3. Required semantics and validation

- One session links all modalities and references using the same random participant/session IDs. Include each target's actual measurement timestamp and status. A score without instrument/version/protocol/timestamp is not an eligible reference label.
- Preserve KSS ordinal value 1–9. Store the fatigue instrument's raw item responses only in a separately access-controlled reference table if needed for scoring; never put item-level answers in broadly accessible manifests. Store approved score, scoring algorithm/version, units, range, and subscale. Preserve PVT trial-level data in restricted reference storage; manifest holds preregistered summaries and reference pointer/hash.
- Availability states are explicit enums: `available`, `not_collected`, `not_consented`, `not_provided`, `invalid`, `failed`, `insufficient_quality`, `withdrawn`, `temporally_unmatched`. Every absent value has a reason. Do not impute a missing outcome to a normal/alert value.
- Every modality carries a separate quality state, actual time interval, extractor/version and feature dictionary. Empty feature dictionary is not interpreted as zero values. Represent unavailable numerical features as null and retain feature missingness/coverage.
- V2 contract/extractor/config/build versions and non-secret content digests must be immutable. Hashes must not be derived from direct identifiers or published if they can enable media linkage. Keep media checksums in the restricted media vault; use keyed digests if exposed outside it.
- Consent scope is modality-specific and purpose-specific. `evaluation`, `model_training`, `raw_media_storage`, cross-site transfer and commercial use are separate permissions. Withdrawal status propagates to the data custodian and invalidates training eligibility as required by the consent/ethics policy.
- Target class labels, if later created, include rule ID, threshold source, protocol hash and software version. Continuous/raw reference remains authoritative. No labels based on V2 heuristic thresholds.
- Controlled metadata is minimized. Age may be an approved broad bin, never birthdate; use coded shift/time bands rather than exact schedules when possible. Do not collect demographic fields without a prespecified scientific purpose and approved privacy review.

## 4. Rights and training-eligibility view

The analysis-ready dataset is a derived view joining only eligible records. A record is eligible for a given target only if all applicable conditions are true:

1. Valid consent status and exact scope permit that target's media processing, evaluation, and (for training) model training; ethics/privacy approval is current.
2. Required reference is observed, from the authorized instrument/protocol, scoreable, correctly timed, and not withdrawn.
3. Required modalities are present and satisfy the preregistered quality gate; missing modalities are retained in the master dataset and excluded only from the relevant model/evaluation set under the preregistered policy.
4. Provenance is complete: protocol digest, client/API contract, V2 extractor and feature-map versions, device/site codes, timestamps, and data custody chain.
5. License/participant consent/data-use agreement permits the specific research/training purpose and transfer context. A research-use grant is not assumed to include commercial use.
6. Participant is assigned to exactly one frozen split. No session/media/person overlap exists across splits. External device/site records are flagged and held outside development.

An independent custodian should create an aggregate eligibility report with counts by participant, target, site/device and missingness reason; do not expose participant IDs or small cells. Eligibility is not proof the sample is sufficiently powered or scientifically representative.

## 5. Access, audit, retention and deletion

Role-based access: identity custodian can resolve identity but cannot train models; data custodian can manage consent/media/splits; approved analysts can access pseudonymous features and permitted labels; raw-media access requires separate approval and time-bounded access. Log access and exports. Prohibit local copies and unapproved generative-AI/public service uploads. Encrypt keys separately from encrypted data. Test deletion workflow before enrollment using synthetic objects only.

Store retention expiry per study asset class: identity/linkage, raw video, raw image, raw audio, derived features, labels, and backups may have different approved deadlines. Deletion must cover primary and backup copies and generate an audit event without retaining deleted content. A withdrawal request must resolve via the linkage custodian without giving analysts identity access. Exact retention durations are **unresolved** and must be set in ethics-approved consent before collection.

## 6. Relation to existing V2 research code

This spec is a study-level governance contract, not a claim that current `research/schema_v1_1.json`, `research/pilot_dataset.py`, or `research/evaluation_v1.py` fully implement it. Before ingestion, map fields explicitly and conduct schema/privacy validation. Existing V2 validation code is software infrastructure, not approval, consent verification, or scientific qualification. Do not alter the operational API to carry consent identity or research IDs.
