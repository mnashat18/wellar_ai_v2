ALTER TABLE scans
  ADD CONSTRAINT scans_identity_triplet_unique UNIQUE (scan_id, tenant_id, subject_id);

CREATE TABLE directus_scan_bindings (
  scan_id text PRIMARY KEY,
  directus_scan_id text NOT NULL UNIQUE,
  tenant_id text NOT NULL,
  subject_id text NOT NULL,
  remote_owner_id text NOT NULL,
  remote_tenant_id text NOT NULL,
  remote_scan_status text NOT NULL,
  membership_active boolean NOT NULL CHECK (membership_active),
  media_owner_verified boolean NOT NULL CHECK (media_owner_verified),
  verification_version text NOT NULL DEFAULT 'directus-binding-v1',
  verified_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (scan_id, tenant_id, subject_id)
    REFERENCES scans(scan_id, tenant_id, subject_id)
);

ALTER TABLE outbox
  ADD COLUMN delivery_status text NOT NULL DEFAULT 'pending'
    CHECK (delivery_status IN ('pending', 'running', 'delivered', 'dead')),
  ADD COLUMN attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  ADD COLUMN available_at timestamptz NOT NULL DEFAULT now(),
  ADD COLUMN lease_until timestamptz,
  ADD COLUMN fencing_token bigint NOT NULL DEFAULT 0 CHECK (fencing_token >= 0),
  ADD COLUMN publisher_id text,
  ADD COLUMN last_error_code text;

CREATE INDEX outbox_delivery_idx
  ON outbox(delivery_status, available_at, lease_until)
  WHERE published_at IS NULL;
