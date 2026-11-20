-- migrate:up
CREATE TABLE violation_rejects(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_month text NOT NULL,
    source_objectid bigint,
    reason text NOT NULL,
    raw jsonb NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX violation_rejects_month_idx ON violation_rejects (source_month);

-- migrate:down
DROP TABLE violation_rejects;