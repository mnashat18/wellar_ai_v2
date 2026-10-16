CREATE TABLE IF NOT EXISTS training_labels (
  scan_id text PRIMARY KEY,
  tenant_id text NOT NULL,
  subject_id text NOT NULL,
  kss integer NOT NULL CHECK (kss BETWEEN 1 AND 9),
  features_json jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (scan_id) REFERENCES scans(scan_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS training_labels_owner_created_idx
  ON training_labels (tenant_id, subject_id, created_at DESC);
