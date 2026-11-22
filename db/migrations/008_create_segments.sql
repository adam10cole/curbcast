-- migrate:up
CREATE TABLE segments (
    id   bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    x    double precision NOT NULL,
    y    double precision NOT NULL,
    geom geometry(Point, 26985) GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(x, y), 26985)) STORED,
    UNIQUE (x, y)
);

CREATE INDEX segments_geom_idx ON segments USING gist (geom);

-- migrate:down
DROP TABLE segments;