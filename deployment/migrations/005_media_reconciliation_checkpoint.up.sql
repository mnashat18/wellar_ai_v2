CREATE TABLE media_reconciliation_state (
  singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
  cycle_started_at timestamptz,
  after_scan_id text,
  after_reference text,
  lease_owner text,
  lease_until timestamptz
);

INSERT INTO media_reconciliation_state(singleton) VALUES (true);
