-- migrate:up
CREATE TABLE ingestion_runs(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_month text NOT NULL,
    stage text NOT NULL CHECK (stage IN ('extract', 'normalize', 'load')),
    status text NOT NULL CHECK (status IN ('running', 'succeeded', 'failed')),
    started_at timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    rows_expected integer,
    rows_clean integer,
    rows_rejected integer,
    error text
);

CREATE INDEX ingestion_runs_month_idx ON ingestion_runs (source_month, started_at DESC);

-- migrate:down
DROP TABLE ingestion_runs;