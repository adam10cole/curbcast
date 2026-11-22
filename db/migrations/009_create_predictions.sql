-- migrate:up
CREATE TABLE predictions (
    run_id     bigint             NOT NULL REFERENCES model_runs (id) ON DELETE CASCADE,
    segment_id bigint             NOT NULL REFERENCES segments (id),
    p          double precision[] NOT NULL,
    PRIMARY KEY (run_id, segment_id),
    CHECK (array_length(p, 1) = 168 AND 0 < ALL (p) AND 1 > ALL (p))
);

-- migrate:down
DROP TABLE predictions;