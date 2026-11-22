-- migrate:up
CREATE TABLE model_runs (
    id             bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fitted_at      timestamptz      NOT NULL DEFAULT now(),
    train_start    date             NOT NULL,
    cutoff_date    date             NOT NULL,
    params         jsonb            NOT NULL,
    holiday_factor double precision NOT NULL CHECK (holiday_factor > 0 AND holiday_factor < 1),
    n_segments     integer          NOT NULL CHECK (n_segments > 0),
    evaluation     jsonb,
    is_active      boolean          NOT NULL DEFAULT false,
    CHECK (train_start <= cutoff_date)
);

CREATE UNIQUE INDEX model_runs_one_active ON model_runs (is_active) WHERE is_active;

-- migrate:down
DROP TABLE model_runs;