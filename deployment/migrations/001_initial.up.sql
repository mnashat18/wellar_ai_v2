CREATE TABLE scans (
  scan_id text PRIMARY KEY, tenant_id text NOT NULL, subject_id text NOT NULL,
  idempotency_key text NOT NULL, payload_hash text NOT NULL, payload_json jsonb NOT NULL,
  job_id text NOT NULL UNIQUE, status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
  request_id text NOT NULL DEFAULT '-', UNIQUE(tenant_id, subject_id, idempotency_key)
);
CREATE TABLE jobs (
  job_id text PRIMARY KEY, scan_id text NOT NULL UNIQUE REFERENCES scans(scan_id),
  status text NOT NULL, attempts integer NOT NULL DEFAULT 0, available_at timestamptz NOT NULL DEFAULT now(),
  lease_until timestamptz, fencing_token bigint NOT NULL DEFAULT 0, worker_id text, last_error text
);
CREATE INDEX jobs_claim_idx ON jobs(status, available_at, lease_until);
CREATE TABLE results (
  scan_id text PRIMARY KEY REFERENCES scans(scan_id), tenant_id text NOT NULL, subject_id text NOT NULL,
  result_json jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE outbox (
  event_id text PRIMARY KEY, scan_id text NOT NULL, event_type text NOT NULL,
  payload_json jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), published_at timestamptz,
  UNIQUE(scan_id,event_type)
);
CREATE TABLE media_assets (
  media_id text PRIMARY KEY, tenant_id text NOT NULL, subject_id text NOT NULL,
  modality text NOT NULL, content_type text NOT NULL, size_bytes bigint NOT NULL,
  sha256 text NOT NULL, relative_path text NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX media_tenant_retention_idx ON media_assets(tenant_id,created_at);
