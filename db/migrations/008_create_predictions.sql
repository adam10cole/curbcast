-- migrate:up
CREATE TABLE predictions (
    run_id bigint NOT NULL REFERENCES model_runs (id) ON DELETE CASCADE,
    x double precision NOT NULL,
    y double precision NOT NULL,
    weekday smallint NOT NULL (weekday BETWEEN 0 AND 6),
    hour smallint NOT NULL CHECK (hour BETWEEN 0 AND 23),
    p double precision NOT NULL CHECK (p > 0 AND p < 1),
    PRIMARY KEY (run_id, x, y, weekday, hour)
);

-- migrate:down
DROP TABLE predictions;
