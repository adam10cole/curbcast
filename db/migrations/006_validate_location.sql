-- migrate:up
ALTER TABLE violations VALIDATE CONSTRAINT violations_location_src_match;

-- migrate:down