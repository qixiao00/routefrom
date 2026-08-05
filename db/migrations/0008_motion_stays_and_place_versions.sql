BEGIN;

CREATE TABLE app.motion_episodes (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  motion_state text NOT NULL CHECK (motion_state IN ('stationary', 'moving', 'uncertain')),
  observed_range tstzrange NOT NULL,
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  start_boundary_confidence numeric(9, 8) NOT NULL
    CHECK (start_boundary_confidence BETWEEN 0 AND 1),
  end_boundary_confidence numeric(9, 8) NOT NULL
    CHECK (end_boundary_confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (processing_run_id, sequence_number),
  UNIQUE (id, processing_run_id),
  CHECK (NOT isempty(observed_range)),
  CHECK (lower_inc(observed_range) AND NOT upper_inc(observed_range))
);

CREATE TABLE app.motion_episode_points (
  processing_run_id uuid NOT NULL,
  motion_episode_id uuid NOT NULL,
  point_id bigint NOT NULL,
  sequence_number integer NOT NULL CHECK (sequence_number >= 0),
  stationary_probability numeric(9, 8) NOT NULL
    CHECK (stationary_probability BETWEEN 0 AND 1),
  moving_probability numeric(9, 8) NOT NULL
    CHECK (moving_probability BETWEEN 0 AND 1),
  uncertain_probability numeric(9, 8) NOT NULL
    CHECK (uncertain_probability BETWEEN 0 AND 1),
  PRIMARY KEY (processing_run_id, point_id),
  FOREIGN KEY (motion_episode_id, processing_run_id)
    REFERENCES app.motion_episodes(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (processing_run_id, point_id)
    REFERENCES app.point_assessments(processing_run_id, point_id) ON DELETE CASCADE,
  UNIQUE (motion_episode_id, sequence_number),
  CHECK (
    abs(stationary_probability + moving_probability + uncertain_probability - 1) < 0.000001
  )
);

CREATE TABLE app.stationary_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stage_run_id uuid NOT NULL,
  motion_episode_id uuid NOT NULL,
  observed_range tstzrange NOT NULL,
  possible_range tstzrange NOT NULL,
  centroid geometry(Point, 4326) NOT NULL,
  spatial_extent geometry(MultiPolygon, 4326),
  spatial_radius_meters double precision NOT NULL CHECK (spatial_radius_meters >= 0),
  adaptive_spatial_scale_meters double precision NOT NULL
    CHECK (adaptive_spatial_scale_meters > 0),
  point_count integer NOT NULL CHECK (point_count > 0),
  effective_point_count double precision NOT NULL CHECK (effective_point_count > 0),
  event_type text NOT NULL
    CHECK (event_type IN ('visit', 'transport_pause', 'uncertain_stop')),
  open_start boolean NOT NULL DEFAULT false,
  open_end boolean NOT NULL DEFAULT false,
  confidence numeric(9, 8) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  visit_probability numeric(9, 8) NOT NULL CHECK (visit_probability BETWEEN 0 AND 1),
  transport_pause_probability numeric(9, 8) NOT NULL
    CHECK (transport_pause_probability BETWEEN 0 AND 1),
  uncertain_stop_probability numeric(9, 8) NOT NULL
    CHECK (uncertain_stop_probability BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (motion_episode_id, processing_run_id)
    REFERENCES app.motion_episodes(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (stage_run_id, processing_run_id)
    REFERENCES app.processing_stage_runs(id, processing_run_id) ON DELETE CASCADE,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (id, processing_run_id),
  CHECK (NOT isempty(observed_range) AND NOT isempty(possible_range)),
  CHECK (possible_range @> observed_range),
  CHECK (lower_inc(observed_range) AND NOT upper_inc(observed_range)),
  CHECK (lower_inc(possible_range) AND NOT upper_inc(possible_range)),
  CHECK (
    abs(visit_probability + transport_pause_probability + uncertain_stop_probability - 1)
    < 0.000001
  ),
  CHECK (GeometryType(centroid) = 'POINT' AND ST_SRID(centroid) = 4326),
  CHECK (
    spatial_extent IS NULL
    OR (GeometryType(spatial_extent) = 'MULTIPOLYGON' AND ST_SRID(spatial_extent) = 4326)
  )
);

ALTER TABLE app.places
  ADD COLUMN retired_at timestamptz,
  ADD COLUMN manually_created boolean NOT NULL DEFAULT false;

CREATE TABLE app.place_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  place_id uuid NOT NULL REFERENCES app.places(id) ON DELETE CASCADE,
  processing_run_id uuid NOT NULL REFERENCES app.processing_runs(id) ON DELETE CASCADE,
  parent_place_version_id uuid,
  centroid geometry(Point, 4326) NOT NULL,
  boundary geometry(MultiPolygon, 4326),
  name text,
  category text,
  address text,
  city text,
  region text,
  country_code text,
  timezone text,
  source_kind text NOT NULL DEFAULT 'trajectory_cluster'
    CHECK (source_kind IN ('manual', 'trajectory_cluster', 'map_feature', 'imported')),
  map_snapshot_id uuid,
  confidence numeric(9, 8) CHECK (confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (place_id, processing_run_id),
  UNIQUE (id, processing_run_id),
  FOREIGN KEY (parent_place_version_id, processing_run_id)
    REFERENCES app.place_versions(id, processing_run_id) ON DELETE RESTRICT,
  CHECK (GeometryType(centroid) = 'POINT' AND ST_SRID(centroid) = 4326),
  CHECK (
    boundary IS NULL
    OR (GeometryType(boundary) = 'MULTIPOLYGON' AND ST_SRID(boundary) = 4326)
  )
);

DO $$
BEGIN
  IF to_regclass('app.visits') IS NOT NULL
     AND to_regclass('app.legacy_visits') IS NULL THEN
    ALTER TABLE app.visits RENAME TO legacy_visits;
  END IF;
END
$$;

CREATE TABLE app.visits (
  id uuid CONSTRAINT semantic_visits_pkey PRIMARY KEY DEFAULT gen_random_uuid(),
  logical_id uuid NOT NULL DEFAULT gen_random_uuid(),
  processing_run_id uuid NOT NULL,
  dataset_id uuid NOT NULL,
  stationary_event_id uuid NOT NULL,
  place_version_id uuid,
  status text NOT NULL DEFAULT 'candidate'
    CHECK (status IN ('candidate', 'confirmed', 'rejected')),
  arrival_confidence numeric(9, 8) NOT NULL CHECK (arrival_confidence BETWEEN 0 AND 1),
  departure_confidence numeric(9, 8) NOT NULL CHECK (departure_confidence BETWEEN 0 AND 1),
  overall_confidence numeric(9, 8) NOT NULL CHECK (overall_confidence BETWEEN 0 AND 1),
  evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  FOREIGN KEY (processing_run_id, dataset_id)
    REFERENCES app.processing_runs(id, dataset_id) ON DELETE CASCADE,
  FOREIGN KEY (stationary_event_id, processing_run_id)
    REFERENCES app.stationary_events(id, processing_run_id) ON DELETE CASCADE,
  FOREIGN KEY (place_version_id, processing_run_id)
    REFERENCES app.place_versions(id, processing_run_id) ON DELETE RESTRICT,
  UNIQUE (processing_run_id, logical_id),
  UNIQUE (processing_run_id, stationary_event_id),
  UNIQUE (id, processing_run_id)
);

CREATE INDEX motion_episodes_run_range_gix
  ON app.motion_episodes USING gist (processing_run_id, observed_range);

CREATE INDEX stationary_events_run_observed_gix
  ON app.stationary_events USING gist (processing_run_id, observed_range);

CREATE INDEX stationary_events_centroid_gix
  ON app.stationary_events USING gist (centroid);

CREATE INDEX place_versions_centroid_gix
  ON app.place_versions USING gist (centroid);

CREATE INDEX semantic_visits_run_status_idx
  ON app.visits (processing_run_id, status, stationary_event_id);

COMMIT;
