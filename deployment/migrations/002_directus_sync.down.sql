DROP INDEX IF EXISTS outbox_delivery_idx;
DROP TABLE IF EXISTS directus_scan_bindings;
ALTER TABLE outbox
  DROP COLUMN IF EXISTS delivery_status,
  DROP COLUMN IF EXISTS attempts,
  DROP COLUMN IF EXISTS available_at,
  DROP COLUMN IF EXISTS lease_until,
  DROP COLUMN IF EXISTS fencing_token,
  DROP COLUMN IF EXISTS publisher_id,
  DROP COLUMN IF EXISTS last_error_code;
ALTER TABLE scans DROP CONSTRAINT IF EXISTS scans_identity_triplet_unique;
