\restrict dbmate

-- Dumped from database version 16.4 (Debian 16.4-1.pgdg110+2)
-- Dumped by pg_dump version 18.7

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: tiger; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tiger;


--
-- Name: tiger_data; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA tiger_data;


--
-- Name: topology; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA topology;


--
-- Name: SCHEMA topology; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA topology IS 'PostGIS Topology schema';


--
-- Name: fuzzystrmatch; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS fuzzystrmatch WITH SCHEMA public;


--
-- Name: EXTENSION fuzzystrmatch; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION fuzzystrmatch IS 'determine similarities and distance between strings';


--
-- Name: postgis; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA public;


--
-- Name: EXTENSION postgis; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis IS 'PostGIS geometry and geography spatial types and functions';


--
-- Name: postgis_tiger_geocoder; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis_tiger_geocoder WITH SCHEMA tiger;


--
-- Name: EXTENSION postgis_tiger_geocoder; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis_tiger_geocoder IS 'PostGIS tiger geocoder and reverse geocoder';


--
-- Name: postgis_topology; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS postgis_topology WITH SCHEMA topology;


--
-- Name: EXTENSION postgis_topology; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION postgis_topology IS 'PostGIS topology spatial types and functions';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: ingestion_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.ingestion_runs (
    id bigint NOT NULL,
    source_month text NOT NULL,
    stage text NOT NULL,
    status text NOT NULL,
    started_at timestamp with time zone DEFAULT now() NOT NULL,
    finished_at timestamp with time zone,
    rows_expected integer,
    rows_clean integer,
    rows_rejected integer,
    error text,
    CONSTRAINT ingestion_runs_stage_check CHECK ((stage = ANY (ARRAY['extract'::text, 'normalize'::text, 'load'::text]))),
    CONSTRAINT ingestion_runs_status_check CHECK ((status = ANY (ARRAY['running'::text, 'succeeded'::text, 'failed'::text])))
);


--
-- Name: ingestion_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.ingestion_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.ingestion_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: model_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.model_runs (
    id bigint NOT NULL,
    fitted_at timestamp with time zone DEFAULT now() NOT NULL,
    train_start date NOT NULL,
    cutoff_date date NOT NULL,
    params jsonb NOT NULL,
    holiday_factor double precision NOT NULL,
    n_segments integer NOT NULL,
    evaluation jsonb,
    is_active boolean DEFAULT false NOT NULL,
    CONSTRAINT model_runs_check CHECK ((train_start <= cutoff_date)),
    CONSTRAINT model_runs_holiday_factor_check CHECK (((holiday_factor > (0)::double precision) AND (holiday_factor < (1)::double precision))),
    CONSTRAINT model_runs_n_segments_check CHECK ((n_segments > 0))
);


--
-- Name: model_runs_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.model_runs ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.model_runs_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: predictions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.predictions (
    run_id bigint NOT NULL,
    segment_id bigint NOT NULL,
    p double precision[] NOT NULL,
    CONSTRAINT predictions_p_check CHECK (((array_length(p, 1) = 168) AND ((0)::double precision < ALL (p)) AND ((1)::double precision > ALL (p))))
);


--
-- Name: restriction_warnings; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.restriction_warnings (
    run_id bigint NOT NULL,
    segment_id bigint NOT NULL,
    kind text NOT NULL,
    weekday smallint NOT NULL,
    hour smallint NOT NULL,
    dates_seen integer NOT NULL,
    last_seen date NOT NULL,
    active_months smallint[] NOT NULL,
    CONSTRAINT restriction_warnings_active_months_check CHECK (((array_length(active_months, 1) >= 1) AND (array_length(active_months, 1) <= 12))),
    CONSTRAINT restriction_warnings_dates_seen_check CHECK ((dates_seen > 0)),
    CONSTRAINT restriction_warnings_hour_check CHECK (((hour >= 0) AND (hour <= 23))),
    CONSTRAINT restriction_warnings_kind_check CHECK ((kind = ANY (ARRAY['rush_hour_am'::text, 'rush_hour_pm'::text, 'street_cleaning'::text, 'restricted_hours'::text, 'school_zone'::text]))),
    CONSTRAINT restriction_warnings_weekday_check CHECK (((weekday >= 0) AND (weekday <= 6)))
);


--
-- Name: schema_migrations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.schema_migrations (
    version character varying NOT NULL
);


--
-- Name: segments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.segments (
    id bigint NOT NULL,
    x double precision NOT NULL,
    y double precision NOT NULL,
    geom public.geometry(Point,26985) GENERATED ALWAYS AS (public.st_setsrid(public.st_makepoint(x, y), 26985)) STORED
);


--
-- Name: segments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.segments ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.segments_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: violation_rejects; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.violation_rejects (
    id bigint NOT NULL,
    source_month text NOT NULL,
    source_objectid bigint,
    reason text NOT NULL,
    raw jsonb NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL,
    field text NOT NULL
);


