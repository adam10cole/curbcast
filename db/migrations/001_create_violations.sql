-- migrate:up
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE violations (
    ticket_number text NOT NULL,
    issued_at_utc timestamptz NOT NULL,
    issued_local_hour smallint NOT NULL CHECK (issued_local_hour BETWEEN 0 AND 23),
    issued_local_weekday smallint NOT NULL CHECK (issued_local_weekday BETWEEN 0 AND 6),
    violation_code text NOT NULL,
    violation_desc text,
    agency_code text NOT NULL,
    fine_amount numeric(10,2) CHECK (fine_amount >= 0),
    latitude double precision,
    longitude double precision,
    is_geocoded boolean NOT NULL,
    source_month text NOT NULL,
    source_objectid bigint NOT NULL,
    loaded_at timestamptz NOT NULL DEFAULT now(),
    geom geometry(Point, 4326) GENERATED ALWAYS AS (
        CASE WHEN is_geocoded THEN ST_SetSRID(ST_MakePoint(longitude, latitude), 4326) END
    ) STORED,

    PRIMARY KEY (source_month, ticket_number),
    CHECK (is_geocoded = (latitude IS NOT NULL AND longitude IS NOT NULL))
);

CREATE INDEX violations_geom_idx ON violations USING gist (geom);
CREATE INDEX violations_issued_idx ON violations (issued_at_utc);

-- migrate:down
DROP TABLE violations;