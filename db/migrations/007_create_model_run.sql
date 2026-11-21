-- migrate:up
CREATE TABLE model_runs(
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fitted_at timestamptz NOT NULL DEFAULT now(),
    cutoff_date date NOT NULL,
    params jsonb NOT NULL,
    holiday_factor double precision NOT NULL,
    is_active boolean NOT NULL DEFAULT false 
);

-- migrate:down
DROP TABLE model_runs;