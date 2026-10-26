# Directus service role for the V2 pull facade

Grant this role only to the V2 server's service account. Store the token in `WELLAR_DIRECTUS_SERVICE_TOKEN`; never send it to Flutter or include it in logs.

## Required collection permissions

- `wellness_scans`: read one scan by ID and fields `id`, `user`, `business_profile`, `status`; update only `status`.
- `scan_media`: read the row filtered by `scan_id`; fields `scan_id`, `video_file`, `audio_file`, `image_file`.
- Directus file assets: read/download by ID only.
- `scan_results`: read by `scan_id`; create and update the existing result row. Field allowlist: `scan_id`, `risk_level`, `readiness_score`, `confidence`, `ai_model_version`, `baseline_used`, `explanation`, `internal_analysis`, `face_metrics`.
- File deletion: delete only the submitted scan's attached file records after the result and scan status are persisted.

Do not grant collection-wide writes outside these collections, user administration, schema administration, arbitrary file URL access, or access to unrelated business data. Directus permissions must still constrain scans to the authenticated tenant where supported.

## Contract limits

The facade checks `wellness_scans.user` against the authenticated principal and `business_profile` against that principal's tenant ID. It confirms all requested file IDs match the scan's `scan_media` row before downloading. The current Directus schema snapshot uses `business_profile` as the tenant scope; staging must confirm the deployed schema matches the checked-in snapshot.

The legacy `scan_results` snapshot does not expose separate fatigue/focus/risk/confidence score columns with the names parsed by every Flutter version. V2 writes only supported score columns as null and stores the versioned projection in `internal_analysis`; no numeric score is synthesized. When that JSON exceeds the 255-character `internal_analysis` limit, the full details are stored in the JSON `face_metrics` field and `internal_analysis` contains a pointer for recovery.

## Required V2 environment variable names

- `WELLAR_DIRECTUS_PULL_ENABLED`
- `WELLAR_DIRECTUS_URL`
- `WELLAR_DIRECTUS_SERVICE_TOKEN`
- `WELLAR_DIRECTUS_TIMEOUT_SECONDS`

Use HTTPS outside local tests. The V2 outbound HTTP client rejects redirects and non-public DNS addresses. Local fake tests use an in-memory HTTP transport and never call a real Directus service.
