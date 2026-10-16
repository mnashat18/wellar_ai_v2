DROP INDEX IF EXISTS training_labels_expiry_idx;
ALTER TABLE training_labels DROP COLUMN IF EXISTS consent_text_version;
ALTER TABLE training_labels DROP COLUMN IF EXISTS consented_at;
ALTER TABLE training_labels DROP COLUMN IF EXISTS expires_at;
ALTER TABLE training_labels DROP COLUMN IF EXISTS time_of_day;
ALTER TABLE training_labels DROP COLUMN IF EXISTS shift;
ALTER TABLE training_labels DROP COLUMN IF EXISTS focus;
ALTER TABLE training_labels DROP COLUMN IF EXISTS fatigue;
