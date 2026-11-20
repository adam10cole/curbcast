-- migrate:up
ALTER TABLE violation_rejects ADD COLUMN field text NOT NULL;

-- migrate:down
ALTER TABLE violation_rejects DROP COLUMN field;