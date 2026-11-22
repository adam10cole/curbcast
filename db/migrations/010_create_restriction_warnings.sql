-- migrate:up
CREATE TABLE restriction_warnings (
    run_id bigint NOT NULL REFERENCES model_runs (id) ON DELETE CASCADE,
    segment_id bigint NOT NULL REFERENCES segments (id),
    kind text NOT NULL CHECK (kind IN ('rush_hour_am', 'rush_hour_pm', 'street_cleaning', 'restricted_hours', 'school_zone')),
    weekday smallint NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    hour smallint NOT NULL CHECK (hour BETWEEN 0 AND 23),
    dates_seen integer NOT NULL CHECK (dates_seen > 0),
    last_seen date NOT NULL,
    active_months smallint[] NOT NULL CHECK (array_length(active_months, 1) BETWEEN 1 AND 12),
    PRIMARY KEY (run_id, segment_id, kind, weekday, hour)
);

-- migrate:down
DROP TABLE restriction_warnings;