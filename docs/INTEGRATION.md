# V2 facade integration contract

This is a backend contract note for a future Flutter integration. Flutter, Directus, and legacy services remain read-only. The checked-in maps were copied from the specified Flutter repository.

## Legacy Directus pull facade

`POST /process` accepts the JSON body used by the legacy integration map: `scan_id`, `user_id`, `video_file_id`, `audio_file_id`, optional `image_file_id`, `task_metrics`, `device_info`, and `environment_info`. It returns `202` after durable enqueue and uses the Directus scan ID as its idempotency key. The caller must be authenticated; the principal subject must equal `user_id`.

The worker reads files from the configured Directus origin by file ID only, confirms `wellness_scans.user`, `business_profile`, status, and `scan_media` file relationships, applies MIME/signature/size caps, stages copies in the V2 media store, invokes the standard processor, writes the legacy projection, updates Directus scan status, and deletes staged and Directus media with retries. `deletion_proven` is persisted in V2's completed scan result and Directus `internal_analysis`. See [DIRECTUS_SERVICE_ROLE.md](DIRECTUS_SERVICE_ROLE.md) for permissions.

Accepted bytes are bounded to MP4 <20 MiB, JPEG <5 MiB, and WAV <8 MiB. The outbound service token variable is `WELLAR_DIRECTUS_SERVICE_TOKEN`; it is never exposed to clients. Checked-in compatibility references: [LEGACY_AI_INTEGRATION_MAP.md](LEGACY_AI_INTEGRATION_MAP.md) and [V2_COMPATIBILITY_PLAN.md](V2_COMPATIBILITY_PLAN.md). The Directus schema snapshot has fewer dedicated score fields than the legacy Flutter parser; unsupported score values remain null in the projection.

## Authentication and submission

V2 expects bearer authentication configured as OIDC or Directus service authentication. Environment variable names are `WELLAR_AUTH_MODE`, `WELLAR_OIDC_ISSUER`, `WELLAR_OIDC_AUDIENCE`, `WELLAR_OIDC_JWKS_URI`, `WELLAR_OIDC_TENANT_CLAIM`, `WELLAR_DIRECTUS_URL`, and `WELLAR_DIRECTUS_TIMEOUT_SECONDS`; provide values through the deployment secret/configuration manager. A facade must forward an authenticated identity that resolves to a tenant and subject in V2.

For `POST /api/v1/scans`, send `Idempotency-Key` equal to the stable legacy Directus scan ID. V2 upload caps are MP4/video <20 MiB, JPEG/image <5 MiB, and PCM WAV/audio <8 MiB under defaults; deployments must check configured caps because these are configurable.

## Client-computed measurements

`POST /api/v1/scans/measurements` accepts validated client-supplied eye/face measurements and returns the same rule indicator and quality gates without media upload. The response explicitly marks `source=client_measurements` and `measurement_provenance=client-supplied, unverified`. This endpoint is not a trust boundary for scientific or employment decisions. A Flutter client would need to compute these measurements on-device; this has not been implemented or verified in Flutter.

## Legacy projection

`app.compat.legacy_projection.project_v1` is a pure projection helper. It maps only the four allowed indicator labels, maps quality abstention to `retake_required`, and always leaves legacy numeric score/confidence fields null. No owner-provided `LEGACY_AI_INTEGRATION_MAP.md` or `V2_COMPATIBILITY_PLAN.md` was found under `D:\flutter\last`; exact external legacy field compatibility is therefore unverified.