--
-- Name: violation_rejects_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

ALTER TABLE public.violation_rejects ALTER COLUMN id ADD GENERATED ALWAYS AS IDENTITY (
    SEQUENCE NAME public.violation_rejects_id_seq
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1
);


--
-- Name: violations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.violations (
    ticket_number text NOT NULL,
    issued_at_utc timestamp with time zone NOT NULL,
    issued_local_hour smallint NOT NULL,
    issued_local_weekday smallint NOT NULL,
    violation_code text NOT NULL,
    violation_desc text,
    agency_code text NOT NULL,
    fine_amount numeric(10,2),
    latitude double precision,
    longitude double precision,
    is_geocoded boolean NOT NULL,
    source_month text NOT NULL,
    source_objectid bigint NOT NULL,
    loaded_at timestamp with time zone DEFAULT now() NOT NULL,
    geom public.geometry(Point,4326) GENERATED ALWAYS AS (
CASE
    WHEN is_geocoded THEN public.st_setsrid(public.st_makepoint(longitude, latitude), 4326)
    ELSE NULL::public.geometry
END) STORED,
    x double precision,
    y double precision,
    location_source text,
    CONSTRAINT violations_check CHECK ((is_geocoded = ((latitude IS NOT NULL) AND (longitude IS NOT NULL)))),
    CONSTRAINT violations_fine_amount_check CHECK ((fine_amount >= (0)::numeric)),
    CONSTRAINT violations_issued_local_hour_check CHECK (((issued_local_hour >= 0) AND (issued_local_hour <= 23))),
    CONSTRAINT violations_issued_local_weekday_check CHECK (((issued_local_weekday >= 0) AND (issued_local_weekday <= 6))),
    CONSTRAINT violations_location_source_check CHECK ((location_source = ANY (ARRAY['xy'::text, 'latlong'::text]))),
    CONSTRAINT violations_location_src_match CHECK ((is_geocoded = (location_source IS NOT NULL)))
);


--
-- Name: ingestion_runs ingestion_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.ingestion_runs
    ADD CONSTRAINT ingestion_runs_pkey PRIMARY KEY (id);


--
-- Name: model_runs model_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.model_runs
    ADD CONSTRAINT model_runs_pkey PRIMARY KEY (id);


--
-- Name: predictions predictions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_pkey PRIMARY KEY (run_id, segment_id);


--
-- Name: restriction_warnings restriction_warnings_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_warnings
    ADD CONSTRAINT restriction_warnings_pkey PRIMARY KEY (run_id, segment_id, kind, weekday, hour);


--
-- Name: schema_migrations schema_migrations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.schema_migrations
    ADD CONSTRAINT schema_migrations_pkey PRIMARY KEY (version);


--
-- Name: segments segments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_pkey PRIMARY KEY (id);


--
-- Name: segments segments_x_y_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.segments
    ADD CONSTRAINT segments_x_y_key UNIQUE (x, y);


--
-- Name: violation_rejects violation_rejects_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violation_rejects
    ADD CONSTRAINT violation_rejects_pkey PRIMARY KEY (id);


--
-- Name: violations violations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.violations
    ADD CONSTRAINT violations_pkey PRIMARY KEY (source_month, ticket_number);


--
-- Name: ingestion_runs_month_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ingestion_runs_month_idx ON public.ingestion_runs USING btree (source_month, started_at DESC);


--
-- Name: model_runs_one_active; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX model_runs_one_active ON public.model_runs USING btree (is_active) WHERE is_active;


--
-- Name: segments_geom_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX segments_geom_idx ON public.segments USING gist (geom);


--
-- Name: violation_rejects_month_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX violation_rejects_month_idx ON public.violation_rejects USING btree (source_month);


--
-- Name: violations_geom_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX violations_geom_idx ON public.violations USING gist (geom);


--
-- Name: violations_issued_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX violations_issued_idx ON public.violations USING btree (issued_at_utc);


--
-- Name: predictions predictions_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.model_runs(id) ON DELETE CASCADE;


--
-- Name: predictions predictions_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.predictions
    ADD CONSTRAINT predictions_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);


--
-- Name: restriction_warnings restriction_warnings_run_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_warnings
    ADD CONSTRAINT restriction_warnings_run_id_fkey FOREIGN KEY (run_id) REFERENCES public.model_runs(id) ON DELETE CASCADE;


--
-- Name: restriction_warnings restriction_warnings_segment_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.restriction_warnings
    ADD CONSTRAINT restriction_warnings_segment_id_fkey FOREIGN KEY (segment_id) REFERENCES public.segments(id);


--
-- PostgreSQL database dump complete
--

\unrestrict dbmate


--
-- Dbmate schema migrations
--

INSERT INTO public.schema_migrations (version) VALUES
    ('001'),
    ('002'),
    ('003'),
    ('004'),
    ('005'),
    ('006'),
    ('007'),
    ('008'),
    ('009'),
    ('010');
