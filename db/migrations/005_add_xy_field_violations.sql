-- migrate:up
ALTER TABLE violations
    ADD COLUMN x double precision,
    ADD COLUMN y double precision, 
    ADD COLUMN location_source text CHECK (location_source IN ('xy', 'latlong'));

ALTER TABLE violations
    ADD CONSTRAINT violations_location_src_match
    CHECK (is_geocoded = (location_source IS NOT NULL)) NOT VALID;

-- migrate:down
    ALTER TABLE violations DROP CONSTRAINT violations_location_src_match;
    ALTER TABLE violations DROP COLUMN location_source, DROP COLUMN y, DROP COLUMN x;