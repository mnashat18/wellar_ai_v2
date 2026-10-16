ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS fatigue integer CHECK (fatigue BETWEEN 1 AND 10);
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS focus integer CHECK (focus BETWEEN 1 AND 5);
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS shift text;
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS time_of_day text;
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS expires_at timestamptz;
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS consented_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE training_labels ADD COLUMN IF NOT EXISTS consent_text_version text NOT NULL DEFAULT 'wellar-v2-training-consent-v1';
CREATE INDEX IF NOT EXISTS training_labels_expiry_idx ON training_labels(expires_at) WHERE expires_at IS NOT NULL;
